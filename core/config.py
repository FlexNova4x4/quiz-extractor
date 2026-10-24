import os
from pathlib import Path
from typing import List, Optional

# Base Paths
HOME = Path.home()
PROJECT_DIR = Path(__file__).resolve().parent.parent

# Env file candidates
ENV_FILES = [
    PROJECT_DIR / ".env",
    HOME / ".quizbot.env",
]

for env_path in ENV_FILES:
    if env_path.exists():
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        except Exception:
            pass


def get_api_id() -> int:
    val = os.environ.get("TG_API_ID", "").strip()
    if not val:
        return 0
    try:
        return int(val)
    except ValueError:
        return 0


def get_api_hash() -> str:
    return os.environ.get("TG_API_HASH", "").strip()


def get_bot_token() -> str:
    return os.environ.get("TG_BOT_TOKEN", "").strip()


def get_session_path() -> Path:
    """
    Resolves the Telegram session file path without .session extension.
    Resolution order:
    1. TG_SESSION_PATH env var if explicitly set.
    2. Existing .session file in PROJECT_DIR (e.g. ./telegram_session.session or *.session).
    3. Existing .session file in Termux default path (/data/data/com.termux/files/home/quiz-extractor/telegram_session.session).
    4. Existing .session file in HOME / "quiz-extractor" / "telegram_session.session".
    5. Fallback default: PROJECT_DIR / "telegram_session".
    """
    custom = os.environ.get("TG_SESSION_PATH", "").strip()
    if custom:
        p = Path(custom).expanduser()
        # Telethon expects session path without .session suffix
        if p.name.endswith(".session"):
            p = p.with_name(p.name[:-8])
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # Check for existing session in PROJECT_DIR
    proj_session = PROJECT_DIR / "telegram_session.session"
    if proj_session.exists():
        return PROJECT_DIR / "telegram_session"

    # Check for any *.session in PROJECT_DIR
    try:
        proj_sessions = [f for f in PROJECT_DIR.glob("*.session") if f.is_file()]
        if proj_sessions:
            s = proj_sessions[0]
            return s.with_name(s.name[:-8])
    except Exception:
        pass

    # Check Termux default path
    termux_session = Path("/data/data/com.termux/files/home/quiz-extractor/telegram_session.session")
    if termux_session.exists():
        return termux_session.with_name("telegram_session")

    # Check HOME / "quiz-extractor" / "telegram_session.session"
    home_extractor_session = HOME / "quiz-extractor" / "telegram_session.session"
    if home_extractor_session.exists():
        return HOME / "quiz-extractor" / "telegram_session"

    # Default fallback: project directory session
    default_path = PROJECT_DIR / "telegram_session"
    default_path.parent.mkdir(parents=True, exist_ok=True)
    return default_path


def get_export_dir() -> Path:
    custom = os.environ.get("EXPORT_DIR", "").strip()
    if custom:
        p = Path(custom).expanduser()
    else:
        p = HOME / "storage" / "shared" / "QuizExports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_authorized_users() -> List[int]:
    raw = os.environ.get("AUTHORIZED_USERS", "").strip()
    if not raw:
        return []
    users = []
    for part in raw.split(","):
        p = part.strip()
        if p.isdigit():
            users.append(int(p))
    return users


# Timing and Rate-control Configuration
QUESTION_TIMEOUT_SECONDS = int(os.environ.get("QUESTION_TIMEOUT_SECONDS", "90"))
QUESTION_WAIT_SECONDS = int(os.environ.get("QUESTION_WAIT_SECONDS", "30"))
MAX_IDLE_TIMEOUTS = int(os.environ.get("MAX_IDLE_TIMEOUTS", "3"))
RECOVERY_LIMIT = int(os.environ.get("RECOVERY_LIMIT", "30"))
PACE_BUTTON_CLICK_SECONDS = float(os.environ.get("PACE_BUTTON_CLICK_SECONDS", "0.5"))
PACE_STOP_SECONDS = float(os.environ.get("PACE_STOP_SECONDS", "1.0"))
MAX_FLOOD_WAIT_SECONDS = int(os.environ.get("MAX_FLOOD_WAIT_SECONDS", "300"))

# Bot Usernames
QUIZBOT_USERNAME = "quizbot"
ADVANCE_BOT_USERNAME = "advance_quiz_bot"

# Advance Quiz Bot default settings
ADVANCE_DEFAULT_MARK = "1"
ADVANCE_DEFAULT_NEGATIVE = "Skip (no negative)"
ADVANCE_DEFAULT_SHUFFLE = "No Shuffle"
ADVANCE_DEFAULT_EXPLANATION = "No (default)"
ADVANCE_DEFAULT_TIMER = "15s"
ADVANCE_DEFAULT_MODE = "Play (Practice)"
