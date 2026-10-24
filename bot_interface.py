import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

from telegram import Document, Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.config import (
    get_api_hash,
    get_api_id,
    get_authorized_users,
    get_bot_token,
    get_session_path,
)
from core.engine import TelethonEngine
from core.messages import Messages
from core.models import QuizJob
from core.parser import parse_quiz_input
from core.queue import JobQueueManager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("QuizExtractorBot")


class QuizTelegramBot:
    def __init__(
        self,
        bot_token: str,
        engine: TelethonEngine,
        authorized_users: Optional[list] = None,
    ):
        self.bot_token = bot_token
        self.engine = engine
        self.authorized_users = authorized_users or []

        self.queue_manager = JobQueueManager(
            engine=self.engine,
            job_completed_callback=self.on_job_completed,
            status_update_callback=self.on_status_update,
            progress_update_callback=self.on_progress_update,
        )
        self.app: Optional[Application] = None

    def is_authorized(self, user_id: int) -> bool:
        if not self.authorized_users:
            return True
        return user_id in self.authorized_users

    async def safe_bot_call(self, func: Callable, *args, **kwargs) -> Any:
        """Executes a Telegram Bot API call with RetryAfter handling."""
        for attempt in range(5):
            try:
                return await func(*args, **kwargs)
            except RetryAfter as exc:
                wait_val = getattr(exc, "retry_after", 1.0)
                wait_time = float(
                    wait_val.total_seconds()
                    if hasattr(wait_val, "total_seconds")
                    else wait_val
                )
                logger.warning(f"Bot API RetryAfter caught: waiting {wait_time}s...")
                await asyncio.sleep(wait_time + 1.0)
            except TelegramError as exc:
                logger.warning(f"Bot API error on attempt {attempt + 1}: {exc}")
                if attempt == 4:
                    raise
                await asyncio.sleep(1.0)

    async def on_status_update(self, job: QuizJob, status_text: str):
        """Notifies user of rate limits or notable status changes."""
        if not self.app or not job.chat_id:
            return
        try:
            await self.safe_bot_call(
                self.app.bot.send_message,
                chat_id=job.chat_id,
                text=status_text,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.debug(f"Failed to send status update: {e}")

    async def on_progress_update(self, job: QuizJob, done: int, total: int):
        """Logs progress and optionally updates user."""
        logger.info(f"Job {job.job_id} progress: {done}/{total}")

    async def on_job_completed(self, job: QuizJob):
        """Delivers final quiz TXT file directly and only to the requesting user/chat."""
        if not self.app or not job.chat_id:
            return

        try:
            if job.status == "completed" and job.output_path and Path(job.output_path).exists():
                out_path = Path(job.output_path)
                status_text = Messages.completed(job, out_path.name)
                await self.safe_bot_call(
                    self.app.bot.send_message,
                    chat_id=job.chat_id,
                    text=status_text,
                    parse_mode=ParseMode.MARKDOWN,
                )
                with out_path.open("rb") as f:
                    await self.safe_bot_call(
                        self.app.bot.send_document,
                        chat_id=job.chat_id,
                        document=f,
                        filename=out_path.name,
                        caption=f"📄 {out_path.name}",
                    )

                if job.review_path and Path(job.review_path).exists():
                    rev_path = Path(job.review_path)
                    with rev_path.open("rb") as f:
                        await self.safe_bot_call(
                            self.app.bot.send_document,
                            chat_id=job.chat_id,
                            document=f,
                            filename=rev_path.name,
                            caption="⚠️ Manual Review Report (non 4-option questions)",
                        )

            elif job.status == "partial" and job.output_path and Path(job.output_path).exists():
                out_path = Path(job.output_path)
                status_text = Messages.partial_completion(
                    job, out_path.name, job.error_message or "Session timed out"
                )
                await self.safe_bot_call(
                    self.app.bot.send_message,
                    chat_id=job.chat_id,
                    text=status_text,
                    parse_mode=ParseMode.MARKDOWN,
                )
                with out_path.open("rb") as f:
                    await self.safe_bot_call(
                        self.app.bot.send_document,
                        chat_id=job.chat_id,
                        document=f,
                        filename=out_path.name,
                    )

            else:
                err_text = Messages.failed(
                    job, job.error_message or "Could not extract quiz questions."
                )
                await self.safe_bot_call(
                    self.app.bot.send_message,
                    chat_id=job.chat_id,
                    text=err_text,
                    parse_mode=ParseMode.MARKDOWN,
                )

        except Exception as exc:
            logger.error(f"Failed to deliver job results for {job.job_id}: {exc}")

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not update.effective_chat:
            return
        if not self.is_authorized(update.effective_user.id):
            await self.safe_bot_call(
                update.effective_chat.send_message,
                Messages.unauthorized(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await self.safe_bot_call(
            update.effective_chat.send_message,
            Messages.start_welcome(),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat:
            return
        await self.safe_bot_call(
            update.effective_chat.send_message,
            Messages.help_guide(),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat:
            return
        active_job, pending = self.queue_manager.get_status_summary()
        status_msg = Messages.queue_status(active_job, pending)
        await self.safe_bot_call(
            update.effective_chat.send_message,
            status_msg,
            parse_mode=ParseMode.MARKDOWN,
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not update.effective_chat or not update.message:
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        if not self.is_authorized(user_id):
            await self.safe_bot_call(
                update.message.reply_text,
                Messages.unauthorized(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        text = update.message.text or update.message.caption or ""
        parsed = parse_quiz_input(text)

        if not parsed:
            await self.safe_bot_call(
                update.message.reply_text,
                Messages.invalid_input(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        provider, payload, count, title = parsed

        # Check if already completed and file exists
        completed_job = self.queue_manager.find_completed_job(provider, payload)
        if completed_job and completed_job.output_path and Path(completed_job.output_path).exists():
            out_path = Path(completed_job.output_path)
            await self.safe_bot_call(
                update.message.reply_text,
                f"ℹ️ **Yeh quiz pehle se extracted hai!**\n📄 `{out_path.name}`\nFile deliver ki ja rahi hai...",
                parse_mode=ParseMode.MARKDOWN,
            )
            with out_path.open("rb") as f:
                await self.safe_bot_call(
                    update.message.reply_document,
                    document=f,
                    filename=out_path.name,
                )
            return

        job, queue_pos, is_new = self.queue_manager.add_job(
            user_id=user_id,
            chat_id=chat_id,
            provider=provider,
            payload=payload,
            title=title,
            expected_questions=count,
            raw_input=text,
        )

        if not is_new:
            await self.safe_bot_call(
                update.message.reply_text,
                Messages.duplicate_job(job.job_id, job.status),
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await self.safe_bot_call(
                update.message.reply_text,
                Messages.job_accepted(job, queue_pos),
                parse_mode=ParseMode.MARKDOWN,
            )

    async def start(self):
        """Starts the Bot API polling and the serial worker loop."""
        self.app = Application.builder().token(self.bot_token).build()

        self.app.add_handler(CommandHandler("start", self.handle_start))
        self.app.add_handler(CommandHandler("help", self.handle_help))
        self.app.add_handler(CommandHandler("status", self.handle_status))
        self.app.add_handler(
            MessageHandler(filters.TEXT | filters.FORWARDED, self.handle_message)
        )

        await self.engine.initialize()
        self.queue_manager.start_worker()

        logger.info("Starting Telegram Bot API polling...")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)


async def main():
    bot_token = get_bot_token()
    api_id = get_api_id()
    api_hash = get_api_hash()
    session_path = get_session_path()
    auth_users = get_authorized_users()

    if not bot_token:
        raise SystemExit("Missing TG_BOT_TOKEN. Put it in ~/.quizbot.env or .env")
    if not api_id or not api_hash:
        raise SystemExit("Missing TG_API_ID or TG_API_HASH. Put them in ~/.quizbot.env or .env")

    engine = TelethonEngine(session_path=session_path, api_id=api_id, api_hash=api_hash)
    bot = QuizTelegramBot(bot_token=bot_token, engine=engine, authorized_users=auth_users)

    try:
        await bot.start()
        print("✅ Bot is online and listening. Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\nStopping bot...")
    finally:
        await bot.queue_manager.stop_worker()
        if bot.app:
            await bot.app.stop()
            await bot.app.shutdown()
        await engine.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
