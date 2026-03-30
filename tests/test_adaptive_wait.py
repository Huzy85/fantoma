"""Tests for adaptive DOM stability wait."""
import pytest
from unittest.mock import MagicMock, patch


class TestWaitForDomStable:
    def test_calls_evaluate_with_js(self):
        from fantoma.browser.observer import wait_for_dom_stable
        page = MagicMock()
        page.evaluate.return_value = True  # DOM was stable
        result = wait_for_dom_stable(page, timeout=5000, debounce=300)
        assert result is True
        page.evaluate.assert_called_once()
        js = page.evaluate.call_args[0][0]
        assert "MutationObserver" in js

    def test_returns_false_on_timeout(self):
        from fantoma.browser.observer import wait_for_dom_stable
        page = MagicMock()
        page.evaluate.return_value = False  # Timed out
        result = wait_for_dom_stable(page, timeout=1000, debounce=300)
        assert result is False

    def test_returns_true_on_exception(self):
        from fantoma.browser.observer import wait_for_dom_stable
        page = MagicMock()
        page.evaluate.side_effect = Exception("Page navigated")
        # On exception, assume page changed (navigation) — return True
        result = wait_for_dom_stable(page, timeout=5000)
        assert result is True

    def test_default_params(self):
        from fantoma.browser.observer import wait_for_dom_stable
        page = MagicMock()
        page.evaluate.return_value = True
        wait_for_dom_stable(page)
        js = page.evaluate.call_args[0][0]
        # Should contain the timeout and debounce values
        assert "5000" in js or "timeout" in js.lower()
