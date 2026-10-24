from typing import List, Optional

from core.models import QuizJob


class Messages:
    """
    Organized message templates for Telegram user-facing bot and terminal outputs.
    Default style is clean Hinglish/English with clear icons and actionable info.
    Easily editable in one place.
    """

    @staticmethod
    def start_welcome() -> str:
        return (
            "👋 **Namaste! Quiz Extractor Bot me aapka swagat hai.**\n\n"
            "Mujhe kisi bhi quiz ka shared link ya shared quiz card forward karein:\n"
            "• **@QuizBot** (e.g. `https://t.me/QuizBot?start=XYZ`)\n"
            "• **@advance_quiz_bot** (e.g. `Quiz ID: 12345` ya link)\n\n"
            "Commands:\n"
            "• `/status` - Current job aur queue ki sthiti dekhein\n"
            "• `/help` - Help aur supported format guide dekhein"
        )

    @staticmethod
    def help_guide() -> str:
        return (
            "📖 **Quiz Extractor Guide**\n\n"
            "**Supported Quizzes:**\n"
            "1. **@QuizBot**: Quiz link send karein jaise `https://t.me/QuizBot?start=...` ya QuizBot ka shared message forward karein.\n"
            "2. **@advance_quiz_bot**: Shared card forward karein jisme `Quiz ID: ...` ho ya direct link bhejein.\n\n"
            "**Processing Flow:**\n"
            "• Ek samay par ek hi quiz live session me extract hoti hai (FloodWait se bachne ke liye).\n"
            "• Multiple quizzes queue me lag jayengi aur serial order me execute hongi.\n"
            "• Complete hone par formatted TXT file aapko yahin deliver ho jayegi.\n\n"
            "**Format:**\n"
            "Har question strictly 4 options (A, B, C, D) aur correct answer ke saath export hota hai. Agar koi question 4 options ka nahi hai toh alag se manual-review file me save hota hai."
        )

    @staticmethod
    def unauthorized() -> str:
        return "⛔ **Access Denied:** Aap is bot ko use karne ke liye authorized nahi hain."

    @staticmethod
    def invalid_input() -> str:
        return (
            "⚠️ **Format Samajh Nahi Aaya!**\n\n"
            "Kripya ek valid quiz link ya card forward karein:\n"
            "• Official QuizBot: `https://t.me/QuizBot?start=...`\n"
            "• Advance Quiz Bot: `Quiz ID: ...` ya `https://t.me/advance_quiz_bot?start=...`"
        )

    @staticmethod
    def duplicate_job(job_id: str, status: str) -> str:
        return (
            f"ℹ️ **Yeh quiz pehle se process ho rahi hai.**\n"
            f"• Job ID: `{job_id}`\n"
            f"• Status: {status.upper()}"
        )

    @staticmethod
    def job_accepted(job: QuizJob, queue_position: int) -> str:
        prov = "@QuizBot" if job.provider == "quizbot" else "@advance_quiz_bot"
        title_str = f"• Title: **{job.title}**\n" if job.title else ""
        count_str = f"• Expected Questions: **{job.expected_questions}**\n" if job.expected_questions else ""
        return (
            f"📥 **Quiz Request Accept Ho Gayi Hai!**\n\n"
            f"• Job ID: `{job.job_id}`\n"
            f"• Provider: **{prov}**\n"
            f"{title_str}"
            f"{count_str}"
            f"• Queue Position: **#{queue_position}**\n\n"
            f"Session start hote hi aapko update milega."
        )

    @staticmethod
    def job_started(job: QuizJob) -> str:
        prov = "@QuizBot" if job.provider == "quizbot" else "@advance_quiz_bot"
        title_display = job.title or "Quiz"
        return (
            f"🚀 **Quiz Extraction Shuru Ho Rahi Hai...**\n\n"
            f"• Job ID: `{job.job_id}`\n"
            f"• Title: **{title_display}**\n"
            f"• Provider: **{prov}**\n"
            f"• Target Questions: **{job.expected_questions if job.expected_questions else 'Detecting...'}**"
        )

    @staticmethod
    def progress_update(job: QuizJob, done: int, total: int) -> str:
        total_str = str(total) if total > 0 else "?"
        percent = f" ({int(done / total * 100)}%)" if total > 0 else ""
        return f"⏳ **Progress:** `{done}/{total_str}` questions extract ho chuke hain{percent}..."

    @staticmethod
    def flood_wait(seconds: int, job_id: str) -> str:
        return (
            f"⚠️ **Telegram FloodWait (Rate Limit)!**\n\n"
            f"Telegram server ne temporary delay diya hai.\n"
            f"• Job: `{job_id}`\n"
            f"• Wait Time: **{seconds} seconds**\n"
            f"Engine wait karne ke baad automatically safe tarike se resume karega."
        )

    @staticmethod
    def completed(job: QuizJob, filename: str) -> str:
        rev_info = (
            f"\n⚠️ **Manual Review:** {job.manual_review_count} question(s) alag file me save kiye gaye hain (non 4-option)."
            if job.manual_review_count > 0
            else ""
        )
        unknown_info = (
            f"\nℹ️ **Unknown Answers:** {job.missing_answers_count} question(s) ka correct answer Telegram ne reveal nahi kiya."
            if job.missing_answers_count > 0
            else ""
        )
        return (
            f"✅ **Quiz Extraction Safalta-purvak Complete!**\n\n"
            f"• Title: **{job.title or 'Quiz'}**\n"
            f"• Total Extracted: **{job.questions_done}** questions\n"
            f"• File Name: `{filename}`"
            f"{rev_info}"
            f"{unknown_info}\n\n"
            f"📁 TXT file neeche deliver ki ja rahi hai:"
        )

    @staticmethod
    def partial_completion(job: QuizJob, filename: str, reason: str) -> str:
        return (
            f"⚠️ **Quiz Extraction Partially Complete!**\n\n"
            f"• Title: **{job.title or 'Quiz'}**\n"
            f"• Extracted: **{job.questions_done}/{job.expected_questions}** questions\n"
            f"• File: `{filename}`\n"
            f"• Reason: {reason}\n\n"
            f"Jitne questions extract huye hain unki file neeche send ki ja rahi hai."
        )

    @staticmethod
    def failed(job: QuizJob, error_message: str) -> str:
        return (
            f"❌ **Quiz Extraction Fail Ho Gayi!**\n\n"
            f"• Job ID: `{job.job_id}`\n"
            f"• Provider: `{job.provider}`\n"
            f"• Error: {error_message}\n\n"
            f"Kripya link/card check karein ya thodi der baad dobara koshish karein."
        )

    @staticmethod
    def queue_status(active_job: Optional[QuizJob], pending_jobs: List[QuizJob]) -> str:
        lines = ["📊 **Current Queue Status:**\n"]
        if active_job:
            lines.append(
                f"▶️ **Running Job:** `{active_job.job_id}`\n"
                f"   • Title: {active_job.title or 'In Progress'}\n"
                f"   • Progress: {active_job.questions_done}/{active_job.expected_questions or '?'}\n"
                f"   • Status: {active_job.status.upper()}"
            )
            if active_job.status == "waiting_flood" and active_job.flood_wait_until:
                import time

                rem = max(0, int(active_job.flood_wait_until - time.time()))
                lines.append(f"   • FloodWait remaining: {rem}s")
        else:
            lines.append("▶️ **Running Job:** None (Engine Idle)")

        lines.append(f"\n📋 **Pending in Queue:** {len(pending_jobs)}")
        for idx, pj in enumerate(pending_jobs[:5], 1):
            t = pj.title or pj.payload
            lines.append(f"{idx}. `{pj.job_id}` - {t} ({pj.provider})")

        if len(pending_jobs) > 5:
            lines.append(f"... aur {len(pending_jobs) - 5} aur jobs.")

        return "\n".join(lines)
