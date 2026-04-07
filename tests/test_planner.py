# tests/test_planner.py
import pytest
from unittest.mock import MagicMock
from fantoma.planner import Planner, Subtask, Checkpoint


class TestSubtaskParsing:
    def _make_planner(self, llm_response: str) -> Planner:
        llm = MagicMock()
        llm.chat.return_value = llm_response
        return Planner(llm)

    def test_parses_numbered_subtasks(self):
        p = self._make_planner(
            '1. instruction: Click the search box and type "AI" | mode: interact | done_when: Search results appear\n'
            '2. instruction: Read the first result title | mode: read | done_when: Title text is extracted'
        )
        result = p.decompose("Find AI articles", "Page: Home\nURL: https://example.com")
        assert len(result) == 2
        assert result[0].instruction == 'Click the search box and type "AI"'
        assert result[0].mode == "interact"
        assert result[0].done_when == "Search results appear"
        assert result[1].mode == "read"

    def test_mode_defaults_to_find(self):
        p = self._make_planner(
            '1. instruction: Look around the page | done_when: Found the link'
        )
        result = p.decompose("Find something", "Page: Home\nURL: https://a.com")
        assert result[0].mode == "find"

    def test_empty_response_returns_single_find_subtask(self):
        p = self._make_planner("")
        result = p.decompose("Do something", "Page: Home\nURL: https://a.com")
        assert len(result) == 1
        assert result[0].mode == "find"
        assert "Do something" in result[0].instruction

    def test_caps_at_5_subtasks(self):
        lines = "\n".join(
            f'{i}. instruction: Step {i} | mode: find | done_when: Done {i}'
            for i in range(1, 8)
        )
        p = self._make_planner(lines)
        result = p.decompose("Big task", "Page: Home\nURL: https://a.com")
        assert len(result) <= 5


class TestModeMapping:
    def test_planner_modes_map_to_dom_modes(self):
        assert Planner.MODE_MAP["find"] == "navigate"
        assert Planner.MODE_MAP["interact"] == "form"
        assert Planner.MODE_MAP["read"] == "content"


class TestReplan:
    def _make_planner(self, llm_response: str) -> Planner:
        llm = MagicMock()
        llm.chat.return_value = llm_response
        return Planner(llm)

    def test_replan_returns_new_subtasks(self):
        p = self._make_planner(
            '1. instruction: Try searching instead | mode: interact | done_when: Results visible'
        )
        failed = Subtask("Click the menu", "interact", "Menu opens")
        result = p.replan("Find info", [], failed, "Page: Home\nURL: https://a.com")
        assert result is not None
        assert len(result) == 1

    def test_replan_returns_none_after_3_attempts(self):
        p = self._make_planner(
            '1. instruction: Try again | mode: find | done_when: Found it'
        )
        failed = Subtask("Click menu", "interact", "Menu opens")
        for _ in range(3):
            p.replan("Find info", [], failed, "Page: Home\nURL: https://a.com")
        result = p.replan("Find info", [], failed, "Page: Home\nURL: https://a.com")
        assert result is None

    def test_replan_includes_failed_context_in_prompt(self):
        llm = MagicMock()
        llm.chat.return_value = '1. instruction: New approach | mode: find | done_when: Done'
        p = Planner(llm)
        failed = Subtask("Click the broken menu", "interact", "Menu opens")
        p.replan("Find info", [], failed, "Page: Error\nURL: https://a.com")
        call_args = llm.chat.call_args[0][0]
        prompt_text = " ".join(m["content"] for m in call_args)
        assert "Click the broken menu" in prompt_text


class TestCheckpoint:
    def test_checkpoint_fields(self):
        c = Checkpoint(url="https://a.com", subtask=Subtask("Do X", "find", "Done"), result_summary="Found X")
        assert c.url == "https://a.com"
        assert c.subtask.instruction == "Do X"
        assert c.result_summary == "Found X"
