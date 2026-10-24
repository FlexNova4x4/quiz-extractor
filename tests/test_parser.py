import pytest
from core.parser import (
    clean_str,
    extract_correct_indexes,
    extract_explanation_from_poll,
    parse_poll_message,
    parse_quiz_input,
)


class DummyOption:
    def __init__(self, option_bytes, text):
        self.option = option_bytes
        self.text = text


class DummyResult:
    def __init__(self, option_bytes, correct):
        self.option = option_bytes
        self.correct = correct


class DummyPoll:
    def __init__(self, question, answers, explanation=None):
        self.question = question
        self.answers = answers
        self.explanation = explanation


class DummyPollMedia:
    def __init__(self, poll, results=None, explanation=None):
        self.poll = poll
        self.results = results
        self.explanation = explanation


class DummyMessage:
    def __init__(self, msg_id, poll_media):
        self.id = msg_id
        self.poll = poll_media


def test_parse_quizbot_direct_link():
    link = "https://t.me/QuizBot?start=M40tSXNF"
    parsed = parse_quiz_input(link)
    assert parsed is not None
    provider, payload, count, title = parsed
    assert provider == "quizbot"
    assert payload == "M40tSXNF"


def test_parse_quizbot_forwarded_card():
    text = (
        "Quiz 'Himachal Pradesh History'\n"
        "30 questions · 30 sec per question\n\n"
        "https://t.me/QuizBot?start=HP_Hist_123"
    )
    parsed = parse_quiz_input(text)
    assert parsed is not None
    provider, payload, count, title = parsed
    assert provider == "quizbot"
    assert payload == "HP_Hist_123"
    assert count == 30
    assert "Himachal Pradesh History" in title


def test_parse_quizbot_hindi_card():
    text = (
        "Quiz ‘हिमाचल प्रदेश सामान्य ज्ञान’\n"
        "25 questions\n"
        "https://t.me/QuizBot?start=HP_Hindi_99"
    )
    parsed = parse_quiz_input(text)
    assert parsed is not None
    provider, payload, count, title = parsed
    assert provider == "quizbot"
    assert payload == "HP_Hindi_99"
    assert count == 25
    assert "हिमाचल प्रदेश सामान्य ज्ञान" in title


def test_parse_advance_quiz_direct_link():
    link = "https://t.me/advance_quiz_bot?start=adv_98765"
    parsed = parse_quiz_input(link)
    assert parsed is not None
    provider, payload, count, title = parsed
    assert provider == "advance_quiz_bot"
    assert payload == "adv_98765"


def test_parse_advance_quiz_shared_card():
    text = (
        "Quiz Shared\n"
        "Title: General Science MCQ\n"
        "Quiz ID: science_54321\n"
        "Questions: 40\n"
        "Time: 15s"
    )
    parsed = parse_quiz_input(text)
    assert parsed is not None
    provider, payload, count, title = parsed
    assert provider == "advance_quiz_bot"
    assert payload == "science_54321"
    assert count == 40
    assert title == "General Science MCQ"


def test_parse_invalid_input():
    assert parse_quiz_input("Hello there, how are you?") is None
    assert parse_quiz_input("https://google.com") is None
    assert parse_quiz_input("") is None


def test_parse_poll_message_with_correct_answer_and_explanation():
    answers = [
        DummyOption(b"opt_0", "Shimla"),
        DummyOption(b"opt_1", "Dharamshala"),
        DummyOption(b"opt_2", "Mandi"),
        DummyOption(b"opt_3", "Kullu"),
    ]
    poll = DummyPoll("What is the capital of Himachal Pradesh?", answers, explanation="Shimla is summer capital.")

    class ResultsObj:
        results = [
            DummyResult(b"opt_0", correct=True),
            DummyResult(b"opt_1", correct=False),
        ]

    media = DummyPollMedia(poll=poll, results=ResultsObj(), explanation=poll.explanation)
    msg = DummyMessage(101, media)

    q = parse_poll_message(msg, number=1)
    assert q is not None
    assert q.question_text == "What is the capital of Himachal Pradesh?"
    assert len(q.options) == 4
    assert q.correct_indexes == [0]
    assert q.explanation == "Shimla is summer capital."
    assert q.is_valid_four_option is True


def test_parse_poll_message_without_correct_answer():
    answers = [
        DummyOption(b"a", "Option 1"),
        DummyOption(b"b", "Option 2"),
        DummyOption(b"c", "Option 3"),
        DummyOption(b"d", "Option 4"),
    ]
    poll = DummyPoll("Test Question", answers)
    media = DummyPollMedia(poll=poll, results=None)
    msg = DummyMessage(102, media)

    q = parse_poll_message(msg, number=2)
    assert q is not None
    assert q.correct_indexes == []
    assert q.explanation is None
