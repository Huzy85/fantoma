"""Tests for DOM element deduplication."""
import pytest


def _el(role, name, state=""):
    return {"role": role, "name": name, "state": state, "raw": {}}


class TestDedupElements:
    def test_removes_exact_duplicates(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("link", "Home"),
            _el("button", "Search"),
            _el("link", "Home"),  # duplicate
            _el("link", "Home"),  # duplicate
        ]
        result = dedup_elements(elements)
        assert len(result) == 2
        names = [e["name"] for e in result]
        assert names.count("Home") == 1

    def test_keeps_different_roles_same_name(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("link", "Login"),
            _el("button", "Login"),
        ]
        result = dedup_elements(elements)
        assert len(result) == 2

    def test_keeps_different_names_same_role(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("link", "Home"),
            _el("link", "About"),
        ]
        result = dedup_elements(elements)
        assert len(result) == 2

    def test_preserves_order_first_seen(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("link", "A"),
            _el("link", "B"),
            _el("link", "A"),  # duplicate
            _el("link", "C"),
        ]
        result = dedup_elements(elements)
        names = [e["name"] for e in result]
        assert names == ["A", "B", "C"]

    def test_empty_list(self):
        from fantoma.dom.accessibility import dedup_elements
        assert dedup_elements([]) == []

    def test_no_duplicates(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [_el("link", "A"), _el("button", "B"), _el("textbox", "C")]
        result = dedup_elements(elements)
        assert len(result) == 3

    def test_textbox_duplicates_kept_if_different_state(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("textbox", "Email", state=' (value: "user@test.com")'),
            _el("textbox", "Email", state=""),
        ]
        # Textboxes with the same name but different state are likely different fields
        # (e.g., login form vs search bar). Keep both.
        result = dedup_elements(elements)
        assert len(result) == 2
