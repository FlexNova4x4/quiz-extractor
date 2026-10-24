import re
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple

from core.models import ExtractedQuestion

OPTION_LETTERS = ["(A)", "(B)", "(C)", "(D)"]
LETTER_MAP = ["A", "B", "C", "D"]


def sanitize_filename(title: str, fallback_id: str = "quiz", suffix: str = ".txt") -> str:
    """
    Sanitize quiz title into a safe filename while preserving Hindi/English characters.
    Replaces path separators and unsafe OS characters.
    Example: 'Himachal Pradesh GK' -> 'Himachal_Pradesh_GK.txt'
    """
    raw_title = (title or "").strip()
    if not raw_title:
        raw_title = fallback_id or "quiz"

    # Normalize unicode (NFC)
    raw_title = unicodedata.normalize("NFC", raw_title)

    # Replace forbidden path/filesystem characters: / \ : * ? " < > | \0-\31
    cleaned = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", raw_title)

    # Replace whitespace sequences with single underscore
    cleaned = re.sub(r"\s+", "_", cleaned)

    # Collapse multiple consecutive underscores
    cleaned = re.sub(r"_+", "_", cleaned)

    # Strip leading/trailing underscores, dots, hyphens
    cleaned = cleaned.strip("._- ")

    # Prevent path traversal
    cleaned = cleaned.replace("..", "_")

    if not cleaned:
        cleaned = f"quiz_{fallback_id}" if fallback_id else "quiz"

    # Truncate length if excessively long (keep max 80 chars for name)
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rstrip("._- ")

    clean_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    return f"{cleaned}{clean_suffix}"


def get_unique_filepath(directory: Path, base_filename: str) -> Path:
    """
    Ensures the target file does not overwrite an existing file.
    Appends _1, _2, etc., if a collision occurs.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / base_filename
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def map_answer_letter(correct_indexes: List[int], num_options: int = 4) -> str:
    """
    Map correct answer index (0-based) to letter format 'ANSWER: B' or 'ANSWER: UNKNOWN'.
    Strictly never guesses.
    """
    if not correct_indexes:
        return "ANSWER: UNKNOWN"

    first_correct = correct_indexes[0]
    if 0 <= first_correct < num_options and first_correct < len(LETTER_MAP):
        return f"ANSWER: {LETTER_MAP[first_correct]}"

    return "ANSWER: UNKNOWN"


def format_question_block(
    q_num: int,
    question: ExtractedQuestion,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Formats a single question.
    Returns (valid_export_block, manual_review_block).
    If options != 4, valid_export_block is None and manual_review_block is populated.
    """
    options = [opt.strip() for opt in question.options if opt is not None]
    explanation = (question.explanation or "").strip()
    exp_line = f"EXP: {explanation}" if explanation else "EXP:"

    if len(options) != 4:
        # Must be sent to manual review
        reason = (
            f"Question has {len(options)} options. Exactly 4 options required for standard format."
        )
        review_lines = [
            f"--- MANUAL REVIEW ITEM (Original Question {question.number}) ---",
            f"Reason: {reason}",
            f"Question: {question.question_text}",
            "Options:",
        ]
        for idx, opt in enumerate(options, 1):
            review_lines.append(f"  ({idx}) {opt}")

        review_lines.append(map_answer_letter(question.correct_indexes, len(options)))
        review_lines.append(exp_line)
        review_lines.append("-" * 50)
        review_block = "\n".join(review_lines) + "\n\n"
        return None, review_block

    # Exactly 4 options
    answer_line = map_answer_letter(question.correct_indexes, 4)

    lines = [
        f"Q{q_num}. {question.question_text}",
        f"{OPTION_LETTERS[0]} {options[0]}",
        f"{OPTION_LETTERS[1]} {options[1]}",
        f"{OPTION_LETTERS[2]} {options[2]}",
        f"{OPTION_LETTERS[3]} {options[3]}",
        answer_line,
        exp_line,
    ]

    return "\n".join(lines) + "\n\n", None


class QuizFileWriter:
    """
    Thread-safe / isolated file writer for a single quiz extraction job.
    Outputs:
    - Primary file: valid 4-option questions formatted strictly.
    - Manual review file (if needed): questions with != 4 options.
    """

    def __init__(self, export_dir: Path, title: str, fallback_id: str):
        self.export_dir = export_dir
        self.title = (title or "").strip() or f"Quiz_{fallback_id}"
        self.fallback_id = fallback_id

        main_filename = sanitize_filename(self.title, fallback_id, suffix=".txt")
        self.output_path = get_unique_filepath(export_dir, main_filename)

        review_filename = sanitize_filename(
            f"{self.title}_manual_review", fallback_id, suffix=".txt"
        )
        self.review_path = get_unique_filepath(export_dir, review_filename)

        self.valid_question_count = 0
        self.manual_review_count = 0
        self.missing_answer_count = 0

        # Initialize main export file with mandatory first line
        self.output_path.write_text(f"TITLE: {self.title}\n\n", encoding="utf-8")
        self.review_file_initialized = False

    def add_question(self, question: ExtractedQuestion) -> bool:
        """
        Processes and writes one question.
        Returns True if question was valid 4-option, False if routed to manual review.
        """
        valid_block, review_block = format_question_block(
            self.valid_question_count + 1, question
        )

        if valid_block:
            self.valid_question_count += 1
            if not question.correct_indexes:
                self.missing_answer_count += 1

            with self.output_path.open("a", encoding="utf-8") as f:
                f.write(valid_block)
            return True
        else:
            self.manual_review_count += 1
            if not self.review_file_initialized:
                header = (
                    f"MANUAL REVIEW REPORT\n"
                    f"TITLE: {self.title}\n"
                    f"{'=' * 60}\n\n"
                )
                self.review_path.write_text(header, encoding="utf-8")
                self.review_file_initialized = True

            with self.review_path.open("a", encoding="utf-8") as f:
                f.write(review_block)
            return False

    def finalize(self) -> Tuple[Path, Optional[Path]]:
        """
        Finalizes files. If no questions needed manual review,
        removes the review file if it was created.
        Returns (output_path, review_path_or_None).
        """
        if self.manual_review_count == 0 and self.review_path.exists():
            try:
                self.review_path.unlink()
            except Exception:
                pass
            return self.output_path, None

        return self.output_path, (self.review_path if self.manual_review_count > 0 else None)
