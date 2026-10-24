import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ExtractedQuestion:
    number: int
    question_text: str
    options: List[str]
    correct_indexes: List[int] = field(default_factory=list)
    explanation: Optional[str] = None
    is_valid_four_option: bool = True
    skip_reason: Optional[str] = None


@dataclass
class QuizJob:
    job_id: str
    user_id: int
    chat_id: int
    provider: str  # "quizbot" | "advance_quiz_bot"
    payload: str
    raw_input: str
    title: str = ""
    expected_questions: int = 0
    questions_done: int = 0
    status: str = "queued"  # queued, running, waiting_flood, completed, partial, failed
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    output_path: Optional[str] = None
    review_path: Optional[str] = None
    manual_review_count: int = 0
    missing_answers_count: int = 0
    error_message: Optional[str] = None
    flood_wait_until: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QuizJob":
        valid_keys = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)
