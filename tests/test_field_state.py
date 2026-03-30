"""Tests for inline field state — validation context in ARIA output."""
import pytest


class TestParseAriaLineWithState:
    def test_invalid_attribute_parsed(self):
        from fantoma.dom.accessibility import _parse_aria_line
        result = _parse_aria_line('textbox "Email" [invalid]')
        assert result is not None
        assert result.get("invalid") is True

    def test_required_attribute_parsed(self):
        from fantoma.dom.accessibility import _parse_aria_line
        result = _parse_aria_line('textbox "Email" [required]')
        assert result is not None
        assert result.get("required") is True

    def test_value_attribute_parsed(self):
        from fantoma.dom.accessibility import _parse_aria_line
        result = _parse_aria_line('textbox "Email" [value="user@test.com"]')
        assert result is not None
        assert result.get("value") == "user@test.com"


class TestEnrichFieldState:
    def test_invalid_field_shows_state(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "textbox", "name": "Email", "state": "", "raw": {"invalid": True}}
        result = enrich_field_state(el)
        assert "[invalid]" in result

    def test_required_field_shows_state(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "textbox", "name": "Email", "state": "", "raw": {"required": True}}
        result = enrich_field_state(el)
        assert "[required]" in result

    def test_invalid_with_description_shows_error(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "textbox", "name": "Email", "state": "",
              "raw": {"invalid": True}, "_error": "Please enter a valid email"}
        result = enrich_field_state(el)
        assert "invalid" in result
        assert "Please enter a valid email" in result

    def test_no_state_returns_empty(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "button", "name": "Submit", "state": "", "raw": {}}
        result = enrich_field_state(el)
        assert result == ""

    def test_value_shown_for_filled_field(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "textbox", "name": "Email", "state": "",
              "raw": {"value": "user@test.com"}}
        result = enrich_field_state(el)
        assert "user@test.com" in result
