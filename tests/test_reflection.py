"""Tests for agent reflection parsing and history formatting."""

import pytest

from fantoma.agent import _parse_reflection, _parse_actions


class TestParseReflection:

    def test_full_reflection_with_actions(self):
        raw = """EVAL: Clicked search button, results loaded.
MEMORY: On results page, found 3 items. Need cheapest.
GOAL: Click sort by price dropdown.

CLICK [5]"""
        reflection, remainder = _parse_reflection(raw)
        assert reflection["eval"] == "Clicked search button, results loaded."
        assert reflection["memory"] == "On results page, found 3 items. Need cheapest."
        assert reflection["goal"] == "Click sort by price dropdown."
        actions = _parse_actions(remainder)
        assert len(actions) == 1
        assert actions[0] == ("click", {"element_id": 5})

    def test_no_reflection_just_actions(self):
        raw = "CLICK [3]\nTYPE [1] \"hello\""
        reflection, remainder = _parse_reflection(raw)
        assert reflection["eval"] == ""
        assert reflection["memory"] == ""
        assert reflection["goal"] == ""
        actions = _parse_actions(remainder)
        assert len(actions) == 2

    def test_partial_reflection(self):
        raw = """GOAL: Find the submit button.

CLICK [2]"""
        reflection, remainder = _parse_reflection(raw)
        assert reflection["eval"] == ""
        assert reflection["goal"] == "Find the submit button."
        actions = _parse_actions(remainder)
        assert len(actions) == 1

    def test_multiline_memory(self):
        raw = """EVAL: Search completed.
MEMORY: Found 5 recipes. Best candidate is item 3 with 4.8 stars.
GOAL: Click item 3 to verify prep time.

CLICK [3]"""
        reflection, remainder = _parse_reflection(raw)
        assert "5 recipes" in reflection["memory"]
        assert "4.8 stars" in reflection["memory"]

    def test_empty_input(self):
        reflection, remainder = _parse_reflection("")
        assert reflection["eval"] == ""
        assert reflection["memory"] == ""
        assert reflection["goal"] == ""
        assert remainder == ""

    def test_done_after_reflection(self):
        raw = """EVAL: All criteria verified. Recipe has 4.5 stars and 20min prep.
MEMORY: Found target recipe on allrecipes.com.
GOAL: Task complete.

DONE"""
        reflection, remainder = _parse_reflection(raw)
        assert "4.5 stars" in reflection["eval"]
        actions = _parse_actions(remainder)
        assert actions == [("done", {})]
