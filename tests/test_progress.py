"""Tests for task intent inference and progress assessment."""
import pytest
from unittest.mock import MagicMock, PropertyMock


def _make_page(url="https://example.com/login", inner_text="Welcome", body_len=100):
    """Create a mock Playwright page."""
    page = MagicMock()
    type(page).url = PropertyMock(return_value=url)
    text = inner_text if len(inner_text) > body_len else inner_text + " " * (body_len - len(inner_text))
    page.inner_text.return_value = text
    page.evaluate.return_value = ""
    return page


def _make_dom_extractor(elements=None):
    ext = MagicMock()
    ext._last_interactive = elements or []
    return ext


# ── _infer_task_intent ──────────────────────────────────────────

class TestInferTaskIntent:
    def test_auth_login(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Log in to the website") == "auth"

    def test_auth_sign_in(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Sign in with Google") == "auth"

    def test_auth_signin_no_space(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Go to signin page") == "auth"

    def test_auth_authenticate(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Authenticate with the API") == "auth"

    def test_extract_scrape(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Scrape the product prices") == "extract"

    def test_extract_read(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Read the article content") == "extract"

    def test_extract_copy(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Copy the table data") == "extract"

    def test_extract_get(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Get the price from the page") == "extract"

    def test_navigate_go_to(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Go to https://example.com") == "navigate"

    def test_navigate_visit(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Visit the homepage") == "navigate"

    def test_navigate_open(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Open the settings page") == "navigate"

    def test_navigate_navigate(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Navigate to the dashboard") == "navigate"

    def test_no_match(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("Click the submit button") is None

    def test_case_insensitive(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("LOGIN to my account") == "auth"


# ── assess_progress ─────────────────────────────────────────────

class TestAssessProgressActionLevel:
    """Layer 1: action-level verification."""

    def test_type_action_ok_when_value_present(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        page.evaluate.return_value = "hello@test.com"
        result = assess_progress(page, "TYPE [1] 'hello@test.com'", "Login", _make_dom_extractor())
        assert result["action_ok"] is True

    def test_type_action_fail_when_value_missing(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        page.evaluate.return_value = ""
        result = assess_progress(page, "TYPE [1] 'hello@test.com'", "Login", _make_dom_extractor())
        assert result["action_ok"] is False

    def test_click_submit_button_url_changed(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/dashboard")
        elem = {"role": "button", "name": "Submit"}
        result = assess_progress(page, "CLICK [5]", "Login", _make_dom_extractor(),
                                 pre_url="https://example.com/login", action_element=elem)
        assert result["action_ok"] is True

    def test_click_submit_button_url_not_changed(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/login")
        elem = {"role": "button", "name": "Sign in"}
        result = assess_progress(page, "CLICK [5]", "Login", _make_dom_extractor(),
                                 pre_url="https://example.com/login", action_element=elem)
        assert result["action_ok"] is False

    def test_click_link_url_changed(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/about")
        elem = {"role": "link", "name": "About Us"}
        result = assess_progress(page, "CLICK [3]", "Visit about", _make_dom_extractor(),
                                 pre_url="https://example.com/", action_element=elem)
        assert result["action_ok"] is True

    def test_click_link_url_not_changed(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/")
        elem = {"role": "link", "name": "About Us"}
        result = assess_progress(page, "CLICK [3]", "Visit about", _make_dom_extractor(),
                                 pre_url="https://example.com/", action_element=elem)
        assert result["action_ok"] is False

    def test_select_action_ok(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        page.evaluate.return_value = "United Kingdom"
        result = assess_progress(page, "SELECT [2] 'United Kingdom'", "Fill form", _make_dom_extractor())
        assert result["action_ok"] is True

    def test_select_action_fail(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        page.evaluate.return_value = ""
        result = assess_progress(page, "SELECT [2] 'United Kingdom'", "Fill form", _make_dom_extractor())
        assert result["action_ok"] is False

    def test_scroll_always_ok(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        result = assess_progress(page, "SCROLL down", "Browse page", _make_dom_extractor())
        assert result["action_ok"] is True

    def test_wait_always_ok(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        result = assess_progress(page, "WAIT 2", "Browse page", _make_dom_extractor())
        assert result["action_ok"] is True

    def test_click_submit_names(self):
        """Submit-like button names should trigger URL change check."""
        from fantoma.browser.page_state import assess_progress
        submit_names = ["Submit", "Sign in", "Login", "Log in", "Send", "Confirm", "Register"]
        for name in submit_names:
            page = _make_page(url="https://example.com/login")
            elem = {"role": "button", "name": name}
            result = assess_progress(page, "CLICK [1]", "Login", _make_dom_extractor(),
                                     pre_url="https://example.com/login", action_element=elem)
            assert result["action_ok"] is False, f"Expected action_ok=False for submit button '{name}' with no URL change"


class TestAssessProgressTaskLevel:
    """Layer 2: task-level progress."""

    def test_auth_progress_url_left_login(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/dashboard")
        result = assess_progress(page, "CLICK [5]", "Log in to the site", _make_dom_extractor(),
                                 pre_url="https://example.com/login")
        assert result["progress_ok"] is True

    def test_auth_progress_url_still_login(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/login")
        result = assess_progress(page, "CLICK [5]", "Log in to the site", _make_dom_extractor(),
                                 pre_url="https://example.com/login")
        assert result["progress_ok"] is False

    def test_auth_progress_signin_variant(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/home")
        result = assess_progress(page, "CLICK [5]", "Sign in to account", _make_dom_extractor(),
                                 pre_url="https://example.com/signin")
        assert result["progress_ok"] is True

    def test_extract_progress_long_body(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/article", body_len=300)
        result = assess_progress(page, "SCROLL down", "Extract the article text", _make_dom_extractor())
        assert result["progress_ok"] is True

    def test_extract_progress_short_body(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/article", inner_text="Short", body_len=50)
        result = assess_progress(page, "SCROLL down", "Scrape the data", _make_dom_extractor())
        assert result["progress_ok"] is False

    def test_navigate_progress_url_changed(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/target")
        result = assess_progress(page, "CLICK [1]", "Go to the target page", _make_dom_extractor(),
                                 pre_url="https://example.com/home")
        assert result["progress_ok"] is True

    def test_navigate_progress_url_same(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/home")
        result = assess_progress(page, "CLICK [1]", "Visit the target page", _make_dom_extractor(),
                                 pre_url="https://example.com/home")
        assert result["progress_ok"] is False

    def test_no_intent_progress_none(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        result = assess_progress(page, "CLICK [1]", "Click the submit button", _make_dom_extractor())
        assert result["progress_ok"] is None

    def test_result_has_reason(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        result = assess_progress(page, "SCROLL down", "Browse page", _make_dom_extractor())
        assert "reason" in result
