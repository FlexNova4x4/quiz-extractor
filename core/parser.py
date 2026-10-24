import re
from typing import Any, List, Optional, Tuple

from core.models import ExtractedQuestion

# Regex patterns for QuizBot
QUIZBOT_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/QuizBot\?start=([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
QUIZBOT_TG_URI_PATTERN = re.compile(
    r"tg://resolve\?domain=QuizBot&start=([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

QUIZBOT_INLINE_PATTERN = re.compile(
    r"@?QuizBot\s+quiz:([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

# Regex patterns for Advance Quiz Bot
ADVANCE_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/advance_quiz_bot\?start=([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
ADVANCE_TG_URI_PATTERN = re.compile(
    r"tg://resolve\?domain=advance_quiz_bot&start=([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
ADVANCE_CARD_ID_PATTERN = re.compile(
    r"Quiz\s*ID\s*:\s*([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    text = getattr(value, "text", None)
    if text is not None:
        return str(text).strip()
    return str(value).strip()


def parse_quiz_input(raw_text: str) -> Optional[Tuple[str, str, int, str]]:
    """
    Parses user input (link, card, forwarded message text) to identify:
    (provider, payload, question_count, title)

    Supported providers: 'quizbot' or 'advance_quiz_bot'.
    Returns None if no supported quiz pattern is detected.
    """
    text = (raw_text or "").strip()
    if not text:
        return None

    # 1. Check for QuizBot link
    qb_inline_match = QUIZBOT_INLINE_PATTERN.search(text)
    qb_match = (
        QUIZBOT_URL_PATTERN.search(text)
        or QUIZBOT_TG_URI_PATTERN.search(text)
        or qb_inline_match
    )
    if qb_match:
        payload = qb_match.group(1)
        count_match = re.search(r"(\d+)\s+questions\b", text, re.IGNORECASE)
        count = int(count_match.group(1)) if count_match else 0

        title_match = re.search(r"Quiz\s+['‘](.*?)['’]", text, re.DOTALL | re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else ""

        if not title and not qb_inline_match:
            # Fallback title check
            first_line = text.splitlines()[0].strip()
            if "t.me" not in first_line and len(first_line) > 3:
                title = first_line

        return "quizbot", payload, count, title

    # 2. Check for Advance Quiz Bot link
    adv_link_match = ADVANCE_URL_PATTERN.search(text) or ADVANCE_TG_URI_PATTERN.search(text)
    if adv_link_match:
        payload = adv_link_match.group(1)
        count_match = re.search(r"Questions\s*:\s*(\d+)", text, re.IGNORECASE) or re.search(
            r"(\d+)\s+questions\b", text, re.IGNORECASE
        )
        count = int(count_match.group(1)) if count_match else 0

        title = ""
        title_match = re.search(r"Title\s*:\s*(.+)", text, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
        else:
            first_line = text.splitlines()[0].strip()
            if "t.me" not in first_line and "quiz" not in first_line.lower() and len(first_line) > 3:
                title = first_line

        return "advance_quiz_bot", payload, count, title

    # 3. Check for Advance Quiz Bot shared card (Quiz ID: xxx)
    adv_card_match = ADVANCE_CARD_ID_PATTERN.search(text)
    if adv_card_match:
        payload = adv_card_match.group(1)
        count_match = re.search(r"Questions\s*:\s*(\d+)", text, re.IGNORECASE)
        count = int(count_match.group(1)) if count_match else 0

        title = ""
        title_match = re.search(r"Title\s*:\s*(.+)", text, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
        else:
            # Look for lines preceding Quiz ID
            lines = text.splitlines()
            for line in lines:
                l = line.strip()
                if (
                    l
                    and "quiz shared" not in l.lower()
                    and "quiz id" not in l.lower()
                    and "questions" not in l.lower()
                ):
                    title = l
                    break

        return "advance_quiz_bot", payload, count, title

    return None


def extract_explanation_from_poll(media: Any) -> Optional[str]:
    """
    Extracts explanation text from Telegram poll media if present.
    """
    if not media:
        return None

    # Check poll object directly
    poll_obj = getattr(media, "poll", None)
    if poll_obj:
        exp = getattr(poll_obj, "explanation", None)
        if exp:
            return clean_str(exp)

    # Check media root
    exp = getattr(media, "explanation", None)
    if exp:
        return clean_str(exp)

    return None


def extract_correct_indexes(media: Any) -> List[int]:
    """
    Extracts correct answer index/indices from Telegram Poll results.
    Works when Telegram exposes poll result.correct.
    """
    try:
        if not media or not getattr(media, "results", None):
            return []

        poll_obj = getattr(media, "poll", None)
        if not poll_obj:
            return []

        results_obj = media.results
        answers = getattr(poll_obj, "answers", []) or []

        option_map = {}
        for idx, answer in enumerate(answers):
            opt = getattr(answer, "option", None)
            if opt is not None:
                option_map[bytes(opt)] = idx

        correct = []
        for result in getattr(results_obj, "results", []) or []:
            if not getattr(result, "correct", False):
                continue
            opt = getattr(result, "option", None)
            if opt is None:
                continue
            idx = option_map.get(bytes(opt))
            if idx is not None:
                correct.append(idx)

        return sorted(set(correct))
    except Exception:
        return []


def parse_poll_message(message: Any, number: int = 1) -> Optional[ExtractedQuestion]:
    """
    Extracts ExtractedQuestion from a Telethon poll Message.
    """
    try:
        media = getattr(message, "poll", None)
        if not media:
            return None

        poll_obj = getattr(media, "poll", None)
        if not poll_obj:
            return None

        q_text = clean_str(getattr(poll_obj, "question", None))
        raw_answers = getattr(poll_obj, "answers", []) or []
        answers = [clean_str(getattr(a, "text", "")) for a in raw_answers]

        if not q_text or not answers:
            return None

        correct_indexes = extract_correct_indexes(media)
        explanation = extract_explanation_from_poll(media)

        is_valid = len(answers) == 4
        skip_reason = None
        if not is_valid:
            skip_reason = f"Poll has {len(answers)} options (expected 4)"

        return ExtractedQuestion(
            number=number,
            question_text=q_text,
            options=answers,
            correct_indexes=correct_indexes,
            explanation=explanation,
            is_valid_four_option=is_valid,
            skip_reason=skip_reason,
        )
    except Exception:
        return None
