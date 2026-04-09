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

    def test_replan_includes_visited_urls_in_prompt(self):
        """Planner must see the cross-subtask URL trail so it can break loops."""
        llm = MagicMock()
        llm.chat.return_value = '1. instruction: Try a new URL | mode: find | done_when: Done'
        p = Planner(llm)
        failed = Subtask("Click menu", "interact", "Menu opens")
        urls = [
            "https://booking.com/",
            "https://booking.com/searchresults.html",
            "https://booking.com/",
            "https://booking.com/searchresults.html",
        ]
        p.replan("Find hotel", [], failed, "Page: Search\nURL: https://booking.com/",
                 visited_urls=urls)
        call_args = llm.chat.call_args[0][0]
        prompt_text = " ".join(m["content"] for m in call_args)
        assert "booking.com/searchresults.html" in prompt_text
        assert "Previously visited URLs" in prompt_text

    def test_replan_visited_urls_none_becomes_placeholder(self):
        """Missing visited_urls should not blow up the prompt template."""
        llm = MagicMock()
        llm.chat.return_value = '1. instruction: X | mode: find | done_when: Done'
        p = Planner(llm)
        failed = Subtask("X", "find", "Done")
        # Old-style positional call with no visited_urls kwarg must still work.
        result = p.replan("Task", [], failed, "Page: Home")
        assert result is not None
        prompt_text = " ".join(m["content"] for m in llm.chat.call_args[0][0])
        assert "Previously visited URLs (avoid revisiting as a dead-end): none" in prompt_text

    def test_replan_caps_visited_urls_to_last_12(self):
        """Very long trails must not balloon the prompt."""
        llm = MagicMock()
        llm.chat.return_value = '1. instruction: X | mode: find | done_when: Done'
        p = Planner(llm)
        failed = Subtask("X", "find", "Done")
        urls = [f"https://a.com/page{i}" for i in range(30)]
        p.replan("Task", [], failed, "Page: Home", visited_urls=urls)
        prompt_text = " ".join(m["content"] for m in llm.chat.call_args[0][0])
        # Last 12 urls are included
        assert "page29" in prompt_text
        assert "page18" in prompt_text
        # Earlier ones are dropped
        assert "page17" not in prompt_text
        assert "page0" not in prompt_text


class TestCheckpoint:
    def test_checkpoint_fields(self):
        c = Checkpoint(url="https://a.com", subtask=Subtask("Do X", "find", "Done"), result_summary="Found X")
        assert c.url == "https://a.com"
        assert c.subtask.instruction == "Do X"
        assert c.result_summary == "Found X"


class TestSummarise:
    def test_passes_all_results_to_llm(self):
        from fantoma.navigator import NavigatorResult
        llm = MagicMock()
        llm.chat.return_value = "The answer is 42"
        p = Planner(llm)
        completed = [
            (Subtask("Step 1", "interact", "Done"), NavigatorResult("done", "Found page A", 3, [], "https://a.com")),
            (Subtask("Step 2", "read", "Done"), NavigatorResult("done", "Price is $42", 2, [], "https://a.com/price")),
        ]
        result = p.summarise("What is the price?", completed)
        assert result == "The answer is 42"
        call_args = llm.chat.call_args[0][0]
        prompt_text = " ".join(m["content"] for m in call_args)
        assert "Found page A" in prompt_text
        assert "Price is $42" in prompt_text

    def test_system_prompt_has_anti_hallucination_rule(self):
        """SUMMARISE_SYSTEM must instruct the LLM not to invent facts
        while still encouraging it to report values that ARE in the data."""
        from fantoma.planner import SUMMARISE_SYSTEM
        # Negative: must forbid invention from general knowledge
        assert "Do not invent" in SUMMARISE_SYSTEM
        # Positive: must encourage reporting what is present (regression guard
        # against the 2026-04-09 over-cautious prompt that made the summariser
        # default to "not found" on pages that did contain the answer).
        assert "Report every relevant value" in SUMMARISE_SYSTEM
        assert "never fall back to" in SUMMARISE_SYSTEM

    def test_summarise_system_message_reaches_llm(self):
        from fantoma.navigator import NavigatorResult
        llm = MagicMock()
        llm.chat.return_value = "Answer"
        p = Planner(llm)
        completed = [(Subtask("s", "read", "Done"),
                      NavigatorResult("done", "data", 1, [], "https://a.com"))]
        p.summarise("t", completed)
        system_msg = llm.chat.call_args[0][0][0]["content"]
        assert "Do not invent" in system_msg
