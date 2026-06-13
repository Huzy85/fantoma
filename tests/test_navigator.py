# tests/test_navigator.py
import time
import pytest
from unittest.mock import MagicMock, PropertyMock, patch
from fantoma.navigator import Navigator, NavigatorResult, _parse_actions, MODE_MAP
from fantoma.planner import Subtask
from fantoma.state_tracker import StateTracker


class TestNavigationLoopGuard:
    """Phase 3 — stop re-bouncing to an already-visited page (e.g. re-doing a login)."""

    def test_repeated_bounce_back_stops(self):
        nav = Navigator()
        subtask = Subtask("do the thing", "find", "done")
        state = {"url": "https://site.com/a"}

        page = MagicMock()
        type(page).url = PropertyMock(side_effect=lambda: state["url"])
        page.title.return_value = "T"
        page.inner_text.return_value = "body content here"

        fantoma = MagicMock()
        fantoma._engine.get_page.return_value = page
        fantoma._dom.extract.return_value = "[0] link 'x'"
        fantoma._dom.extract_content.return_value = "content"
        fantoma._dom.signature.return_value = None

        def navigate(url=None):
            state["url"] = url
            return {"success": True, "state": {"url": url}}
        fantoma.navigate.side_effect = navigate

        llm = MagicMock()
        # bounce a -> b -> a -> b -> a ; the 2nd navigate back to /a trips the guard
        llm.chat.side_effect = [
            "NAVIGATE https://site.com/b",
            "NAVIGATE https://site.com/a",
            "NAVIGATE https://site.com/b",
            "NAVIGATE https://site.com/a",
            "extracted answer",  # _extract_answer call after the guard fires
        ]
        tracker = StateTracker()

        with patch("fantoma.navigator.aria_snapshot", return_value={}):
            with patch("fantoma.navigator.aria_diff", return_value=""):
                with patch("fantoma.navigator.classify_blocker", return_value=None):
                    result = nav.execute(subtask, fantoma, llm, tracker, max_steps=10)

        assert result.failure_reason == "navigation_loop"
        # The 4th navigate (2nd bounce to /a) was blocked, so navigate ran only 3x
        assert fantoma.navigate.call_count == 3


class TestWallClockDeadline:
    """Phase 2 — a slow LLM must not blow the time budget within the step budget."""

    def test_past_deadline_returns_timeout_without_navigating(self):
        nav = Navigator()
        subtask = Subtask("do the thing", "find", "done")
        fantoma = MagicMock()
        page = MagicMock()
        page.url = "https://example.com"
        page.title.return_value = "Example"
        page.inner_text.return_value = "page body content"
        fantoma._engine.get_page.return_value = page
        fantoma._dom.extract.return_value = "[0] button 'Go'"
        fantoma._dom.extract_content.return_value = "content"
        llm = MagicMock()
        llm.chat.return_value = "extracted answer"
        tracker = StateTracker()

        with patch("fantoma.navigator.classify_blocker", return_value=None):
            result = nav.execute(subtask, fantoma, llm, tracker, max_steps=10,
                                 deadline=time.monotonic() - 1)

        assert result.status == "timeout"
        assert result.failure_reason == "timeout"
        # No navigation action was dispatched (deadline hit before the LLM loop)
        fantoma.click.assert_not_called()
        fantoma.type_text.assert_not_called()


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

    def test_back(self):
        result = _parse_actions("BACK")
        assert result == [("go_back", {})]

    def test_back_terminates_sequence(self):
        result = _parse_actions("CLICK [1]\nBACK\nCLICK [2]")
        assert len(result) == 2
        assert result[1] == ("go_back", {})

    def test_empty_returns_empty(self):
        assert _parse_actions("") == []
        assert _parse_actions(None) == []

    # --- Phase 0 weak-LLM parser hardening ---

    def test_type_unquoted_is_a_type_not_a_click(self):
        """Weak models drop the quotes. Must still parse as TYPE, never a
        wrong CLICK on the input via the [N] catch-all."""
        result = _parse_actions("TYPE [3] hello world")
        assert result == [("type_text", {"element_id": 3, "text": "hello world"})]

    def test_type_preserves_apostrophe(self):
        """Quoted text with an inner apostrophe must not truncate at the
        first quote character."""
        result = _parse_actions("TYPE [5] \"it's here\"")
        assert result == [("type_text", {"element_id": 5, "text": "it's here"})]

    def test_done_requires_full_match(self):
        """A line that merely starts with 'done' (e.g. the prompt's own
        done_when: field echoed back) must NOT terminate the subtask."""
        assert _parse_actions("done_when: the page shows results") == []

    def test_done_allows_trailing_punctuation(self):
        assert _parse_actions("DONE.") == [("done", {})]
        assert _parse_actions("done") == [("done", {})]

    def test_navigate_strips_trailing_punctuation(self):
        result = _parse_actions("NAVIGATE https://example.com.")
        assert result == [("navigate", {"url": "https://example.com"})]

    def test_malformed_type_does_not_become_click(self):
        """TYPE with no text must not degrade into a CLICK on the field."""
        assert _parse_actions("TYPE [4]") == []


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


class TestMutationFeedback:
    def test_change_line_included_in_prompt(self):
        """Verify the navigator includes Change: line in LLM messages."""
        from fantoma.navigator import Navigator, NAVIGATOR_SYSTEM
        from fantoma.planner import Subtask
        from fantoma.state_tracker import StateTracker

        nav = Navigator()
        subtask = Subtask("Click the button", "interact", "Page changes")

        # Mock fantoma
        fantoma = MagicMock()
        page = MagicMock()
        page.url = "https://example.com"
        page.title.return_value = "Example"
        fantoma._engine.get_page.return_value = page
        fantoma._dom.extract.return_value = "Page: Example\n[0] button 'Submit'"
        fantoma._dom.extract_content.return_value = "Some content"
        fantoma.click.return_value = {"success": True, "state": {"url": "https://example.com"}}

        # Mock LLM: first call returns CLICK, second returns DONE
        llm = MagicMock()
        llm.chat.side_effect = ["CLICK [0]", "DONE"]

        tracker = StateTracker()

        with patch("fantoma.navigator.aria_snapshot", return_value={}):
            with patch("fantoma.navigator.aria_diff", return_value="+ [button] \"Results\""):
                with patch("fantoma.navigator.classify_blocker", return_value=None):
                    result = nav.execute(subtask, fantoma, llm, tracker, max_steps=5)

        # Check second LLM call includes ARIA diff feedback
        second_call = llm.chat.call_args_list[1][0][0]
        user_msg = [m for m in second_call if m["role"] == "user"][0]["content"]
        assert "+ [button]" in user_msg


class TestDomainDriftMidBatch:
    """Drift must be caught immediately after each action, not at end-of-step.

    Regression: Booking--0 clicked a search-result ad that redirected to
    booking.lastminute.com. The remaining actions in the batch kept executing
    on the partner domain before drift was detected.
    """

    def test_drift_breaks_mid_batch_and_returns_immediately(self):
        nav = Navigator()
        subtask = Subtask("Click the result", "interact", "Results open")

        # Mutable URL holder so every .url access returns the current value.
        # The click() mock flips the holder to a partner domain.
        state = {"url": "https://booking.com/search"}

        page = MagicMock()
        type(page).url = PropertyMock(side_effect=lambda: state["url"])
        page.title.return_value = "Booking"

        fantoma = MagicMock()
        fantoma._engine.get_page.return_value = page
        fantoma._dom.extract.return_value = "[0] a 'First result'\n[1] a 'Second result'"
        fantoma._dom.extract_content.return_value = "search results"

        def flip_to_partner(**kwargs):
            state["url"] = "https://partner.com/deal"
            return {"success": True, "state": {"url": state["url"]}}

        fantoma.click.side_effect = flip_to_partner

        llm = MagicMock()
        # A single LLM response with TWO clicks. After the first click drifts,
        # the second must NOT execute.
        llm.chat.return_value = "CLICK [0]\nCLICK [1]"

        tracker = StateTracker()

        with patch("fantoma.navigator.aria_snapshot", return_value={}):
            with patch("fantoma.navigator.aria_diff", return_value=""):
                with patch("fantoma.navigator.classify_blocker", return_value=None):
                    result = nav.execute(
                        subtask, fantoma, llm, tracker,
                        max_steps=3, start_domain="booking.com",
                    )

        assert result.status == "failed"
        assert result.failure_reason == "domain_drift"
        # Only the first click should have been dispatched.
        assert fantoma.click.call_count == 1


class TestExtractOnDoneAntiHallucination:
    def test_prompt_forbids_inventing_facts(self):
        """EXTRACT_ON_DONE must forbid invention while still encouraging
        extraction of values that ARE on the page. Regression guard against
        the 2026-04-09 over-cautious prompt that made the navigator default
        to 'not on page' for data that was actually visible."""
        from fantoma.navigator import EXTRACT_ON_DONE
        # Negative: must forbid invention from general knowledge
        assert "Do not invent values from general knowledge" in EXTRACT_ON_DONE
        # Positive: must tell the model to report what is present
        assert "Report every relevant value" in EXTRACT_ON_DONE
        assert "never default to" in EXTRACT_ON_DONE


class TestEmptyResponseBailout:
    """Navigator must escape when the LLM returns no usable actions."""

    def _make_fantoma(self):
        fantoma = MagicMock()
        page = MagicMock()
        page.url = "https://example.com"
        page.title.return_value = "Example"
        fantoma._engine.get_page.return_value = page
        fantoma._dom.extract.return_value = "Page: Example\n[0] button 'Submit'"
        fantoma._dom.extract_content.return_value = "Some content"
        return fantoma

    def test_two_consecutive_empties_bail_out(self):
        nav = Navigator()
        subtask = Subtask("Click submit", "interact", "Form submitted")
        fantoma = self._make_fantoma()
        llm = MagicMock()
        llm.chat.side_effect = ["", ""]  # two empties
        tracker = StateTracker()

        with patch("fantoma.navigator.classify_blocker", return_value=None):
            result = nav.execute(subtask, fantoma, llm, tracker, max_steps=10)

        assert result.status == "failed"
        assert result.failure_reason == "llm_empty"
        # Both empty calls were made; we did not burn the full step budget
        assert llm.chat.call_count == 2
        assert result.steps_taken == 2

    def test_unparseable_responses_bail_out(self):
        """Garbage responses with no recognisable actions also count as empty."""
        nav = Navigator()
        subtask = Subtask("Click submit", "interact", "Form submitted")
        fantoma = self._make_fantoma()
        llm = MagicMock()
        llm.chat.side_effect = ["I am thinking about it", "Let me consider"]
        tracker = StateTracker()

        with patch("fantoma.navigator.classify_blocker", return_value=None):
            result = nav.execute(subtask, fantoma, llm, tracker, max_steps=10)

        assert result.status == "failed"
        assert result.failure_reason == "llm_empty"

    def test_empty_streak_resets_on_valid_action(self):
        """One empty followed by a valid action should not bail out."""
        nav = Navigator()
        subtask = Subtask("Click submit", "interact", "Form submitted")
        fantoma = self._make_fantoma()
        fantoma.click.return_value = {"success": True, "state": {"url": "https://example.com"}}
        llm = MagicMock()
        # empty, valid click, DONE
        llm.chat.side_effect = ["", "CLICK [0]", "DONE"]
        tracker = StateTracker()

        with patch("fantoma.navigator.aria_snapshot", return_value={}):
            with patch("fantoma.navigator.aria_diff", return_value=""):
                with patch("fantoma.navigator.classify_blocker", return_value=None):
                    result = nav.execute(subtask, fantoma, llm, tracker, max_steps=10)

        assert result.status == "done"
        assert result.failure_reason == ""
