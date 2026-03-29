"""Tests for action verification and inline error detection."""
import pytest
from unittest.mock import MagicMock, PropertyMock


def _make_page(url="https://example.com/login", inner_text="Welcome", elements=None):
    """Create a mock Playwright page."""
    page = MagicMock()
    type(page).url = PropertyMock(return_value=url)
    page.inner_text.return_value = inner_text
    page.evaluate.return_value = []
    page.query_selector_all.return_value = []
    return page


def _make_dom_extractor(elements=None):
    """Create a mock AccessibilityExtractor."""
    ext = MagicMock()
    ext._last_interactive = elements or []
    return ext


class TestVerifyAction:
    def test_url_changed(self):
        from fantoma.browser.page_state import verify_action
        page = _make_page(url="https://example.com/dashboard")
        result = verify_action(page, pre_url="https://example.com/login", pre_dom_hash="abc", dom_extractor=_make_dom_extractor())
        assert result["url_changed"] is True

    def test_url_unchanged(self):
        from fantoma.browser.page_state import verify_action
        page = _make_page(url="https://example.com/login")
        result = verify_action(page, pre_url="https://example.com/login", pre_dom_hash="abc", dom_extractor=_make_dom_extractor())
        assert result["url_changed"] is False

    def test_dom_changed(self):
        from fantoma.browser.page_state import verify_action
        page = _make_page(url="https://example.com/login")
        ext = _make_dom_extractor(elements=[{"role": "textbox", "name": "Code"}])
        result = verify_action(page, pre_url="https://example.com/login", pre_dom_hash="abc", dom_extractor=ext)
        assert result["new_elements"] == 1

    def test_error_found_in_page(self):
        from fantoma.browser.page_state import verify_action
        page = _make_page(url="https://example.com/login")
        page.evaluate.return_value = ["Invalid email address"]
        result = verify_action(page, pre_url="https://example.com/login", pre_dom_hash="abc", dom_extractor=_make_dom_extractor())
        assert result["error_found"] is not None
        assert "Invalid email" in result["error_found"]


class TestDetectErrors:
    def test_no_errors(self):
        from fantoma.browser.page_state import detect_errors
        page = _make_page()
        page.evaluate.return_value = []
        result = detect_errors(page)
        assert result == []

    def test_returns_error_strings(self):
        from fantoma.browser.page_state import detect_errors
        page = _make_page()
        page.evaluate.return_value = ["Invalid email address", "Password too short"]
        result = detect_errors(page)
        assert len(result) == 2
        assert "Invalid email address" in result

    def test_max_three_errors(self):
        from fantoma.browser.page_state import detect_errors
        page = _make_page()
        page.evaluate.return_value = ["Error 1", "Error 2", "Error 3", "Error 4", "Error 5"]
        result = detect_errors(page)
        assert len(result) == 3

    def test_handles_evaluate_failure(self):
        from fantoma.browser.page_state import detect_errors
        page = _make_page()
        page.evaluate.side_effect = Exception("Page crashed")
        result = detect_errors(page)
        assert result == []
