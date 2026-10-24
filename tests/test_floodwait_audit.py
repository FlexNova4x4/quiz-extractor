import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telethon.errors import FloodWaitError
from telegram.error import RetryAfter

from bot_interface import QuizTelegramBot
from core.config import get_session_path, PROJECT_DIR
from core.engine import TelethonEngine
from core.models import QuizJob
from core.queue import JobQueueManager


@pytest.mark.asyncio
async def test_safe_get_messages_floodwait_retry():
    engine = TelethonEngine(session_path=Path("/tmp/dummy_sess"), api_id=123, api_hash="abc")
    engine.client = MagicMock()

    call_count = 0

    async def mock_get_messages(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            err = FloodWaitError(request=None)
            err.seconds = 2
            raise err
        return ["msg1", "msg2"]

    engine.client.get_messages = mock_get_messages

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        msgs = await engine.safe_get_messages("test_bot", limit=5)
        assert msgs == ["msg1", "msg2"]
        assert call_count == 2
        mock_sleep.assert_called_with(3.0)  # 2 + 1.0


@pytest.mark.asyncio
async def test_safe_get_entity_floodwait_retry():
    engine = TelethonEngine(session_path=Path("/tmp/dummy_sess"), api_id=123, api_hash="abc")
    engine.client = MagicMock()

    call_count = 0

    async def mock_get_entity(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            err = FloodWaitError(request=None)
            err.seconds = 1
            raise err
        return MagicMock(id=999, username="target_bot")

    engine.client.get_entity = mock_get_entity

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        entity = await engine.safe_get_entity("@target_bot")
        assert entity.id == 999
        assert call_count == 2
        mock_sleep.assert_called_with(2.0)  # 1 + 1.0


@pytest.mark.asyncio
async def test_quiz_start_floodwait_with_duplicate_protection():
    """
    If StartBotRequest encounters FloodWait, it pauses, then checks check_success.
    If check_success confirms start took effect, it must NOT retry StartBotRequest.
    """
    engine = TelethonEngine(session_path=Path("/tmp/dummy_sess"), api_id=123, api_hash="abc")
    engine.client = MagicMock()

    start_call_count = 0

    async def mock_start(*args, **kwargs):
        nonlocal start_call_count
        start_call_count += 1
        err = FloodWaitError(request=None)
        err.seconds = 1
        raise err

    # Simulate check_success: confirms bot responded with a ready card during the wait
    check_called = False

    async def mock_check_started():
        nonlocal check_called
        check_called = True
        return True  # Verified start already succeeded on server!

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await engine.safe_call(
            mock_start,
            check_success=mock_check_started,
        )
        assert result is True
        # Crucial: Start request must only be called ONCE, not retried!
        assert start_call_count == 1
        assert check_called is True
        mock_sleep.assert_called_with(2.0)


@pytest.mark.asyncio
async def test_safe_click_button_floodwait_with_duplicate_protection():
    """
    If clicking an inline button encounters FloodWait, it pauses, then checks check_success.
    If already succeeded, it avoids clicking again.
    """
    engine = TelethonEngine(session_path=Path("/tmp/dummy_sess"), api_id=123, api_hash="abc")
    mock_msg = MagicMock()

    click_count = 0

    async def mock_click(*args, **kwargs):
        nonlocal click_count
        click_count += 1
        err = FloodWaitError(request=None)
        err.seconds = 1
        raise err

    mock_msg.click = mock_click

    check_step_done = False

    async def mock_check_success():
        nonlocal check_step_done
        check_step_done = True
        return True

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        ok = await engine.safe_click_button(
            mock_msg, row=0, col=0, check_success=mock_check_success
        )
        assert ok is True
        assert click_count == 1
        assert check_step_done is True
        mock_sleep.assert_called_with(2.0)


@pytest.mark.asyncio
async def test_safe_vote_option_floodwait_with_voted_check():
    """
    If voting on a poll hits FloodWait, verifies it checks if vote is registered
    before re-submitting.
    """
    engine = TelethonEngine(session_path=Path("/tmp/dummy_sess"), api_id=123, api_hash="abc")
    engine.client = MagicMock()

    mock_poll_msg = MagicMock()
    vote_count = 0

    async def mock_vote(*args, **kwargs):
        nonlocal vote_count
        vote_count += 1
        err = FloodWaitError(request=None)
        err.seconds = 1
        raise err

    mock_poll_msg.click = mock_vote
    mock_poll_msg.id = 555

    # Mock client.get_messages to return poll where vote was registered
    chosen_result = MagicMock(chosen=True)
    poll_results = MagicMock(results=[chosen_result])
    poll_obj = MagicMock(results=poll_results)
    refreshed_msg = MagicMock(poll=poll_obj)

    engine.client.get_messages = AsyncMock(return_value=refreshed_msg)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await engine.safe_vote_option(
            mock_poll_msg, option_idx=0, bot_entity="bot_entity"
        )
        assert res is True
        assert vote_count == 1  # Did not repeat vote!
        mock_sleep.assert_called_with(2.0)


@pytest.mark.asyncio
async def test_floodwait_exceeds_max_threshold_marks_job_failed():
    engine = TelethonEngine(session_path=Path("/tmp/dummy_sess"), api_id=123, api_hash="abc")

    job = QuizJob(
        job_id="extreme_wait_job",
        user_id=1,
        chat_id=2,
        provider="quizbot",
        payload="p1",
        raw_input="link1",
    )

    async def extreme_flood():
        err = FloodWaitError(request=None)
        err.seconds = 500  # Exceeds default MAX_FLOOD_WAIT_SECONDS = 300
        raise err

    with pytest.raises(RuntimeError, match="exceeds maximum allowed threshold"):
        await engine.safe_call(extreme_flood, job=job)

    assert job.status == "failed"
    assert "exceeds maximum allowed threshold" in job.error_message


@pytest.mark.asyncio
async def test_bounded_retry_behavior_on_persistent_floodwait():
    engine = TelethonEngine(session_path=Path("/tmp/dummy_sess"), api_id=123, api_hash="abc")

    call_count = 0

    async def persistent_flood():
        nonlocal call_count
        call_count += 1
        err = FloodWaitError(request=None)
        err.seconds = 1
        raise err

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="Exceeded maximum FloodWait retries"):
            await engine.safe_call(persistent_flood, max_retries=3)

    assert call_count == 3


@pytest.mark.asyncio
async def test_queue_preservation_during_floodwait(tmp_path: Path):
    engine = MagicMock()
    state_file = tmp_path / "queue_preservation.json"

    job1_waited = False

    async def mock_execute(job, status_callback=None, progress_callback=None):
        nonlocal job1_waited
        if job.job_id == "job_first":
            # Simulate entering FloodWait
            job.status = "waiting_flood"
            job.flood_wait_until = time.time() + 10
            if status_callback:
                await status_callback("Telegram rate limit hit. Waiting 10s...")
            job1_waited = True
            job.status = "completed"
            job.output_path = str(tmp_path / "out1.txt")
        else:
            job.status = "completed"
            job.output_path = str(tmp_path / "out2.txt")
        return job

    engine.execute_job = mock_execute

    mgr = JobQueueManager(engine=engine, state_file=state_file, inter_job_delay=0.01)
    mgr.start_worker()

    job1, _, _ = mgr.add_job(
        user_id=1, chat_id=1, provider="quizbot", payload="p1", title="Q1"
    )
    job1.job_id = "job_first"
    mgr._jobs["job_first"] = job1

    job2, _, _ = mgr.add_job(
        user_id=2, chat_id=2, provider="quizbot", payload="p2", title="Q2"
    )

    await asyncio.sleep(0.3)
    await mgr.stop_worker()

    assert job1_waited is True
    assert job1.status == "completed"
    assert job2.status == "completed"


def test_session_path_configuration(tmp_path: Path):
    # 1. Custom TG_SESSION_PATH without .session
    with patch.dict(os.environ, {"TG_SESSION_PATH": str(tmp_path / "my_custom_session")}):
        p = get_session_path()
        assert str(p) == str(tmp_path / "my_custom_session")

    # 2. Custom TG_SESSION_PATH with .session suffix -> stripped for Telethon
    with patch.dict(os.environ, {"TG_SESSION_PATH": str(tmp_path / "my_custom_session.session")}):
        p = get_session_path()
        assert str(p) == str(tmp_path / "my_custom_session")


@pytest.mark.asyncio
async def test_unauthorized_session_raises_clear_error():
    engine = TelethonEngine(session_path=Path("/tmp/unauth_sess"), api_id=123, api_hash="abc")
    engine.client = MagicMock()
    engine.client.is_connected = MagicMock(return_value=True)
    engine.client.is_user_authorized = AsyncMock(return_value=False)

    with pytest.raises(RuntimeError, match="Telethon session is not authorized"):
        await engine.initialize()


@pytest.mark.asyncio
async def test_bot_api_retry_after_handling():
    bot = QuizTelegramBot(bot_token="dummy:token", engine=MagicMock())

    call_count = 0

    async def mock_bot_send(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            err = RetryAfter(retry_after=2)
            raise err
        return "DELIVERED"

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await bot.safe_bot_call(mock_bot_send)
        assert res == "DELIVERED"
        assert call_count == 2
        mock_sleep.assert_called_with(3.0)  # 2.0 + 1.0
