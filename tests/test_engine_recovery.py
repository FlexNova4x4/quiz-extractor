import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telethon.errors import FloodWaitError

from core.engine import TelethonEngine
from core.models import QuizJob


class DummyPollMsg:
    def __init__(self, msg_id):
        self.id = msg_id
        self.poll = MagicMock()


@pytest.mark.asyncio
async def test_recover_recent_questions():
    engine = TelethonEngine(session_path="/tmp/test_session", api_id=123, api_hash="abc")
    engine.client = MagicMock()

    # Mock get_messages to return 3 messages (2 with polls, 1 without)
    m1 = DummyPollMsg(10)
    m2 = MagicMock(id=11, poll=None)
    m3 = DummyPollMsg(12)
    engine.client.get_messages = AsyncMock(return_value=[m3, m2, m1])

    seen_ids = {10}
    run_after_id = 5
    queue = asyncio.Queue()

    recovered = await engine.recover_recent_questions(
        bot_entity="dummy_bot",
        run_after_id=run_after_id,
        seen_poll_ids=seen_ids,
        queue=queue,
    )

    # m1 was already seen, m2 has no poll, only m3 should be recovered
    assert recovered == 1
    assert 12 in seen_ids
    assert queue.qsize() == 1
    queued_msg = await queue.get()
    assert queued_msg.id == 12


@pytest.mark.asyncio
async def test_safe_call_floodwait_recovery():
    engine = TelethonEngine(session_path="/tmp/test_session", api_id=123, api_hash="abc")

    call_count = 0

    async def flaky_api_call():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Simulate a 1-second FloodWait on first attempt
            err = FloodWaitError(request=None)
            err.seconds = 1
            raise err
        return "SUCCESS"

    status_updates = []

    def on_status(msg):
        status_updates.append(msg)

    job = QuizJob(
        job_id="test_flood",
        user_id=1,
        chat_id=2,
        provider="quizbot",
        payload="test",
        raw_input="test",
    )

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await engine.safe_call(
            flaky_api_call,
            status_callback=on_status,
            job=job,
        )
        assert res == "SUCCESS"
        assert call_count == 2
        # Verify sleep was called for 1 + 1 = 2 seconds
        mock_sleep.assert_called_with(2)
        assert any("rate limit" in s.lower() for s in status_updates)


@pytest.mark.asyncio
async def test_safe_call_floodwait_exceeds_max_raises():
    engine = TelethonEngine(session_path="/tmp/test_session", api_id=123, api_hash="abc")

    async def extreme_flood_call():
        err = FloodWaitError(request=None)
        err.seconds = 9999  # > 300s limit
        raise err

    with pytest.raises(RuntimeError, match="exceeds maximum allowed"):
        await engine.safe_call(extreme_flood_call)
