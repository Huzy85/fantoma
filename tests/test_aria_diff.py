"""Tests for fantoma/dom/aria_diff.py"""
import pytest
from unittest.mock import MagicMock, patch
from fantoma.dom.aria_diff import _parse_line, aria_snapshot, aria_diff


class TestParseLine:
    def test_button(self):
        r = _parse_line('  - button "Submit"')
        assert r == ("button", "Submit", "")

    def test_textbox_with_value(self):
        r = _parse_line('  - textbox "Email" [value="user@example.com"]')
        assert r == ("textbox", "Email", "user@example.com")

    def test_checkbox_checked(self):
        r = _parse_line('  - checkbox "Remember me" [checked]')
        assert r == ("checkbox", "Remember me", "checked")

    def test_disabled_button(self):
        r = _parse_line('  - button "Next" [disabled]')
        assert r == ("button", "Next", "disabled")

    def test_heading_skipped(self):
        # heading is not in _TRACKED
        assert _parse_line('  - heading "Welcome"') is None

    def test_blank_line(self):
        assert _parse_line("") is None

    def test_link(self):
        r = _parse_line('- link "Click here"')
        assert r == ("link", "Click here", "")

    def test_alert(self):
        r = _parse_line('- alert "Error: required field"')
        assert r == ("alert", "Error: required field", "")


class TestAriaSnapshot:
    def test_returns_dict(self):
        page = MagicMock()
        page.locator.return_value.aria_snapshot.return_value = (
            '- button "Submit"\n- textbox "Email" [value="test@example.com"]'
        )
        result = aria_snapshot(page)
        assert ("button", "Submit") in result
        assert ("textbox", "Email") in result
        assert result[("textbox", "Email")] == "test@example.com"

    def test_page_error_returns_empty(self):
        page = MagicMock()
        page.locator.return_value.aria_snapshot.side_effect = Exception("page closed")
        assert aria_snapshot(page) == {}

    def test_first_occurrence_wins(self):
        # Same role+name appearing twice — first value kept
        page = MagicMock()
        page.locator.return_value.aria_snapshot.return_value = (
            '- button "Submit"\n- button "Submit" [disabled]'
        )
        result = aria_snapshot(page)
        assert result[("button", "Submit")] == ""  # first has no value


class TestAriaDiff:
    def test_added_element(self):
        before = {}
        after = {("button", "Submit"): ""}
        diff = aria_diff(before, after)
        assert '+ [button]' in diff
        assert "Submit" in diff

    def test_removed_element(self):
        before = {("alert", "Error"): ""}
        after = {}
        diff = aria_diff(before, after)
        assert '- [alert]' in diff

    def test_value_changed(self):
        before = {("textbox", "Email"): ""}
        after  = {("textbox", "Email"): "user@example.com"}
        diff = aria_diff(before, after)
        assert '~ [textbox]' in diff
        assert "user@example.com" in diff

    def test_no_change_returns_empty(self):
        snap = {("button", "Submit"): "", ("textbox", "Email"): ""}
        diff = aria_diff(snap, snap)
        assert diff == ""

    def test_capped_at_8_lines(self):
        before = {}
        after = {("button", f"Btn{i}"): "" for i in range(20)}
        diff = aria_diff(before, after)
        assert len(diff.strip().split("\n")) <= 8

    def test_empty_dicts(self):
        assert aria_diff({}, {}) == ""


class TestStateChangesAreVisible:
    def _snap(self, text):
        from unittest.mock import MagicMock
        from fantoma.dom.aria_diff import aria_snapshot
        page = MagicMock()
        page.locator.return_value.aria_snapshot.return_value = text
        return aria_snapshot(page)

    def test_a_select_is_reported(self):
        from fantoma.dom.aria_diff import aria_diff
        before = self._snap('- combobox:\n  - option "Pick" [disabled] [selected]\n  - option "Option 2"')
        after = self._snap('- combobox:\n  - option "Pick" [disabled]\n  - option "Option 2" [selected]')
        diff = aria_diff(before, after)
        assert '"Option 2": "" → "selected"' in diff
        assert '"Pick": "selected disabled" → "disabled"' in diff

    def test_second_of_two_unnamed_checkboxes_is_reported(self):
        from fantoma.dom.aria_diff import aria_diff
        before = self._snap("- checkbox\n- checkbox [checked]")
        after = self._snap("- checkbox\n- checkbox")
        assert "#2" in aria_diff(before, after)

    def test_filled_unnamed_field_is_reported(self):
        from fantoma.dom.aria_diff import aria_diff
        before = self._snap("- spinbutton")
        after = self._snap('- spinbutton: "42"')
        assert '"42"' in aria_diff(before, after)
