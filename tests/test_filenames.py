import pytest
from pathlib import Path
from core.formatter import get_unique_filepath, sanitize_filename


def test_sanitize_english_title():
    title = "Himachal Pradesh GK 2026"
    filename = sanitize_filename(title)
    assert filename == "Himachal_Pradesh_GK_2026.txt"


def test_sanitize_hindi_title():
    title = "हिमाचल प्रदेश सामान्य ज्ञान"
    filename = sanitize_filename(title)
    assert filename == "हिमाचल_प्रदेश_सामान्य_ज्ञान.txt"


def test_sanitize_forbidden_characters():
    title = 'Maths: Part/1 *Final* "Exam" <Test> | Cool?'
    filename = sanitize_filename(title)
    assert "/" not in filename
    assert "\\" not in filename
    assert ":" not in filename
    assert "*" not in filename
    assert "?" not in filename
    assert '"' not in filename
    assert "<" not in filename
    assert ">" not in filename
    assert "|" not in filename
    assert filename.endswith(".txt")


def test_sanitize_path_traversal_attempt():
    title = "../../etc/passwd"
    filename = sanitize_filename(title)
    assert ".." not in filename
    assert "/" not in filename
    assert not filename.startswith("/")


def test_sanitize_empty_title_uses_fallback():
    assert sanitize_filename("", fallback_id="quiz_123") == "quiz_123.txt"
    assert sanitize_filename("    ", fallback_id="xyz") == "xyz.txt"


def test_get_unique_filepath_avoids_overwriting(tmp_path: Path):
    name = "General_Knowledge.txt"

    # First call - creates target
    f1 = get_unique_filepath(tmp_path, name)
    assert f1.name == "General_Knowledge.txt"
    f1.write_text("file 1", encoding="utf-8")

    # Second call - must return _1
    f2 = get_unique_filepath(tmp_path, name)
    assert f2.name == "General_Knowledge_1.txt"
    f2.write_text("file 2", encoding="utf-8")

    # Third call - must return _2
    f3 = get_unique_filepath(tmp_path, name)
    assert f3.name == "General_Knowledge_2.txt"
