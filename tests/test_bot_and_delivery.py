import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from bot_interface import QuizTelegramBot
from core.models import QuizJob


@pytest.mark.asyncio
async def test_bot_authorization():
    mock_engine = MagicMock()
    bot = QuizTelegramBot(
        bot_token="fake:token",
        engine=mock_engine,
        authorized_users=[12345, 67890],
    )

    assert bot.is_authorized(12345) is True
    assert bot.is_authorized(67890) is True
    assert bot.is_authorized(99999) is False

    # Empty authorized_users means open to all
    open_bot = QuizTelegramBot(bot_token="fake:token", engine=mock_engine, authorized_users=[])
    assert open_bot.is_authorized(99999) is True


@pytest.mark.asyncio
async def test_bot_file_delivery_on_completion(tmp_path: Path):
    mock_engine = MagicMock()
    bot = QuizTelegramBot(bot_token="fake:token", engine=mock_engine)
    bot.app = MagicMock()
    bot.app.bot.send_message = AsyncMock()
    bot.app.bot.send_document = AsyncMock()

    # Create dummy files
    out_file = tmp_path / "Himachal_GK.txt"
    out_file.write_text("TITLE: Himachal GK\n\nQ1. Test...", encoding="utf-8")

    rev_file = tmp_path / "Himachal_GK_manual_review.txt"
    rev_file.write_text("MANUAL REVIEW...", encoding="utf-8")

    job = QuizJob(
        job_id="job_delivery_test",
        user_id=101,
        chat_id=202,
        provider="quizbot",
        payload="test_payload",
        raw_input="https://t.me/QuizBot?start=test",
        title="Himachal GK",
        expected_questions=5,
        questions_done=5,
        status="completed",
        output_path=str(out_file),
        review_path=str(rev_file),
        manual_review_count=1,
    )

    await bot.on_job_completed(job)

    # Verify send_message was called with chat_id=202
    assert bot.app.bot.send_message.called
    msg_call_args = bot.app.bot.send_message.call_args[1]
    assert msg_call_args["chat_id"] == 202

    # Verify send_document was called twice: once for main output, once for manual review
    assert bot.app.bot.send_document.call_count == 2
    doc_call_args = bot.app.bot.send_document.call_args_list[0][1]
    assert doc_call_args["chat_id"] == 202
    assert doc_call_args["filename"] == "Himachal_GK.txt"

    rev_call_args = bot.app.bot.send_document.call_args_list[1][1]
    assert rev_call_args["chat_id"] == 202
    assert rev_call_args["filename"] == "Himachal_GK_manual_review.txt"


@pytest.mark.asyncio
async def test_bot_delivery_failure_handled_gracefully():
    mock_engine = MagicMock()
    bot = QuizTelegramBot(bot_token="fake:token", engine=mock_engine)
    bot.app = MagicMock()
    # Simulate network exception on send_message
    bot.app.bot.send_message = AsyncMock(side_effect=Exception("Network failure"))

    job = QuizJob(
        job_id="job_err_test",
        user_id=101,
        chat_id=202,
        provider="quizbot",
        payload="test",
        raw_input="test",
        status="failed",
        error_message="Something went wrong",
    )

    # Should not raise exception
    await bot.on_job_completed(job)
