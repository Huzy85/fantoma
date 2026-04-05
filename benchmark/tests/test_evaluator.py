"""Tests for evaluator verdict parsing."""

from benchmark.evaluator import parse_verdict, build_eval_messages


def test_parse_verdict_success():
    assert parse_verdict("The task was completed. SUCCESS") == "SUCCESS"


def test_parse_verdict_not_success():
    assert parse_verdict("The agent failed to find the recipe. NOT SUCCESS") == "NOT SUCCESS"


def test_parse_verdict_not_success_takes_priority():
    """NOT SUCCESS should match even if SUCCESS appears as substring."""
    assert parse_verdict("NOT SUCCESS despite some progress") == "NOT SUCCESS"


def test_parse_verdict_indeterminate():
    assert parse_verdict("I cannot determine the outcome.") is None


def test_build_eval_messages_structure():
    """Eval messages have system + user with text and image."""
    msgs = build_eval_messages(
        instruction="Find a recipe",
        answer="Vegetarian lasagna",
        screenshot_b64="aW1hZ2VkYXRh",
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "evaluator" in msgs[0]["content"].lower()
    assert msgs[1]["role"] == "user"
    content = msgs[1]["content"]
    assert isinstance(content, list)
    types = [c["type"] for c in content]
    assert types == ["text", "image_url", "text"]
