#!/usr/bin/env python3
"""
Quiz Extractor — Main Runner & CLI Interface
Supports both interactive CLI extraction from Saved Messages / direct link
and running the Telegram Bot Interface.
"""

import asyncio
import os
import re
import sys
from pathlib import Path

from core.config import (
    get_api_hash,
    get_api_id,
    get_authorized_users,
    get_bot_token,
    get_export_dir,
    get_session_path,
)
from core.engine import TelethonEngine
from core.formatter import sanitize_filename
from core.models import QuizJob
from core.parser import parse_quiz_input


async def find_latest_official_saved(engine: TelethonEngine):
    """Finds latest @QuizBot shared quiz in Saved Messages with FloodWait protection."""
    print("🔎 Saved Messages me latest @QuizBot shared quiz dhoond raha hoon...")
    messages = await engine.safe_get_messages("me", limit=100)
    pattern = re.compile(r"https?://t\.me/QuizBot\?start=([A-Za-z0-9_-]+)", re.I)

    for msg in messages or []:
        raw = msg.stringify() if hasattr(msg, "stringify") else repr(msg)
        match = pattern.search(raw)
        if not match:
            continue

        payload = match.group(1)
        text = msg.raw_text or ""
        cm = re.search(r"(\d+)\s+questions\b", text, re.I)
        count = int(cm.group(1)) if cm else 0

        tm = re.search(r"Quiz\s+['‘](.*?)['’]", text, re.S)
        title = tm.group(1).strip() if tm else ""

        return payload, count, title, msg.id
    return None, 0, "", 0


async def find_latest_advance_saved(engine: TelethonEngine):
    """Finds latest @advance_quiz_bot shared quiz in Saved Messages with FloodWait protection."""
    print("🔎 Saved Messages me latest @advance_quiz_bot shared quiz dhoond raha hoon...")
    messages = await engine.safe_get_messages("me", limit=100)

    for msg in messages or []:
        text = msg.raw_text or ""
        low = text.lower()
        if "quiz shared" not in low and "quiz id:" not in low:
            continue

        im = re.search(r"Quiz\s*ID\s*:\s*([A-Za-z0-9_-]+)", text, re.I)
        if not im:
            continue

        quiz_id = im.group(1)
        cm = re.search(r"Questions:\s*(\d+)", text, re.I)
        count = int(cm.group(1)) if cm else 0

        # Title detection
        title = ""
        tm = re.search(r"Title\s*:\s*(.+)", text, re.I)
        if tm:
            title = tm.group(1).strip()
        else:
            for line in text.splitlines():
                l = line.strip()
                if l and "quiz shared" not in l.lower() and "quiz id" not in l.lower():
                    title = l
                    break

        return quiz_id, count, title, msg.id
    return None, 0, "", 0


async def run_cli_extraction(job: QuizJob, engine: TelethonEngine):
    """Runs a single quiz extraction job via CLI and sends the output to Saved Messages."""
    print()
    print("=" * 60)
    print(f"🚀 RUNNING QUIZ EXTRACTION: {job.provider.upper()}")
    print("=" * 60)
    print(f"• Payload: {job.payload}")
    print(f"• Expected Questions: {job.expected_questions or 'Unknown'}")
    print(f"• Title: {job.title or 'Quiz'}")
    print("=" * 60)

    def on_status(msg):
        print(f"📢 STATUS: {msg}")

    def on_progress(done, total):
        tot_str = str(total) if total else "?"
        print(f"📚 PROGRESS: {done}/{tot_str} questions saved")

    result = await engine.execute_job(
        job, status_callback=on_status, progress_callback=on_progress
    )

    print()
    print("=" * 60)
    print(f"🏁 RESULT: {result.status.upper()}")
    print(f"• Valid 4-Option Questions: {result.questions_done}")
    print(f"• Manual Review Questions: {result.manual_review_count}")
    print(f"• Output TXT: {result.output_path}")
    if result.review_path:
        print(f"• Manual Review TXT: {result.review_path}")
    print("=" * 60)

    # Deliver to Saved Messages if connected as user
    if engine.client and engine.client.is_connected() and result.output_path:
        try:
            out_file = Path(result.output_path)
            if out_file.exists():
                caption = (
                    f"✅ Quiz Export: {result.title or out_file.stem}\n"
                    f"📚 Questions: {result.questions_done}\n"
                    f"📄 {out_file.name}"
                )
                if result.manual_review_count > 0:
                    caption += f"\n⚠️ {result.manual_review_count} questions sent to manual review."
                await engine.safe_call(engine.client.send_file, "me", str(out_file), caption=caption)
                print("✅ TXT file Saved Messages me bhej di gayi hai.")

                if result.review_path and Path(result.review_path).exists():
                    await engine.safe_call(
                        engine.client.send_file,
                        "me",
                        str(result.review_path),
                        caption="⚠️ Manual Review Report (Non 4-option questions)",
                    )
                    print("✅ Manual Review TXT bhi Saved Messages me bhej di.")
        except Exception as e:
            print(f"⚠️ Could not send file to Saved Messages: {e}")


async def main():
    api_id = get_api_id()
    api_hash = get_api_hash()
    session_path = get_session_path()

    if not api_id or not api_hash:
        print("❌ Error: TG_API_ID or TG_API_HASH is missing.")
        print("Please configure them in ~/.quizbot.env or .env file.")
        print("Example:\nTG_API_ID=12345678\nTG_API_HASH=abcdef0123456789\n")
        sys.exit(1)

    engine = TelethonEngine(session_path=session_path, api_id=api_id, api_hash=api_hash)
    if engine.client is None:
        from telethon import TelegramClient
        engine.client = TelegramClient(str(session_path), api_id, api_hash)
    await engine.safe_call(engine.client.connect)
    if not await engine.safe_call(engine.client.is_user_authorized):
        print("\n🔑 Telegram user session authorized nahi hai.")
        print("Pehli baar login karne ke liye phone number aur OTP enter karein:")
        await engine.client.start()
        print("✅ Telegram login successful!")

    print()
    print("=" * 60)
    print("                  QUIZ EXTRACTOR v3.0 (UPGRADED)")
    print("=" * 60)
    print("1) @QuizBot shared quiz (from Saved Messages) → TXT")
    print("2) @advance_quiz_bot shared quiz (from Saved Messages) → TXT")
    print("3) Paste quiz link / card text directly → TXT")
    print("4) Start Telegram Bot Interface (Auto-responder)")
    print("=" * 60)

    choice = input("Select (1-4): ").strip()

    if choice == "1":
        payload, count, title, msg_id = await find_latest_official_saved(engine)
        if not payload:
            print("❌ Saved Messages me @QuizBot shared quiz nahi mila.")
            await engine.disconnect()
            return
        job = QuizJob(
            job_id="cli_official",
            user_id=0,
            chat_id=0,
            provider="quizbot",
            payload=payload,
            title=title or "Official_Quiz",
            expected_questions=count,
            raw_input=f"https://t.me/QuizBot?start={payload}",
        )
        await run_cli_extraction(job, engine)

    elif choice == "2":
        quiz_id, count, title, msg_id = await find_latest_advance_saved(engine)
        if not quiz_id:
            print("❌ Saved Messages me Advance Quiz shared card nahi mila.")
            await engine.disconnect()
            return
        job = QuizJob(
            job_id="cli_advance",
            user_id=0,
            chat_id=0,
            provider="advance_quiz_bot",
            payload=quiz_id,
            title=title or f"Advance_{quiz_id}",
            expected_questions=count,
            raw_input=f"Quiz ID: {quiz_id}",
        )
        await run_cli_extraction(job, engine)

    elif choice == "3":
        print("\nQuiz link ya card paste karein:")
        raw_input = input("> ").strip()
        parsed = parse_quiz_input(raw_input)
        if not parsed:
            print("❌ Invalid quiz format. Enter a valid QuizBot or Advance Quiz Bot link/card.")
            await engine.disconnect()
            return
        provider, payload, count, title = parsed
        job = QuizJob(
            job_id="cli_custom",
            user_id=0,
            chat_id=0,
            provider=provider,
            payload=payload,
            title=title or f"{provider}_{payload}",
            expected_questions=count,
            raw_input=raw_input,
        )
        await run_cli_extraction(job, engine)

    elif choice == "4":
        await engine.disconnect()
        from bot_interface import main as bot_main

        await bot_main()
        return

    else:
        print("❌ Invalid selection.")

    await engine.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⛔ Exited by user.")
