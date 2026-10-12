import asyncio
import os
import re
import secrets
from pathlib import Path

from telethon import TelegramClient, events, functions

# =========================================================
# CONFIG
# =========================================================

HOME = Path.home()
PROJECT_DIR = HOME / "quiz-extractor"
SESSION = str(PROJECT_DIR / "telegram_session")

ENV_FILE = HOME / ".quizbot.env"

QUIZBOT_USERNAME = "quizbot"
ADVANCE_BOT_USERNAME = "advance_quiz_bot"

EXPORT_DIR = HOME / "storage" / "shared" / "QuizExports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# Advance Quiz Bot defaults used when its setup wizard appears.
ADVANCE_DEFAULT_MARK = "1"
ADVANCE_DEFAULT_NEGATIVE = "Skip (no negative)"
ADVANCE_DEFAULT_SHUFFLE = "No Shuffle"
ADVANCE_DEFAULT_EXPLANATION = "No (default)"
ADVANCE_DEFAULT_TIMER = "15s"
ADVANCE_DEFAULT_MODE = "Play (Practice)"

# =========================================================
# LOAD LOCAL ENV FILE
# =========================================================

if ENV_FILE.exists():
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


try:
    API_ID = int(os.environ["TG_API_ID"])
    API_HASH = os.environ["TG_API_HASH"]
except KeyError as exc:
    raise SystemExit(
        f"Missing {exc.args[0]}. Put TG_API_ID and TG_API_HASH in ~/.quizbot.env"
    )
except ValueError:
    raise SystemExit("TG_API_ID must be a numeric value.")


client = TelegramClient(SESSION, API_ID, API_HASH)

# =========================================================
# RUNTIME STATE
# =========================================================

active_mode = None                  # "official" / "advance"
active_bot_username = None          # "quizbot" / "advance_quiz_bot"
active_bot_entity = None
active_run_after_id = 0

question_queue = asyncio.Queue()
seen_poll_ids = set()
questions_done = 0
total_questions = 0
output_file = None


# =========================================================
# TEXT / BUTTON HELPERS
# =========================================================

def clean_text(value) -> str:
    if value is None:
        return ""
    text = getattr(value, "text", None)
    if text is not None:
        return str(text).strip()
    return str(value).strip()


def normalize(text: str) -> str:
    return (
        clean_text(text)
        .lower()
        .replace("!", "")
        .replace("?", "")
        .replace(".", "")
        .replace("’", "'")
        .replace("\u00a0", " ")
        .strip()
    )


def get_message_buttons(message):
    """Return (row, col, text) for inline/reply buttons."""
    markup = getattr(message, "reply_markup", None)
    if not markup:
        return []

    out = []
    for r, row in enumerate(getattr(markup, "rows", []) or []):
        for c, button in enumerate(getattr(row, "buttons", []) or []):
            out.append((r, c, clean_text(getattr(button, "text", "")), button))
    return out


async def click_button(message, row, col):
    """Try raw message click, then MessageButton wrapper click."""
    try:
        await message.click(row, col)
        return True
    except Exception:
        pass

    try:
        wrapped = message.buttons[row][col]
        await wrapped.click(open_url=False)
        return True
    except Exception:
        return False


async def click_exact_or_preferred(message, preferred, reject=()):
    """Click the highest-scoring allowed button on one message."""
    wanted = [normalize(x) for x in preferred]
    blocked = [normalize(x) for x in reject]
    candidates = []

    for r, c, text, _raw in get_message_buttons(message):
        low = normalize(text)

        if any(b and b in low for b in blocked):
            continue

        score = 0
        for item in wanted:
            if not item:
                continue
            if low == item:
                score = max(score, 100)
            elif item in low:
                score = max(score, 50)

        if score:
            candidates.append((score, r, c, text))

    if not candidates:
        return False

    candidates.sort(key=lambda x: x[0], reverse=True)
    _score, r, c, text = candidates[0]

    print(f"▶️ Button: {text}")

    ok = await click_button(message, r, c)
    if ok:
        print(f"✅ Clicked: {text}")
        await asyncio.sleep(0.5)
    else:
        print(f"❌ Could not click: {text}")
    return ok


# =========================================================
# POLL PARSER / ANSWER EXTRACTION
# =========================================================

def parse_poll(message):
    try:
        media = message.poll
        if not media:
            return None

        poll = media.poll
        question = clean_text(poll.question)
        answers = [clean_text(a.text) for a in (poll.answers or [])]

        if not question or not answers:
            return None

        return {
            "id": message.id,
            "question": question,
            "answers": answers,
        }
    except Exception:
        return None


def get_correct_indexes(message):
    """Works when Telegram exposes poll result.correct."""
    try:
        media = message.poll
        if not media or not media.results:
            return []

        poll = media.poll
        results = media.results

        option_map = {}
        for idx, answer in enumerate(poll.answers or []):
            option = getattr(answer, "option", None)
            if option is not None:
                option_map[bytes(option)] = idx

        correct = []
        for result in results.results or []:
            if not getattr(result, "correct", False):
                continue
            option = getattr(result, "option", None)
            if option is None:
                continue
            idx = option_map.get(bytes(option))
            if idx is not None:
                correct.append(idx)

        return sorted(set(correct))
    except Exception:
        return []


# =========================================================
# POLL EVENT LISTENER
# =========================================================

@client.on(events.NewMessage)
async def on_new_message(event):
    global questions_done

    try:
        if active_mode is None or active_bot_username is None:
            return

        message = event.message
        if message.id <= active_run_after_id:
            return

        sender = await event.get_sender()
        if not sender:
            return

        username = (getattr(sender, "username", "") or "").lower()
        if username != active_bot_username:
            return

        poll = parse_poll(message)
        if not poll:
            return

        if message.id in seen_poll_ids:
            return

        seen_poll_ids.add(message.id)

        print("\n" + "=" * 60)
        print("🚨 NEW QUIZ QUESTION")
        print("=" * 60)
        print(f"📚 Next: {questions_done + 1}/{total_questions}")
        print("❓", poll["question"])
        for i, option in enumerate(poll["answers"], 1):
            print(f"   {i}) {option}")
        print("=" * 60)

        await question_queue.put(message)

    except Exception as exc:
        print("⚠️ Listener error:", repr(exc))


# =========================================================
# EXPORT
# =========================================================

def save_question(number, poll, correct):
    """
    Save in direct quiz-maker TXT format:

    1. Question
    A. Option
    B. Option
    C. Option
    D. Option
    2

    The answer line is 1-based. If Telegram does not expose the
    correct answer, write 0 as a clear sentinel rather than guessing.
    """
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    with output_file.open("a", encoding="utf-8") as f:
        f.write(f"{number}. {poll['question']}\n")

        for i, option in enumerate(poll["answers"]):
            letter = letters[i] if i < len(letters) else str(i + 1)
            f.write(f"{letter}. {option}\n")

        if correct:
            # Native Telegram quiz polls normally have one correct answer.
            # Keep the first exposed correct answer as the import answer.
            answer_number = correct[0] + 1
        else:
            # Do not invent an answer. 0 is used as an explicit sentinel.
            answer_number = 0

        f.write(f"{answer_number}\n\n")

async def finish_export(label):
    if questions_done <= 0 or output_file is None:
        return

    try:
        await client.send_file(
            "me",
            str(output_file),
            caption=(
                f"✅ {label} export complete\n"
                f"📚 Questions: {questions_done}/{total_questions}\n"
                f"📄 {output_file.name}"
            ),
        )
        print("✅ TXT Saved Messages me bhej di.")
    except Exception as exc:
        print("⚠️ File send error:", repr(exc))


# =========================================================
# OFFICIAL QUIZBOT: FIND SHARED QUIZ
# =========================================================

async def find_latest_official_shared_quiz():
    print("🔎 Saved Messages me latest @QuizBot shared quiz dhoond raha hoon...")

    messages = await client.get_messages("me", limit=100)
    pattern = re.compile(r"https://t\.me/QuizBot\?start=([A-Za-z0-9_-]+)")

    for msg in messages:
        try:
            raw = msg.stringify()
        except Exception:
            raw = repr(msg)

        match = pattern.search(raw)
        if not match:
            continue

        payload = match.group(1)
        text = msg.raw_text or ""

        count_match = re.search(r"(\d+)\s+questions\b", text, re.I)
        count = int(count_match.group(1)) if count_match else 0

        title_match = re.search(r"Quiz\s+['‘](.*?)['’]", text, re.S)
        title = title_match.group(1).strip() if title_match else ""

        return payload, count, title, msg.id

    return None, 0, "", 0


# =========================================================
# OFFICIAL QUIZBOT: STOP OLD SESSION
# =========================================================

async def stop_official_quizbot(bot):
    print("🛑 Purani/paused @QuizBot session stop kar raha hoon...")

    for _ in range(3):
        try:
            await client.send_message(bot, "/stop")
        except Exception:
            pass

        for _ in range(10):
            await asyncio.sleep(0.4)
            recent = await client.get_messages(bot, limit=8)
            combined = "\n".join(
                (m.raw_text or "") for m in recent
            ).lower()

            if (
                "there is no quiz to stop" in combined
                or "quiz was cancelled" in combined
                or "quiz has been cancelled" in combined
                or "quiz discarded" in combined
                or "nothing to stop" in combined
            ):
                print("✅ Purani QuizBot session clear.")
                return

            if "currently in progress" not in combined:
                # Could already be clear. Wait a little, then proceed.
                break

    print("✅ Stop sequence complete.")


# =========================================================
# OFFICIAL QUIZBOT: START + READY
# =========================================================

async def start_official_quiz(payload):
    global active_mode, active_bot_username, active_bot_entity, active_run_after_id

    active_mode = "official"
    active_bot_username = QUIZBOT_USERNAME
    active_bot_entity = await client.get_entity("@QuizBot")

    latest = await client.get_messages(active_bot_entity, limit=1)
    active_run_after_id = latest[0].id if latest else 0

    print("🚀 QuizBot deep-link start...")

    try:
        await client(
            functions.messages.StartBotRequest(
                bot=active_bot_entity,
                peer=active_bot_entity,
                random_id=secrets.randbits(63),
                start_param=payload,
            )
        )
        print("✅ messages.startBot SUCCESS")
        return True
    except Exception as exc:
        print("❌ StartBot failed:", repr(exc))
        return False


async def click_official_ready(title):
    expected = normalize(title)
    expected_key = expected[:30]

    print("🔎 उसी official quiz का Ready message dhoond raha hoon...")

    for _ in range(40):
        messages = await client.get_messages(active_bot_entity, limit=20)

        for msg in reversed(messages):
            if msg.id <= active_run_after_id:
                continue

            text = clean_text(msg.raw_text)
            low_text = text.lower()

            if expected_key and expected_key not in low_text:
                continue

            if "press the button below when you are ready" not in low_text:
                continue

            buttons = get_message_buttons(msg)
            candidates = []

            for r, c, btext, _raw in buttons:
                low = normalize(btext)
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
                print(f"✅ सही Ready button मिला: {btext}")
                if await click_button(msg, r, c):
                    print("✅ Ready button clicked")
                    return True

        await asyncio.sleep(0.5)

    print("❌ Official quiz ready button nahi mila.")
    return False


# =========================================================
# OFFICIAL QUIZBOT: FULL RUN
# =========================================================

async def run_official():
    global total_questions, questions_done, output_file, seen_poll_ids

    payload, count, title, source_message_id = (
        await find_latest_official_shared_quiz()
    )

    if not payload:
        print("❌ Saved Messages me @QuizBot shared quiz nahi mila.")
        return

    if count <= 0:
        print("❌ Official quiz ka question count read nahi hua.")
        return

    total_questions = count
    questions_done = 0
    seen_poll_ids.clear()
    output_file = EXPORT_DIR / f"quiz_{payload}.txt"

    print(f"✅ Quiz payload मिला: {payload}")
    print(f"📁 Message ID: {source_message_id}")
    print(f"📚 Question count: {count}")
    print(f"🎯 Quiz title: {title}")
    print(f"📄 Output: {output_file}")

    bot = await client.get_entity("@QuizBot")
    await stop_official_quizbot(bot)
    await asyncio.sleep(0.8)

    if not await start_official_quiz(payload):
        return

    if not await click_official_ready(title):
        return

    # Start with a clean bot-import-compatible TXT.  No headers/separators.
    output_file.write_text("", encoding="utf-8")

    print("📚 Official QuizBot questions collect kar raha hoon...")

    while questions_done < total_questions:
        try:
            message = await asyncio.wait_for(question_queue.get(), timeout=20)
        except asyncio.TimeoutError:
            print("❌ 20 sec me next question nahi aaya.")
            print("QuizBot session ruk gayi/closed ho gayi.")
            break

        poll = parse_poll(message)
        if not poll:
            continue

        questions_done += 1
        print(f"📖 QUESTION {questions_done}/{total_questions}")

        # Submit immediately because timers can be short.
        try:
            await message.click(0)
            print("✅ Option A submitted")
        except Exception as exc:
            print("⚠️ Vote error:", repr(exc))

        await asyncio.sleep(0.3)

        correct = []
        try:
            updated = await client.get_messages(active_bot_entity, ids=message.id)
            if updated:
                correct = get_correct_indexes(updated)
        except Exception:
            pass

        save_question(questions_done, poll, correct)

        if correct:
            print("✅ Correct:", correct)
        else:
            print("⚠️ Correct answer Telegram ne expose nahi kiya → TXT me answer 0 likha gaya")

        print(f"💾 Q{questions_done} saved")

    print(f"Questions extracted: {questions_done}/{total_questions}")
    print(f"TXT: {output_file}")
    await finish_export("Official QuizBot")


# =========================================================
# ADVANCE QUIZ: FIND SHARED CARD
# =========================================================

async def find_latest_advance_shared():
    print("🔎 Saved Messages me latest @advance_quiz_bot shared quiz dhoond raha hoon...")

    messages = await client.get_messages("me", limit=100)

    for msg in messages:
        text = msg.raw_text or ""
        low = text.lower()

        if "quiz shared" not in low and "quiz id:" not in low:
            continue

        match = re.search(
            r"Quiz ID:\s*([A-Za-z0-9_-]+)",
            text,
            re.I,
        )
        if not match:
            continue

        quiz_id = match.group(1)
        count_match = re.search(r"Questions:\s*(\d+)", text, re.I)
        count = int(count_match.group(1)) if count_match else 0

        return msg, quiz_id, count, text

    return None, None, 0, ""


# =========================================================
# ADVANCE QUIZ: START SHARED CARD
# =========================================================

async def start_advance_shared_quiz(quiz_id):
    """
    Shared Advance quiz cards use a URL/deep-link Start button.
    Telethon cannot reliably click that URL button from Saved Messages.
    Start the bot directly with the quiz ID instead.
    """
    global active_bot_entity

    print("🚀 Advance quiz deep-link start...")

    try:
        await client(
            functions.messages.StartBotRequest(
                bot=active_bot_entity,
                peer=active_bot_entity,
                random_id=secrets.randbits(63),
                start_param=quiz_id,
            )
        )
        print("✅ Advance messages.startBot SUCCESS")
        return True
    except Exception as exc:
        print("❌ Advance start failed:", repr(exc))
        return False


# =========================================================
# ADVANCE QUIZ: WIZARD AUTOMATION
# =========================================================

async def advance_setup_and_start(bot):
    """Handle the optional Advance Quiz Bot setup wizard."""
    clicked_message_buttons = set()

    for _ in range(50):
        # If a NEW poll already arrived, setup is finished.
        recent = await client.get_messages(bot, limit=12)

        for msg in reversed(recent):
            if msg.id <= active_run_after_id:
                continue
            if parse_poll(msg):
                return True

        clicked = False

        for msg in reversed(recent):
            text = clean_text(msg.raw_text)
            low_text = text.lower()

            buttons = get_message_buttons(msg)
            if not buttons:
                continue

            # Avoid repeatedly clicking same message/button.
            fresh_buttons = []
            for r, c, btext, _raw in buttons:
                key = (msg.id, r, c)
                if key in clicked_message_buttons:
                    continue
                fresh_buttons.append((r, c, btext, key))

            # 1. If quick start is explicitly available, use it.
            if "quick start" in low_text:
                for r, c, btext, key in fresh_buttons:
                    if "quick start" in normalize(btext):
                        print("⚡ Advance: Quick Start")
                        if await click_button(msg, r, c):
                            clicked_message_buttons.add(key)
                            clicked = True
                            break

            if clicked:
                break

            # 2. Practice/Exam. Practice is chosen by default because it is
            # the least restrictive interactive mode for extraction.
            if "play (practice)" in low_text or "play (exam)" in low_text:
                for r, c, btext, key in fresh_buttons:
                    if normalize(btext) == normalize(ADVANCE_DEFAULT_MODE):
                        print("🎯 Advance mode:", btext)
                        if await click_button(msg, r, c):
                            clicked_message_buttons.add(key)
                            clicked = True
                            break

            if clicked:
                break

            # 3. Correct mark per question -> 1
            if "correct mark per question" in low_text:
                for r, c, btext, key in fresh_buttons:
                    if normalize(btext) == normalize(ADVANCE_DEFAULT_MARK):
                        print("🎯 Correct mark: 1")
                        if await click_button(msg, r, c):
                            clicked_message_buttons.add(key)
                            clicked = True
                            break

            if clicked:
                break

            # 4. Negative marking -> no negative
            if "negative marking" in low_text:
                for r, c, btext, key in fresh_buttons:
                    if normalize(btext) in {
                        normalize(ADVANCE_DEFAULT_NEGATIVE),
                        "skip",
                    }:
                        print("🎯 Negative marking: none")
                        if await click_button(msg, r, c):
                            clicked_message_buttons.add(key)
                            clicked = True
                            break

            if clicked:
                break

            # 5. Shuffle -> no shuffle
            if "shuffle" in low_text:
                for r, c, btext, key in fresh_buttons:
                    if normalize(btext) == normalize(ADVANCE_DEFAULT_SHUFFLE):
                        print("🎯 Shuffle: none")
                        if await click_button(msg, r, c):
                            clicked_message_buttons.add(key)
                            clicked = True
                            break

            if clicked:
                break

            # 6. Explanation -> no
            if "show explanation" in low_text:
                for r, c, btext, key in fresh_buttons:
                    n = normalize(btext)
                    if n in {
                        normalize(ADVANCE_DEFAULT_EXPLANATION),
                        "no",
                    }:
                        print("🎯 Explanation: no")
                        if await click_button(msg, r, c):
                            clicked_message_buttons.add(key)
                            clicked = True
                            break

            if clicked:
                break

            # 7. Timer -> 15s
            if "timer per question" in low_text:
                for r, c, btext, key in fresh_buttons:
                    if normalize(btext) == normalize(ADVANCE_DEFAULT_TIMER):
                        print("🎯 Timer: 15s")
                        if await click_button(msg, r, c):
                            clicked_message_buttons.add(key)
                            clicked = True
                            break

            if clicked:
                break

            # 8. Final start.
            if (
                "all set" in low_text
                or "press start" in low_text
                or "start quiz" in low_text
            ):
                for r, c, btext, key in fresh_buttons:
                    if normalize(btext) in {
                        "start quiz",
                        "start",
                        "start the quiz",
                    }:
                        print("▶️ Advance: Start Quiz")
                        if await click_button(msg, r, c):
                            clicked_message_buttons.add(key)
                            clicked = True
                            break

            if clicked:
                break

        await asyncio.sleep(0.4)

    # Final poll check.
    return any(
        parse_poll(m)
        for m in await client.get_messages(bot, limit=10)
    )


# =========================================================
# ADVANCE QUIZ: FULL RUN
# =========================================================

async def run_advance():
    global total_questions, questions_done, output_file
    global active_mode, active_bot_username, active_bot_entity, active_run_after_id
    global seen_poll_ids

    shared, quiz_id, count, share_text = (
        await find_latest_advance_shared()
    )

    if not shared:
        print("❌ Saved Messages me Advance shared quiz nahi mila.")
        return

    if count <= 0:
        print("❌ Advance quiz ka question count read nahi hua.")
        return

    total_questions = count
    questions_done = 0
    seen_poll_ids.clear()

    output_file = EXPORT_DIR / f"advance_{quiz_id}.txt"

    active_mode = "advance"
    active_bot_username = ADVANCE_BOT_USERNAME
    active_bot_entity = await client.get_entity("@advance_quiz_bot")

    latest = await client.get_messages(active_bot_entity, limit=1)
    active_run_after_id = latest[0].id if latest else 0

    print()
    print("✅ Advance shared quiz मिला")
    print("🆔 Quiz ID:", quiz_id)
    print("📚 Question count:", count)
    print("📄 Output:", output_file)

    # Start with a clean bot-import-compatible TXT.  No headers/separators.
    output_file.write_text("", encoding="utf-8")

    if not await start_advance_shared_quiz(quiz_id):
        return

    await asyncio.sleep(0.8)

    # Optional setup wizard / Start Quiz flow.
    await advance_setup_and_start(active_bot_entity)

    print("📚 Advance Quiz questions collect kar raha hoon...")

    while questions_done < total_questions:
        try:
            message = await asyncio.wait_for(question_queue.get(), timeout=20)
        except asyncio.TimeoutError:
            print("❌ 20 sec me next Advance question nahi aaya.")
            print("Advance Quiz session ruk gayi/closed ho gayi.")
            break

        poll = parse_poll(message)
        if not poll:
            continue

        questions_done += 1

        print()
        print(f"📖 ADVANCE QUESTION {questions_done}/{total_questions}")
        print(poll["question"])
        for i, option in enumerate(poll["answers"], 1):
            print(f"{i}) {option}")

        try:
            await message.click(0)
            print("✅ Option A submitted")
        except Exception as exc:
            print("⚠️ Advance vote error:", repr(exc))

        await asyncio.sleep(0.3)

        correct = []
        try:
            updated = await client.get_messages(active_bot_entity, ids=message.id)
            if updated:
                correct = get_correct_indexes(updated)
        except Exception:
            pass

        save_question(questions_done, poll, correct)

        if correct:
            print("✅ Correct:", correct)
        else:
            print("ℹ️ Correct answer exposed nahi hua")

        print(f"💾 Advance Q{questions_done} saved")

    print(f"Questions extracted: {questions_done}/{total_questions}")
    print(f"TXT: {output_file}")
    await finish_export("Advance Quiz")


# =========================================================
# MENU
# =========================================================

async def main():
    global active_mode

    print()
    print("=" * 60)
    print("                         BOT3")
    print("=" * 60)
    print()
    print("1) @QuizBot shared quiz → TXT")
    print("2) @advance_quiz_bot shared quiz → TXT")
    print()

    choice = input("Select 1 or 2: ").strip()

    await client.start()

    print("✅ Telegram connected")

    if choice == "1":
        print("✅ Mode: Official QuizBot")
        await run_official()

    elif choice == "2":
        print("✅ Mode: Advance Quiz Bot")
        await run_advance()

    else:
        print("❌ Invalid choice. Enter 1 or 2.")

    active_mode = None
    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⛔ Stopped by user.")
