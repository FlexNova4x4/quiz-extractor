import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_gitignore_protects_critical_files():
    gitignore_path = PROJECT_ROOT / ".gitignore"
    assert gitignore_path.exists()
    content = gitignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

    expected_rules = [
        ".env",
        "*.session",
        "jobs_state.json",
        "*.log",
        "QuizExports/",
    ]

    for rule in expected_rules:
        assert any(rule in line for line in lines), f"Missing {rule} in .gitignore"


def test_env_example_contains_only_placeholders():
    env_example_path = PROJECT_ROOT / ".env.example"
    assert env_example_path.exists()
    content = env_example_path.read_text(encoding="utf-8")

    # Ensure no real bot token (pattern like 123456789:AA...)
    # The dummy example in .env.example should not be a real token
    assert "TG_API_ID=" in content
    assert "TG_API_HASH=" in content
    assert "TG_BOT_TOKEN=" in content

    # Check for placeholder indicators
    assert "YOUR_TELEGRAM_API_ID" in content
    assert "YOUR_TELEGRAM_API_HASH_HERE" in content
    assert "YOUR_BOT_TOKEN_FROM_BOTFATHER" in content


def test_no_live_session_committed():
    # Make sure no .session files are committed in repo
    session_files = list(PROJECT_ROOT.glob("*.session"))
    assert len(session_files) == 0, f"Found session files in working tree: {session_files}"
