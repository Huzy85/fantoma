# tests/test_state_tracker.py
import pytest
from fantoma.state_tracker import StateTracker


class TestFingerprint:
    def test_not_stagnant_with_different_content(self):
        t = StateTracker()
        t.add("https://a.com", "content one", "click({ID})")
        t.add("https://a.com", "content two", "click({ID})")
        t.add("https://a.com", "content three", "click({ID})")
        assert t.is_stagnant() is False

    def test_stagnant_after_3_identical(self):
        t = StateTracker()
        t.add("https://a.com", "same content", "click({ID})")
        t.add("https://a.com", "same content", "scroll(down)")
        t.add("https://a.com", "same content", "click({ID})")
        assert t.is_stagnant() is True

    def test_not_stagnant_with_fewer_than_3(self):
        t = StateTracker()
        t.add("https://a.com", "same", "click({ID})")
        t.add("https://a.com", "same", "click({ID})")
        assert t.is_stagnant() is False

    def test_url_change_breaks_stagnation(self):
        t = StateTracker()
        t.add("https://a.com", "same", "click({ID})")
        t.add("https://b.com", "same", "click({ID})")
        t.add("https://a.com", "same", "click({ID})")
        assert t.is_stagnant() is False


class TestCycleDetection:
    def test_not_cycling_with_varied_actions(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "click({'element_id': 1})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c3", "type_text({'element_id': 2, 'text': 'hello'})")
        t.add("https://a.com", "c4", "click({'element_id': 3})")
        assert t.is_cycling() is False

    def test_cycling_same_action_4_times(self):
        t = StateTracker()
        for i in range(4):
            t.add("https://a.com", f"c{i}", "click({'element_id': 5}) -> OK")
        assert t.is_cycling() is True

    def test_cycling_alternating_pattern(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'}) -> OK")
        t.add("https://a.com", "c2", "click({'element_id': 3}) -> OK")
        t.add("https://a.com", "c3", "scroll({'direction': 'down'}) -> OK")
        t.add("https://a.com", "c4", "click({'element_id': 7}) -> FAILED")
        assert t.is_cycling() is True

    def test_normalises_element_ids(self):
        """Different element IDs for same action type should still detect cycles."""
        t = StateTracker()
        t.add("https://a.com", "c1", "click({'element_id': 1}) -> OK")
        t.add("https://a.com", "c2", "click({'element_id': 5}) -> OK")
        t.add("https://a.com", "c3", "click({'element_id': 9}) -> FAILED")
        t.add("https://a.com", "c4", "click({'element_id': 2}) -> OK")
        assert t.is_cycling() is True

    def test_not_cycling_with_fewer_than_4(self):
        t = StateTracker()
        for i in range(3):
            t.add("https://a.com", f"c{i}", "click({'element_id': 1})")
        assert t.is_cycling() is False


class TestScrollLimit:
    def test_no_limit_under_3(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is False

    def test_stale_limit_at_3(self):
        """3 stale scrolls (same DOM) triggers limit."""
        t = StateTracker()
        t.add("https://a.com", "same", "scroll({'direction': 'down'})")
        t.add("https://a.com", "same", "scroll({'direction': 'down'})")
        t.add("https://a.com", "same", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is True

    def test_productive_scrolls_no_limit_at_3(self):
        """3 productive scrolls (different DOM each time) should NOT trigger limit."""
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c3", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is False

    def test_hard_limit_at_5(self):
        """5 total scrolls on same URL always triggers, even if productive."""
        t = StateTracker()
        for i in range(5):
            t.add("https://a.com", f"content_{i}", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is True

    def test_non_scroll_resets_counter(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c3", "click({'element_id': 1})")
        t.add("https://a.com", "c4", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is False

    def test_url_change_resets_counter(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        t.add("https://b.com", "c3", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is False


class TestShouldStop:
    def test_returns_scroll_limit_reason(self):
        """3 stale scrolls (same DOM) triggers scroll_limit."""
        t = StateTracker()
        for i in range(3):
            t.add("https://a.com", "same", "scroll({'direction': 'down'})")
        stop, reason = t.should_stop()
        assert stop is True
        assert reason == "scroll_limit"

    def test_no_stop_when_healthy(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "click({'element_id': 1})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        stop, reason = t.should_stop()
        assert stop is False
        assert reason == ""


class TestReset:
    def test_reset_clears_all(self):
        t = StateTracker()
        for i in range(4):
            t.add("https://a.com", "same", "click({'element_id': 1})")
        assert t.is_cycling() is True
        t.reset()
        assert t.is_cycling() is False
        assert t.is_stagnant() is False
        assert t.scroll_limit_hit() is False
