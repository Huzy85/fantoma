# tests/test_navigator.py
import pytest
from unittest.mock import MagicMock, patch
from fantoma.navigator import Navigator, NavigatorResult, _parse_actions, MODE_MAP
from fantoma.planner import Subtask
from fantoma.state_tracker import StateTracker


class TestParseActions:
    """Moved from agent.py -- verify action parsing still works."""

    def test_click(self):
        result = _parse_actions("CLICK [3]")
        assert result == [("click", {"element_id": 3})]

    def test_type(self):
        result = _parse_actions('TYPE [5] "hello world"')
        assert result == [("type_text", {"element_id": 5, "text": "hello world"})]

    def test_select(self):
        result = _parse_actions('SELECT [2] "Option A"')
        assert result == [("select", {"element_id": 2, "value": "Option A"})]

    def test_scroll_down(self):
        result = _parse_actions("SCROLL down")
        assert result == [("scroll", {"direction": "down"})]

    def test_navigate(self):
        result = _parse_actions("NAVIGATE https://example.com")
        assert result == [("navigate", {"url": "https://example.com"})]

    def test_press(self):
        result = _parse_actions("PRESS Enter")
        assert result == [("press_key", {"key": "Enter"})]

    def test_done(self):
        result = _parse_actions("DONE")
        assert result == [("done", {})]

    def test_multiple_actions(self):
        result = _parse_actions("CLICK [1]\nTYPE [2] \"test\"\nPRESS Enter")
        assert len(result) == 3
        assert result[0] == ("click", {"element_id": 1})
        assert result[1] == ("type_text", {"element_id": 2, "text": "test"})
        assert result[2] == ("press_key", {"key": "Enter"})

    def test_done_terminates_sequence(self):
        result = _parse_actions("CLICK [1]\nDONE\nCLICK [2]")
        assert len(result) == 2
        assert result[1] == ("done", {})

    def test_max_5_actions(self):
        lines = "\n".join(f"CLICK [{i}]" for i in range(10))
        result = _parse_actions(lines)
        assert len(result) == 5

    def test_empty_returns_empty(self):
        assert _parse_actions("") == []
        assert _parse_actions(None) == []


class TestModeMap:
    def test_all_planner_modes_mapped(self):
        assert MODE_MAP["find"] == "navigate"
        assert MODE_MAP["interact"] == "form"
        assert MODE_MAP["read"] == "content"


class TestNavigatorResult:
    def test_fields(self):
        r = NavigatorResult(status="done", data="Found it", steps_taken=3, steps_detail=[], final_url="https://a.com")
        assert r.status == "done"
        assert r.data == "Found it"
        assert r.steps_taken == 3


class TestDomainDrift:
    def test_detects_drift(self):
        nav = Navigator()
        assert nav._is_domain_drift("https://www.espn.com/scores", "www.espn.co.uk") is True

    def test_no_drift_same_domain(self):
        nav = Navigator()
        assert nav._is_domain_drift("https://www.amazon.com/results", "www.amazon.com") is False

    def test_no_drift_subdomain(self):
        nav = Navigator()
        assert nav._is_domain_drift("https://www.amazon.com/results", "amazon.com") is False

    def test_no_drift_without_start_domain(self):
        nav = Navigator()
        assert nav._is_domain_drift("https://www.example.com", "") is False
