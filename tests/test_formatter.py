import pytest
from pathlib import Path
from core.formatter import (
    QuizFileWriter,
    format_question_block,
    map_answer_letter,
)
from core.models import ExtractedQuestion


def test_map_answer_letter():
    assert map_answer_letter([0], 4) == "ANSWER: A"
    assert map_answer_letter([1], 4) == "ANSWER: B"
    assert map_answer_letter([2], 4) == "ANSWER: C"
    assert map_answer_letter([3], 4) == "ANSWER: D"
    assert map_answer_letter([], 4) == "ANSWER: UNKNOWN"
    assert map_answer_letter([4], 4) == "ANSWER: UNKNOWN"


def test_format_valid_question():
    q = ExtractedQuestion(
        number=1,
        question_text="रोहतांग दर्रा किस राज्य में स्थित है?",
        options=["उत्तराखंड", "हिमाचल प्रदेश", "सिक्किम", "लद्दाख"],
        correct_indexes=[1],
        explanation="यह मनाली के पास स्थित है।",
    )
    valid_block, review_block = format_question_block(1, q)
    assert review_block is None
    assert valid_block is not None

    expected = (
        "Q1. रोहतांग दर्रा किस राज्य में स्थित है?\n"
        "(A) उत्तराखंड\n"
        "(B) हिमाचल प्रदेश\n"
        "(C) सिक्किम\n"
        "(D) लद्दाख\n"
        "ANSWER: B\n"
        "EXP: यह मनाली के पास स्थित है।\n\n"
    )
    assert valid_block == expected


def test_format_question_missing_answer_and_explanation():
    q = ExtractedQuestion(
        number=2,
        question_text="Which river originates from Mansarovar?",
        options=["Indus", "Ganga", "Yamuna", "Godavari"],
        correct_indexes=[],
        explanation=None,
    )
    valid_block, review_block = format_question_block(2, q)
    assert review_block is None
    assert valid_block is not None

    expected = (
        "Q2. Which river originates from Mansarovar?\n"
        "(A) Indus\n"
        "(B) Ganga\n"
        "(C) Yamuna\n"
        "(D) Godavari\n"
        "ANSWER: UNKNOWN\n"
        "EXP: Not available\n\n"
    )
    assert valid_block == expected


def test_question_with_fewer_than_four_options_routes_to_manual_review():
    q = ExtractedQuestion(
        number=3,
        question_text="Is Python dynamically typed?",
        options=["Yes", "No"],
        correct_indexes=[0],
        explanation=None,
    )
    valid_block, review_block = format_question_block(3, q)
    assert valid_block is None
    assert review_block is not None
    assert "MANUAL REVIEW ITEM" in review_block
    assert "has 2 options" in review_block
    assert "(1) Yes" in review_block
    assert "(2) No" in review_block


def test_question_with_more_than_four_options_routes_to_manual_review():
    q = ExtractedQuestion(
        number=4,
        question_text="Select one from five:",
        options=["1", "2", "3", "4", "5"],
        correct_indexes=[2],
    )
    valid_block, review_block = format_question_block(4, q)
    assert valid_block is None
    assert review_block is not None
    assert "has 5 options" in review_block


def test_quiz_file_writer_full_workflow(tmp_path: Path):
    writer = QuizFileWriter(
        export_dir=tmp_path,
        title="हिमाचल सामान्य ज्ञान",
        fallback_id="test123",
    )

    q1 = ExtractedQuestion(
        number=1,
        question_text="Question 1 text",
        options=["A1", "B1", "C1", "D1"],
        correct_indexes=[0],
        explanation="Exp 1",
    )
    # Question 2 has only 3 options (triggers manual review)
    q2 = ExtractedQuestion(
        number=2,
        question_text="Question 2 text (odd options)",
        options=["Opt 1", "Opt 2", "Opt 3"],
        correct_indexes=[1],
    )
    # Question 3 is valid 4-option (must continue export numbered as Q2)
    q3 = ExtractedQuestion(
        number=3,
        question_text="Question 3 text",
        options=["A3", "B3", "C3", "D3"],
        correct_indexes=[2],
        explanation=None,
    )

    writer.add_question(q1)
    writer.add_question(q2)
    writer.add_question(q3)

    out_path, rev_path = writer.finalize()

    assert out_path.exists()
    assert rev_path is not None and rev_path.exists()

    content = out_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Rule 1: First line must be TITLE: ...
    assert lines[0] == "TITLE: हिमाचल सामान्य ज्ञान"

    # Rule 2: Valid questions numbered sequentially Q1., Q2.
    assert "Q1. Question 1 text" in content
    assert "Q2. Question 3 text" in content

    # Rule 3: Four options
    assert "(A) A1" in content
    assert "(B) B1" in content

    # Rule 4 & 5: Answer letter
    assert "ANSWER: A" in content
    assert "ANSWER: C" in content

    # Rule 6: Explanation
    assert "EXP: Exp 1" in content
    assert "EXP: Not available" in content

    # Check Manual Review content
    rev_content = rev_path.read_text(encoding="utf-8")
    assert "MANUAL REVIEW REPORT" in rev_content
    assert "Question 2 text (odd options)" in rev_content
    assert "has 3 options" in rev_content
