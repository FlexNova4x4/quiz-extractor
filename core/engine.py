import asyncio
import inspect
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Set, Tuple

from telethon import TelegramClient, events, functions
from telethon.errors import FloodWaitError

from core.config import (
    ADVANCE_BOT_USERNAME,
    ADVANCE_DEFAULT_EXPLANATION,
    ADVANCE_DEFAULT_MARK,
    ADVANCE_DEFAULT_MODE,
    ADVANCE_DEFAULT_NEGATIVE,
    ADVANCE_DEFAULT_SHUFFLE,
    ADVANCE_DEFAULT_TIMER,
    MAX_FLOOD_WAIT_SECONDS,
    MAX_IDLE_TIMEOUTS,
    PACE_BUTTON_CLICK_SECONDS,
    PACE_STOP_SECONDS,
    QUESTION_TIMEOUT_SECONDS,
    QUESTION_WAIT_SECONDS,
    QUIZBOT_USERNAME,
    RECOVERY_LIMIT,
    get_export_dir,
)
from core.formatter import QuizFileWriter
from core.models import ExtractedQuestion, QuizJob
from core.parser import clean_str, parse_poll_message

logger = logging.getLogger("QuizExtractorEngine")


def normalize_text(text: str) -> str:
    return (
        clean_str(text)
        .lower()
        .replace("!", "")
        .replace("?", "")
        .replace(".", "")
        .replace("’", "'")
        .replace("\u00a0", " ")
        .strip()
    )


def get_message_buttons(message: Any) -> List[Tuple[int, int, str, Any]]:
    """Return (row, col, text, button_obj) for inline/reply buttons."""
    markup = getattr(message, "reply_markup", None)
    if not markup:
        return []

    out = []
    for r, row in enumerate(getattr(markup, "rows", []) or []):
        for c, button in enumerate(getattr(row, "buttons", []) or []):
            out.append((r, c, clean_str(getattr(button, "text", "")), button))
    return out


class TelethonEngine:
    """
    Main extraction engine utilizing Telethon MTProto client.
    Guarantees serial, isolated job execution on the user session with
    consistent FloodWait protection across all RPC operations.
    """

    def __init__(self, session_path: Path, api_id: int, api_hash: str):
        self.session_path = str(session_path)
        self.api_id = api_id
        self.api_hash = api_hash
        self.client: Optional[TelegramClient] = None
        self._lock = asyncio.Lock()  # Strict serial lock for Telethon actions

    async def initialize(self):
        """Starts the Telethon client if not already connected and validates authorization."""
        if self.client is None:
            self.client = TelegramClient(self.session_path, self.api_id, self.api_hash)
        if not self.client.is_connected():
            await self.safe_call(self.client.connect)

        is_auth = await self.safe_call(self.client.is_user_authorized)
        if not is_auth:
            raise RuntimeError(
                f"Telethon session is not authorized at '{self.session_path}'. "
                "Please run 'python bot3.py' interactively in terminal once to authenticate your Telegram account."
            )

    async def disconnect(self):
        if self.client and self.client.is_connected():
            await self.client.disconnect()

    async def safe_call(
        self,
        func: Callable,
        *args,
        status_callback: Optional[Callable[[str], Any]] = None,
        job: Optional[QuizJob] = None,
        check_success: Optional[Callable[[], Any]] = None,
        max_retries: int = 5,
        **kwargs,
    ) -> Any:
        """
        Executes any Telethon API/RPC call with consistent FloodWait handling.
        Reads FloodWaitError.seconds, pauses required time, and checks if
        non-idempotent operations already took effect before retrying.
        """
        retries = 0
        while retries < max_retries:
            try:
                return await func(*args, **kwargs)
            except FloodWaitError as exc:
                wait_time = int(exc.seconds)
                logger.warning(f"Telegram FloodWaitError caught: {wait_time}s required.")

                if wait_time > MAX_FLOOD_WAIT_SECONDS:
                    err_msg = (
                        f"Telegram FloodWait duration ({wait_time}s) exceeds "
                        f"maximum allowed threshold ({MAX_FLOOD_WAIT_SECONDS}s)."
                    )
                    if job:
                        job.status = "failed"
                        job.error_message = err_msg
                    raise RuntimeError(err_msg)

                if job:
                    job.status = "waiting_flood"
                    job.flood_wait_until = time.time() + wait_time

                if status_callback:
                    try:
                        res = status_callback(
                            f"Telegram rate limit hit. Waiting {wait_time}s before resuming..."
                        )
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass

                await asyncio.sleep(wait_time + 1.0)

                if job:
                    job.status = "running"
                    job.flood_wait_until = None

                # Before repeating non-idempotent operations, verify if it already succeeded
                if check_success:
                    try:
                        already_ok = check_success()
                        if inspect.isawaitable(already_ok):
                            already_ok = await already_ok
                        if already_ok:
                            logger.info("Action verified as successful during wait; skipping redundant retry.")
                            return already_ok if already_ok is not True else True
                    except Exception as chk_err:
                        logger.debug(f"check_success check error: {chk_err}")

                retries += 1
            except Exception as e:
                # If error is other than FloodWaitError, raise immediately
                raise e

        raise RuntimeError("Exceeded maximum FloodWait retries.")

    async def safe_get_messages(
        self,
        entity: Any,
        limit: Optional[int] = None,
        ids: Optional[Any] = None,
        job: Optional[QuizJob] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
        **kwargs,
    ) -> Any:
        """FloodWait-safe wrapper for client.get_messages()."""
        return await self.safe_call(
            self.client.get_messages,
            entity,
            limit=limit,
            ids=ids,
            job=job,
            status_callback=status_callback,
            **kwargs,
        )

    async def safe_get_entity(
        self,
        entity_id: Any,
        job: Optional[QuizJob] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
    ) -> Any:
        """FloodWait-safe wrapper for client.get_entity()."""
        return await self.safe_call(
            self.client.get_entity,
            entity_id,
            job=job,
            status_callback=status_callback,
        )

    async def safe_click_button(
        self,
        message: Any,
        row: int,
        col: int,
        job: Optional[QuizJob] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
        check_success: Optional[Callable[[], Any]] = None,
    ) -> bool:
        """
        FloodWait-safe button clicker.
        Attempts direct click, then MessageButton wrapper click.
        Avoids redundant duplicate button clicks if check_success confirms completion.
        """
        async def _do_click():
            try:
                await message.click(row, col)
                return True
            except FloodWaitError:
                raise
            except Exception:
                pass

            try:
                buttons = getattr(message, "buttons", None)
                if buttons and len(buttons) > row and len(buttons[row]) > col:
                    wrapped = buttons[row][col]
                    await wrapped.click(open_url=False)
                    return True
            except FloodWaitError:
                raise
            except Exception:
                pass
            return False

        return bool(
            await self.safe_call(
                _do_click,
                job=job,
                status_callback=status_callback,
                check_success=check_success,
            )
        )

    async def safe_vote_option(
        self,
        message: Any,
        option_idx: int = 0,
        bot_entity: Optional[Any] = None,
        job: Optional[QuizJob] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
    ) -> bool:
        """
        Submits answer option click on a quiz poll in a FloodWait-safe manner.
        Checks if the vote was already registered on Telegram before repeating.
        """
        async def _check_already_voted():
            if not bot_entity:
                return False
            try:
                refreshed = await self.safe_get_messages(
                    bot_entity, ids=message.id, job=job, status_callback=status_callback
                )
                if refreshed and getattr(refreshed, "poll", None):
                    results = getattr(refreshed.poll, "results", None)
                    if results and getattr(results, "results", None):
                        for r in results.results:
                            if getattr(r, "chosen", False):
                                return True
            except Exception:
                pass
            return False

        async def _do_vote():
            try:
                await message.click(option_idx)
                return True
            except FloodWaitError:
                raise
            except Exception as exc:
                logger.debug(f"Vote submission note: {exc}")
                return False

        return bool(
            await self.safe_call(
                _do_vote,
                job=job,
                status_callback=status_callback,
                check_success=_check_already_voted,
            )
        )

    async def recover_recent_questions(
        self,
        bot_entity: Any,
        run_after_id: int,
        seen_poll_ids: Set[int],
        queue: asyncio.Queue,
        limit: int = 15,
        job: Optional[QuizJob] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
    ) -> int:
        """
        Recover poll messages from chat history if Telegram event delivery was delayed.
        Paced and bounded to avoid excessive history scans.
        """
        recovered = 0
        try:
            recent = await self.safe_get_messages(
                bot_entity,
                limit=limit,
                job=job,
                status_callback=status_callback,
            )
        except Exception as exc:
            logger.warning(f"Recent-question scan failed: {exc}")
            return 0

        # get_messages returns newest-first; queue old-to-new to preserve quiz order
        for message in reversed(recent or []):
            if message.id <= run_after_id:
                continue
            if message.id in seen_poll_ids:
                continue
            if not getattr(message, "poll", None):
                continue

            seen_poll_ids.add(message.id)
            await queue.put(message)
            recovered += 1

        if recovered:
            logger.info(f"Recovered {recovered} delayed question(s) from chat history.")
        return recovered

    async def stop_official_quizbot(
        self,
        bot_entity: Any,
        job: Optional[QuizJob] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
    ):
        """Sends /stop to QuizBot to clear any lingering quiz session safely."""
        logger.info("Stopping any old @QuizBot session...")
        for _ in range(3):
            try:
                await self.safe_call(
                    self.client.send_message,
                    bot_entity,
                    "/stop",
                    job=job,
                    status_callback=status_callback,
                )
            except Exception:
                pass

            for _ in range(6):
                await asyncio.sleep(0.4)
                try:
                    recent = await self.safe_get_messages(
                        bot_entity,
                        limit=6,
                        job=job,
                        status_callback=status_callback,
                    )
                    combined = "\n".join((m.raw_text or "") for m in recent).lower()
                    if (
                        "there is no quiz to stop" in combined
                        or "quiz was cancelled" in combined
                        or "quiz has been cancelled" in combined
                        or "quiz discarded" in combined
                        or "nothing to stop" in combined
                    ):
                        logger.info("Previous QuizBot session cleared.")
                        return
                    if "currently in progress" not in combined:
                        break
                except Exception:
                    pass

        await asyncio.sleep(PACE_STOP_SECONDS)

    async def click_official_ready(
        self,
        bot_entity: Any,
        title: str,
        run_after_id: int,
        job: Optional[QuizJob] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
    ) -> bool:
        """Finds and clicks the Ready or Start button for @QuizBot with FloodWait safety."""
        expected = normalize_text(title)
        expected_key = expected[:30] if expected else ""

        for _ in range(35):
            messages = await self.safe_get_messages(
                bot_entity,
                limit=15,
                job=job,
                status_callback=status_callback,
            )
            for msg in reversed(messages or []):
                if msg.id <= run_after_id:
                    continue

                text = clean_str(msg.raw_text)
                low_text = text.lower()

                # Handle "you already took this quiz" -> Try again
                if "you already took this quiz" in low_text:
                    for r, c, btext, _ in get_message_buttons(msg):
                        if normalize_text(btext) == "try again":
                            await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            )
                            await asyncio.sleep(PACE_BUTTON_CLICK_SECONDS)
                            break

                if expected_key and expected_key not in low_text:
                    continue

                if "press the button below when you are ready" not in low_text:
                    continue

                candidates = []
                for r, c, btext, _ in get_message_buttons(msg):
                    low = normalize_text(btext)
                    if "group" in low or "share" in low or "try again" in low:
                        continue

                    score = {
                        "i am ready": 100,
                        "im ready": 100,
                        "i'm ready": 100,
                        "ready": 90,
                        "start quiz": 80,
                        "start the quiz": 80,
                    }.get(low, 0)
                    if score:
                        candidates.append((score, r, c, btext))

                if candidates:
                    candidates.sort(reverse=True)
                    _, r, c, btext = candidates[0]
                    logger.info(f"Clicking Official Ready button: {btext}")

                    # Check if ready step already advanced before repeating
                    async def _check_ready_advanced():
                        recent_check = await self.safe_get_messages(
                            bot_entity, limit=5, job=job
                        )
                        for m in recent_check or []:
                            if m.id > msg.id and getattr(m, "poll", None):
                                return True
                        return False

                    ok = await self.safe_click_button(
                        msg,
                        r,
                        c,
                        job=job,
                        status_callback=status_callback,
                        check_success=_check_ready_advanced,
                    )
                    if ok:
                        await asyncio.sleep(PACE_BUTTON_CLICK_SECONDS)
                        return True

            await asyncio.sleep(0.5)

        logger.warning("Official Ready button not found.")
        return False

    async def advance_setup_and_start(
        self,
        bot_entity: Any,
        run_after_id: int,
        job: Optional[QuizJob] = None,
        status_callback: Optional[Callable[[str], Any]] = None,
    ) -> bool:
        """Automates the Advance Quiz Bot setup wizard / Quick Start with FloodWait safety."""
        clicked_message_buttons = set()

        for _ in range(40):
            recent = await self.safe_get_messages(
                bot_entity,
                limit=12,
                job=job,
                status_callback=status_callback,
            )

            # Check if poll already arrived
            for msg in reversed(recent or []):
                if msg.id <= run_after_id:
                    continue
                if getattr(msg, "poll", None):
                    return True

            clicked = False
            for msg in reversed(recent or []):
                low_text = clean_str(msg.raw_text).lower()
                buttons = get_message_buttons(msg)
                if not buttons:
                    continue

                fresh = [
                    (r, c, btext, (msg.id, r, c))
                    for r, c, btext, _ in buttons
                    if (msg.id, r, c) not in clicked_message_buttons
                ]

                # 1. Quick Start
                if "quick start" in low_text:
                    for r, c, btext, key in fresh:
                        if "quick start" in normalize_text(btext):
                            logger.info(f"Advance: Quick Start ({btext})")
                            if await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            ):
                                clicked_message_buttons.add(key)
                                clicked = True
                                break
                if clicked:
                    break

                # 2. Practice Mode
                if "play (practice)" in low_text or "play (exam)" in low_text:
                    for r, c, btext, key in fresh:
                        if normalize_text(btext) == normalize_text(ADVANCE_DEFAULT_MODE):
                            logger.info(f"Advance mode: {btext}")
                            if await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            ):
                                clicked_message_buttons.add(key)
                                clicked = True
                                break
                if clicked:
                    break

                # 3. Correct Mark
                if "correct mark per question" in low_text:
                    for r, c, btext, key in fresh:
                        if normalize_text(btext) == normalize_text(ADVANCE_DEFAULT_MARK):
                            logger.info(f"Advance Mark: {btext}")
                            if await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            ):
                                clicked_message_buttons.add(key)
                                clicked = True
                                break
                if clicked:
                    break

                # 4. Negative Marking
                if "negative marking" in low_text:
                    for r, c, btext, key in fresh:
                        if normalize_text(btext) in {
                            normalize_text(ADVANCE_DEFAULT_NEGATIVE),
                            "skip",
                        }:
                            logger.info("Advance negative: none")
                            if await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            ):
                                clicked_message_buttons.add(key)
                                clicked = True
                                break
                if clicked:
                    break

                # 5. Shuffle
                if "shuffle" in low_text:
                    for r, c, btext, key in fresh:
                        if normalize_text(btext) == normalize_text(ADVANCE_DEFAULT_SHUFFLE):
                            logger.info("Advance shuffle: none")
                            if await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            ):
                                clicked_message_buttons.add(key)
                                clicked = True
                                break
                if clicked:
                    break

                # 6. Show Explanation
                if "show explanation" in low_text:
                    for r, c, btext, key in fresh:
                        if normalize_text(btext) in {
                            normalize_text(ADVANCE_DEFAULT_EXPLANATION),
                            "no",
                        }:
                            logger.info("Advance explanation: no")
                            if await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            ):
                                clicked_message_buttons.add(key)
                                clicked = True
                                break
                if clicked:
                    break

                # 7. Timer
                if "timer per question" in low_text:
                    for r, c, btext, key in fresh:
                        if normalize_text(btext) == normalize_text(ADVANCE_DEFAULT_TIMER):
                            logger.info("Advance timer: 15s")
                            if await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            ):
                                clicked_message_buttons.add(key)
                                clicked = True
                                break
                if clicked:
                    break

                # 8. Start Quiz
                if (
                    "all set" in low_text
                    or "press start" in low_text
                    or "start quiz" in low_text
                ):
                    for r, c, btext, key in fresh:
                        if normalize_text(btext) in {
                            "start quiz",
                            "start",
                            "start the quiz",
                        }:
                            logger.info("Advance: Start Quiz button clicked")
                            if await self.safe_click_button(
                                msg, r, c, job=job, status_callback=status_callback
                            ):
                                clicked_message_buttons.add(key)
                                clicked = True
                                break
                if clicked:
                    break

            await asyncio.sleep(0.4)

        # Final check if any poll arrived
        recent_after = await self.safe_get_messages(
            bot_entity, limit=10, job=job, status_callback=status_callback
        )
        return any(getattr(m, "poll", None) for m in (recent_after or []))

    async def execute_job(
        self,
        job: QuizJob,
        status_callback: Optional[Callable[[str], Any]] = None,
        progress_callback: Optional[Callable[[int, int], Any]] = None,
    ) -> QuizJob:
        """
        Executes a single extraction job with complete isolation.
        Acquires the serial execution lock so only one live quiz session runs at a time.
        """
        async with self._lock:
            await self.initialize()

            job.status = "running"
            job.started_at = time.time()
            logger.info(f"Starting job {job.job_id} ({job.provider}: {job.payload})")

            target_bot_username = (
                QUIZBOT_USERNAME if job.provider == "quizbot" else ADVANCE_BOT_USERNAME
            )
            bot_entity = await self.safe_get_entity(
                f"@{target_bot_username}",
                job=job,
                status_callback=status_callback,
            )

            # Get current latest message id to avoid reading old messages
            latest_messages = await self.safe_get_messages(
                bot_entity,
                limit=1,
                job=job,
                status_callback=status_callback,
            )
            run_after_id = latest_messages[0].id if latest_messages else 0

            # Job-isolated queue and seen set
            question_queue: asyncio.Queue = asyncio.Queue()
            seen_poll_ids: Set[int] = set()

            # Scoped event handler for incoming polls
            @self.client.on(events.NewMessage)
            async def job_poll_listener(event):
                try:
                    if event.out:
                        return
                    sender = await event.get_sender()
                    if not sender:
                        return
                    username = (getattr(sender, "username", "") or "").lower()
                    if username != target_bot_username.lower():
                        return
                    msg = event.message
                    if msg.id <= run_after_id:
                        return
                    if not getattr(msg, "poll", None):
                        return
                    if msg.id in seen_poll_ids:
                        return

                    seen_poll_ids.add(msg.id)
                    await question_queue.put(msg)
                except Exception as e:
                    logger.debug(f"Listener error: {e}")

            export_dir = get_export_dir()
            writer = QuizFileWriter(
                export_dir=export_dir,
                title=job.title,
                fallback_id=job.payload,
            )

            try:
                # 1. Setup and Start Quiz based on Provider
                if job.provider == "quizbot":
                    await self.stop_official_quizbot(
                        bot_entity, job=job, status_callback=status_callback
                    )
                    await asyncio.sleep(0.5)

                    # Update run_after_id after /stop
                    latest = await self.safe_get_messages(
                        bot_entity, limit=1, job=job, status_callback=status_callback
                    )
                    run_after_id = latest[0].id if latest else run_after_id

                    # Check if quiz already started before sending StartBotRequest
                    async def _check_quizbot_started():
                        check_msgs = await self.safe_get_messages(
                            bot_entity, limit=5, job=job
                        )
                        for m in check_msgs or []:
                            if m.id > run_after_id:
                                return True
                        return False

                    logger.info(f"Sending StartBotRequest for payload: {job.payload}")
                    start_ok = await self.safe_call(
                        self.client,
                        functions.messages.StartBotRequest(
                            bot=bot_entity,
                            peer=bot_entity,
                            random_id=secrets.randbits(63),
                            start_param=job.payload,
                        ),
                        status_callback=status_callback,
                        job=job,
                        check_success=_check_quizbot_started,
                    )
                    if not start_ok:
                        raise RuntimeError("Failed to start @QuizBot via StartBotRequest.")

                    # Handle Ready button
                    ready_ok = await self.click_official_ready(
                        bot_entity,
                        job.title,
                        run_after_id,
                        job=job,
                        status_callback=status_callback,
                    )
                    if not ready_ok:
                        logger.warning("Ready button not clicked; proceeding to poll loop.")

                elif job.provider == "advance_quiz_bot":
                    logger.info(f"Starting Advance Quiz Bot with quiz ID: {job.payload}")

                    async def _check_advance_started():
                        check_msgs = await self.safe_get_messages(
                            bot_entity, limit=5, job=job
                        )
                        for m in check_msgs or []:
                            if m.id > run_after_id:
                                return True
                        return False

                    start_ok = await self.safe_call(
                        self.client,
                        functions.messages.StartBotRequest(
                            bot=bot_entity,
                            peer=bot_entity,
                            random_id=secrets.randbits(63),
                            start_param=job.payload,
                        ),
                        status_callback=status_callback,
                        job=job,
                        check_success=_check_advance_started,
                    )
                    if not start_ok:
                        raise RuntimeError("Failed to start Advance Quiz Bot.")

                    await asyncio.sleep(0.8)
                    await self.advance_setup_and_start(
                        bot_entity,
                        run_after_id,
                        job=job,
                        status_callback=status_callback,
                    )

                else:
                    raise ValueError(f"Unsupported provider: {job.provider}")

                # 2. Main Question Extraction Loop
                target_total = job.expected_questions or 9999
                idle_timeouts = 0
                total_extracted = 0

                while total_extracted < target_total:
                    try:
                        # Wait for question with QUESTION_WAIT_SECONDS
                        msg = await asyncio.wait_for(
                            question_queue.get(), timeout=QUESTION_WAIT_SECONDS
                        )
                        idle_timeouts = 0  # Reset idle counter on valid arrival
                    except asyncio.TimeoutError:
                        # Attempt recovery from chat history
                        logger.info(
                            f"Queue idle for {QUESTION_WAIT_SECONDS}s; scanning history for delayed polls..."
                        )
                        recovered = await self.recover_recent_questions(
                            bot_entity,
                            run_after_id,
                            seen_poll_ids,
                            question_queue,
                            limit=15,
                            job=job,
                            status_callback=status_callback,
                        )
                        if recovered > 0:
                            idle_timeouts = 0
                            continue

                        idle_timeouts += 1
                        logger.info(
                            f"Idle timeout {idle_timeouts}/{MAX_IDLE_TIMEOUTS} (no new polls received)."
                        )
                        if idle_timeouts >= MAX_IDLE_TIMEOUTS:
                            logger.info(
                                "Quiz session ended or timed out after multiple idle windows."
                            )
                            break
                        continue

                    # Process the poll message
                    extracted_q = parse_poll_message(msg, number=total_extracted + 1)
                    if not extracted_q:
                        continue

                    total_extracted += 1

                    # Click option 0 (Option A) to trigger Telegram answer reveal safely
                    await self.safe_vote_option(
                        msg,
                        option_idx=0,
                        bot_entity=bot_entity,
                        job=job,
                        status_callback=status_callback,
                    )

                    # Brief pacing before re-fetching results
                    await asyncio.sleep(0.3)

                    # Re-fetch message to read exposed correct answer
                    try:
                        updated_msgs = await self.safe_get_messages(
                            bot_entity,
                            ids=msg.id,
                            job=job,
                            status_callback=status_callback,
                        )
                        if updated_msgs:
                            fresh_q = parse_poll_message(
                                updated_msgs, number=total_extracted
                            )
                            if fresh_q and fresh_q.correct_indexes:
                                extracted_q.correct_indexes = fresh_q.correct_indexes
                            if fresh_q and fresh_q.explanation:
                                extracted_q.explanation = fresh_q.explanation
                    except Exception as e:
                        logger.debug(f"Failed to re-fetch poll results: {e}")

                    # Write to file
                    writer.add_question(extracted_q)
                    job.questions_done = writer.valid_question_count

                    if progress_callback:
                        try:
                            res = progress_callback(
                                job.questions_done, job.expected_questions
                            )
                            if asyncio.iscoroutine(res):
                                await res
                        except Exception:
                            pass

                # Finalize files
                out_path, rev_path = writer.finalize()
                job.output_path = str(out_path)
                job.review_path = str(rev_path) if rev_path else None
                job.manual_review_count = writer.manual_review_count
                job.missing_answers_count = writer.missing_answer_count

                if (
                    job.expected_questions > 0
                    and job.questions_done >= job.expected_questions
                ):
                    job.status = "completed"
                elif job.questions_done > 0:
                    job.status = "partial"
                else:
                    job.status = "failed"
                    job.error_message = (
                        "No questions were extracted. Quiz may have expired or ended."
                    )

            except Exception as e:
                logger.error(f"Error in job {job.job_id}: {e}", exc_info=True)
                job.status = "failed"
                job.error_message = str(e)
            finally:
                # Always remove the scoped event listener to prevent leaks
                try:
                    self.client.remove_event_handler(job_poll_listener)
                except Exception:
                    pass
                job.finished_at = time.time()

            return job
