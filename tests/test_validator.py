"""Tests for fantoma/validator.py"""
import pytest
from unittest.mock import MagicMock
from fantoma.validator import validate_answer


def _llm(response: str):
    m = MagicMock()
    m.chat.return_value = response
    return m


class TestValidateAnswer:
    def test_yes_passes(self):
        passed, reason = validate_answer("Find HN top story", "CRISPR tech story", _llm("YES\nAnswer names the top story clearly."))
        assert passed is True
        assert reason

    def test_no_fails(self):
        passed, reason = validate_answer("Find HN top story", "I don't know", _llm("NO\nAnswer does not contain a story title."))
        assert passed is False
        assert reason

    def test_case_insensitive_yes(self):
        passed, _ = validate_answer("task", "answer", _llm("yes\nLooks good."))
        assert passed is True

    def test_empty_answer_fails_without_llm(self):
        llm = MagicMock()
        passed, reason = validate_answer("task", "", llm)
        assert passed is False
        llm.chat.assert_not_called()

    def test_whitespace_answer_fails_without_llm(self):
        llm = MagicMock()
        passed, _ = validate_answer("task", "   ", llm)
        assert passed is False
        llm.chat.assert_not_called()

    def test_llm_error_fails_open(self):
        llm = MagicMock()
        llm.chat.side_effect = Exception("connection refused")
        passed, reason = validate_answer("task", "some answer", llm)
        assert passed is True
        assert "unavailable" in reason

    def test_empty_llm_response_fails_open(self):
        passed, reason = validate_answer("task", "answer", _llm(""))
        assert passed is True
        assert "unavailable" in reason

    def test_single_line_response(self):
        # Only one line — no reason, but should still parse verdict
        passed, reason = validate_answer("task", "answer", _llm("YES"))
        assert passed is True
        assert reason == ""

    def test_reason_returned(self):
        _, reason = validate_answer("task", "answer", _llm("NO\nMissing the price."))
        assert reason == "Missing the price."
