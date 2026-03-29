"""Tests for tree diffing — marking new elements with * prefix."""
import pytest


class TestTreeDiff:
    def test_new_elements_marked(self):
        from fantoma.dom.accessibility import mark_new_elements
        previous = [
            {"role": "button", "name": "Submit"},
            {"role": "textbox", "name": "Email"},
        ]
        current = [
            {"role": "button", "name": "Submit"},
            {"role": "textbox", "name": "Email"},
            {"role": "textbox", "name": "Verification code"},
            {"role": "button", "name": "Verify"},
        ]
        new_flags = mark_new_elements(previous, current)
        assert new_flags == [False, False, True, True]

    def test_all_new_on_first_page(self):
        from fantoma.dom.accessibility import mark_new_elements
        current = [
            {"role": "textbox", "name": "Email"},
            {"role": "button", "name": "Next"},
        ]
        new_flags = mark_new_elements([], current)
        assert new_flags == [False, False]

    def test_no_new_elements(self):
        from fantoma.dom.accessibility import mark_new_elements
        previous = [
            {"role": "button", "name": "Submit"},
            {"role": "textbox", "name": "Email"},
        ]
        current = [
            {"role": "button", "name": "Submit"},
            {"role": "textbox", "name": "Email"},
        ]
        new_flags = mark_new_elements(previous, current)
        assert new_flags == [False, False]

    def test_all_new_elements(self):
        from fantoma.dom.accessibility import mark_new_elements
        previous = [
            {"role": "textbox", "name": "Email"},
        ]
        current = [
            {"role": "textbox", "name": "Verification code"},
            {"role": "button", "name": "Verify"},
        ]
        new_flags = mark_new_elements(previous, current)
        assert new_flags == [True, True]


class TestParseInteractiveHandlesStarPrefix:
    def test_parse_star_prefix(self):
        from fantoma.dom.accessibility import AccessibilityExtractor
        output = '*[0] textbox "Verification code"\n[1] button "Submit"\n*[2] button "Verify"'
        elements = AccessibilityExtractor._parse_interactive_from_output(output)
        assert len(elements) == 3
        assert elements[0]["role"] == "textbox"
        assert elements[0]["name"] == "Verification code"
        assert elements[1]["role"] == "button"
