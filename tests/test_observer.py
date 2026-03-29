"""Tests for MutationObserver injection and mutation collection."""
import pytest
from unittest.mock import MagicMock


class TestInjectObserver:
    def test_calls_evaluate(self):
        from fantoma.browser.observer import inject_observer
        page = MagicMock()
        inject_observer(page)
        page.evaluate.assert_called_once()
        js_code = page.evaluate.call_args[0][0]
        assert "__fantoma_mutations" in js_code

    def test_handles_evaluate_failure(self):
        from fantoma.browser.observer import inject_observer
        page = MagicMock()
        page.evaluate.side_effect = Exception("Detached")
        inject_observer(page)  # should not raise


class TestCollectMutations:
    def test_returns_empty_when_no_mutations(self):
        from fantoma.browser.observer import collect_mutations
        page = MagicMock()
        page.evaluate.return_value = {"added": [], "removed": [], "changed_attrs": [], "text_changes": []}
        result = collect_mutations(page)
        assert result["added"] == []
        assert result["text_changes"] == []

    def test_returns_mutation_summary(self):
        from fantoma.browser.observer import collect_mutations
        page = MagicMock()
        page.evaluate.return_value = {
            "added": ["div.error-message", "span.alert"],
            "removed": ["div.loading-spinner"],
            "changed_attrs": [{"element": "input#email", "attr": "aria-invalid", "value": "true"}],
            "text_changes": ["Error: Invalid email"],
        }
        result = collect_mutations(page)
        assert len(result["added"]) == 2
        assert "div.error-message" in result["added"]
        assert len(result["text_changes"]) == 1

    def test_handles_evaluate_failure(self):
        from fantoma.browser.observer import collect_mutations
        page = MagicMock()
        page.evaluate.side_effect = Exception("Page navigated")
        result = collect_mutations(page)
        assert result["added"] == []
        assert result["removed"] == []

    def test_caps_results(self):
        from fantoma.browser.observer import collect_mutations
        page = MagicMock()
        page.evaluate.return_value = {
            "added": [f"div.item-{i}" for i in range(50)],
            "removed": [],
            "changed_attrs": [],
            "text_changes": [f"Text {i}" for i in range(20)],
        }
        result = collect_mutations(page)
        assert len(result["added"]) <= 10
        assert len(result["text_changes"]) <= 5


class TestFormatMutations:
    def test_empty_mutations(self):
        from fantoma.browser.observer import format_mutations
        result = format_mutations({"added": [], "removed": [], "changed_attrs": [], "text_changes": []})
        assert result == ""

    def test_formats_added_and_text(self):
        from fantoma.browser.observer import format_mutations
        result = format_mutations({
            "added": ["div.error"],
            "removed": [],
            "changed_attrs": [],
            "text_changes": ["Invalid email"],
        })
        assert "Added: div.error" in result
        assert "New text: Invalid email" in result
