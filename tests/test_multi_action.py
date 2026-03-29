"""Tests for multi-action parsing and page-change guards."""
from fantoma.action_parser import parse_actions


class TestParseActions:
    def test_single_action(self):
        actions = parse_actions('CLICK [3]')
        assert actions == ["CLICK [3]"]

    def test_multiple_actions_newline(self):
        actions = parse_actions('CLICK [3]\nTYPE [5] "hello"\nPRESS Enter')
        assert actions == ["CLICK [3]", 'TYPE [5] "hello"', "PRESS Enter"]

    def test_max_five_actions(self):
        raw = "\n".join([f"CLICK [{i}]" for i in range(10)])
        actions = parse_actions(raw)
        assert len(actions) == 5

    def test_max_actions_override(self):
        raw = "CLICK [1]\nCLICK [2]\nCLICK [3]"
        actions = parse_actions(raw, max_actions=1)
        assert len(actions) == 1

    def test_done_terminates(self):
        actions = parse_actions('CLICK [3]\nDONE')
        assert actions == ["CLICK [3]", "DONE"]

    def test_navigate_terminates_sequence(self):
        actions = parse_actions('NAVIGATE https://example.com\nCLICK [3]')
        assert actions == ["NAVIGATE https://example.com"]

    def test_empty_string(self):
        actions = parse_actions("")
        assert actions == []

    def test_strips_thinking_text(self):
        raw = "I should click the button\nCLICK [3]\nThen type\nTYPE [5] \"hello\""
        actions = parse_actions(raw)
        assert actions == ["CLICK [3]", 'TYPE [5] "hello"']
