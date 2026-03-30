"""Tests for structured LLM action output — JSON schema parsing."""
import json
import pytest


class TestActionSchema:
    def test_schema_is_valid_json_schema(self):
        from fantoma.llm.structured import ACTION_SCHEMA
        assert "type" in ACTION_SCHEMA
        assert ACTION_SCHEMA["type"] == "object"
        assert "actions" in ACTION_SCHEMA["properties"]

    def test_schema_has_required_fields(self):
        from fantoma.llm.structured import ACTION_SCHEMA
        assert "required" in ACTION_SCHEMA
        assert "actions" in ACTION_SCHEMA["required"]


class TestParseStructured:
    def test_parse_click(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "CLICK", "index": 3}]}'
        result = parse_structured(raw)
        assert result == ["CLICK [3]"]

    def test_parse_type(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "TYPE", "index": 0, "text": "hello@test.com"}]}'
        result = parse_structured(raw)
        assert result == ['TYPE [0] "hello@test.com"']

    def test_parse_multiple_actions(self):
        from fantoma.llm.structured import parse_structured
        raw = json.dumps({"actions": [
            {"action": "TYPE", "index": 1, "text": "user@test.com"},
            {"action": "TYPE", "index": 2, "text": "password123"},
            {"action": "CLICK", "index": 3},
        ]})
        result = parse_structured(raw)
        assert len(result) == 3
        assert result[0] == 'TYPE [1] "user@test.com"'
        assert result[2] == "CLICK [3]"

    def test_parse_navigate(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "NAVIGATE", "url": "https://example.com"}]}'
        result = parse_structured(raw)
        assert result == ["NAVIGATE https://example.com"]

    def test_parse_scroll(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "SCROLL", "direction": "down"}]}'
        result = parse_structured(raw)
        assert result == ["SCROLL down"]

    def test_parse_done(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "DONE"}]}'
        result = parse_structured(raw)
        assert result == ["DONE"]

    def test_parse_press(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "PRESS", "key": "Enter"}]}'
        result = parse_structured(raw)
        assert result == ["PRESS Enter"]

    def test_invalid_json_returns_none(self):
        from fantoma.llm.structured import parse_structured
        result = parse_structured("not json at all")
        assert result is None

    def test_missing_actions_key_returns_none(self):
        from fantoma.llm.structured import parse_structured
        result = parse_structured('{"thinking": "hmm"}')
        assert result is None

    def test_caps_at_five_actions(self):
        from fantoma.llm.structured import parse_structured
        raw = json.dumps({"actions": [{"action": "SCROLL", "direction": "down"}] * 10})
        result = parse_structured(raw)
        assert len(result) == 5

    def test_stops_at_done(self):
        from fantoma.llm.structured import parse_structured
        raw = json.dumps({"actions": [
            {"action": "CLICK", "index": 1},
            {"action": "DONE"},
            {"action": "CLICK", "index": 2},
        ]})
        result = parse_structured(raw)
        assert len(result) == 2
        assert result[1] == "DONE"
