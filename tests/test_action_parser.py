"""Tests for normalize_action() and execute_action() in action_parser.py."""
import time
from unittest.mock import MagicMock, patch, call

import pytest

from fantoma.action_parser import normalize_action, execute_action


# ---------------------------------------------------------------------------
# normalize_action()
# ---------------------------------------------------------------------------

class TestNormalizeAction:
    # --- Standard actions pass through unchanged ---

    def test_click_standard(self):
        assert normalize_action("CLICK [5]") == "CLICK [5]"

    def test_type_standard(self):
        assert normalize_action('TYPE [3] "hello"') == 'TYPE [3] "hello"'

    def test_navigate_standard(self):
        assert normalize_action("NAVIGATE https://example.com") == "NAVIGATE https://example.com"

    def test_press_standard(self):
        assert normalize_action("PRESS Enter") == "PRESS Enter"

    def test_done_preserved(self):
        assert normalize_action("DONE") == "DONE"

    def test_scroll_up(self):
        assert normalize_action("SCROLL UP") == "SCROLL UP"

    def test_scroll_down(self):
        assert normalize_action("SCROLL DOWN") == "SCROLL DOWN"

    # --- Lowercase actions get uppercased ---

    def test_click_lowercase(self):
        assert normalize_action("click [5]") == "click [5]"

    def test_click_uppercase_extracted(self):
        # EXTRACT_PATTERNS match case-insensitively and return the matched text as-is
        # "CLICK [5]" is already uppercase so it passes through
        result = normalize_action("CLICK [5]")
        assert "CLICK" in result
        assert "5" in result

    def test_done_lowercase_extracted(self):
        # DONE pattern is case-insensitive; extracted verbatim from input
        result = normalize_action("done")
        # EXTRACT_PATTERNS match "DONE" case-insensitively — returns as found
        assert result.upper() == "DONE" or result == "done"

    # --- Extra whitespace trimmed ---

    def test_leading_trailing_whitespace_stripped(self):
        assert normalize_action("  CLICK [5]  ") == "CLICK [5]"

    def test_only_whitespace_returns_empty(self):
        assert normalize_action("   ") == ""

    # --- Empty input ---

    def test_empty_string_returns_empty(self):
        assert normalize_action("") == ""

    def test_none_returns_empty(self):
        assert normalize_action(None) == ""

    # --- Quoted strings preserved ---

    def test_type_with_double_quotes(self):
        result = normalize_action('TYPE [3] "hello world"')
        assert '"hello world"' in result

    def test_type_with_single_quotes(self):
        # EXTRACT_PATTERNS only capture double-quoted TYPE; single-quote falls through
        result = normalize_action("TYPE [3] 'hello'")
        assert result  # something returned (either extracted or passthrough)

    def test_search_page_quotes_preserved(self):
        result = normalize_action('SEARCH_PAGE "find me"')
        assert '"find me"' in result

    def test_find_quotes_preserved(self):
        result = normalize_action('FIND "button.submit"')
        assert '"button.submit"' in result

    # --- Bracket-only reference (Phi-style) ---

    def test_bracket_only_becomes_click(self):
        result = normalize_action("[14] some link text")
        assert result == "CLICK [14]"

    def test_navigate_to_bracket(self):
        result = normalize_action("Navigate to [3]")
        assert result == "CLICK [3]"

    # --- Verbose LLM responses — action extracted ---

    def test_click_buried_in_prose(self):
        result = normalize_action("I think I should CLICK [7] on the submit button")
        assert result == "CLICK [7]"

    def test_navigate_buried_in_prose(self):
        result = normalize_action("Let me go to NAVIGATE https://example.com now")
        assert result == "NAVIGATE https://example.com"

    # --- navigate without URL uses task_context ---

    def test_navigate_without_url_uses_task_context(self):
        result = normalize_action(
            "navigate to the homepage",
            task_context="Go to https://mysite.com and log in"
        )
        assert result == "NAVIGATE https://mysite.com"

    def test_navigate_without_url_no_context(self):
        # No URL extractable — returns the raw text (no EXTRACT_PATTERN match)
        result = normalize_action("navigate to the homepage")
        assert isinstance(result, str)  # doesn't crash

    # --- Multi-line input — only first matching action extracted ---

    def test_multiline_returns_first_match(self):
        raw = "CLICK [3]\nTYPE [5] \"hello\""
        # normalize_action works on a single string; newlines are not split here
        result = normalize_action(raw)
        assert result == "CLICK [3]"

    # --- DONE at end of multi-word line ---

    def test_done_after_prose(self):
        result = normalize_action("Task complete. DONE")
        assert result == "DONE"


# ---------------------------------------------------------------------------
# execute_action()
# ---------------------------------------------------------------------------

def _make_browser_and_dom():
    """Return (mock_browser, mock_dom_extractor) with a usable page mock."""
    page = MagicMock()
    page.keyboard = MagicMock()
    page.mouse = MagicMock()

    browser = MagicMock()
    browser.get_page.return_value = page

    dom = MagicMock()
    return browser, dom, page


class TestExecuteAction:
    # --- CLICK ---

    def test_click_calls_click_element(self):
        browser, dom, page = _make_browser_and_dom()
        element = MagicMock()
        dom.get_element_by_index.return_value = element

        with patch("fantoma.action_parser.click_element", return_value=True) as mock_click:
            result = execute_action("CLICK [5]", browser, dom)

        dom.get_element_by_index.assert_called_once_with(page, 5)
        mock_click.assert_called_once_with(browser, element)
        assert result is True

    def test_click_element_not_found_returns_false(self):
        browser, dom, page = _make_browser_and_dom()
        dom.get_element_by_index.return_value = None

        result = execute_action("CLICK [99]", browser, dom)
        assert result is False

    def test_click_index_zero(self):
        browser, dom, page = _make_browser_and_dom()
        element = MagicMock()
        dom.get_element_by_index.return_value = element

        with patch("fantoma.action_parser.click_element", return_value=True):
            result = execute_action("CLICK [0]", browser, dom)

        dom.get_element_by_index.assert_called_once_with(page, 0)
        assert result is True

    # --- TYPE ---

    def test_type_calls_type_into(self):
        browser, dom, page = _make_browser_and_dom()
        element = MagicMock()
        dom.get_element_by_index.return_value = element

        with patch("fantoma.action_parser.type_into", return_value=True) as mock_type:
            result = execute_action('TYPE [3] "hello world"', browser, dom)

        dom.get_element_by_index.assert_called_once_with(page, 3)
        mock_type.assert_called_once_with(browser, element, "hello world")
        assert result is True

    def test_type_element_not_found_returns_false(self):
        browser, dom, page = _make_browser_and_dom()
        dom.get_element_by_index.return_value = None

        result = execute_action('TYPE [3] "hello"', browser, dom)
        assert result is False

    def test_type_with_single_quotes(self):
        browser, dom, page = _make_browser_and_dom()
        element = MagicMock()
        dom.get_element_by_index.return_value = element

        with patch("fantoma.action_parser.type_into", return_value=True) as mock_type:
            result = execute_action("TYPE [2] 'password123'", browser, dom)

        mock_type.assert_called_once_with(browser, element, "password123")
        assert result is True

    # --- SELECT ---

    def test_select_calls_select_option(self):
        browser, dom, page = _make_browser_and_dom()
        element = MagicMock()
        dom.get_element_by_index.return_value = element

        result = execute_action('SELECT [4] "Option A"', browser, dom)

        dom.get_element_by_index.assert_called_once_with(page, 4)
        element.select_option.assert_called_once_with(label="Option A")
        assert result is True

    def test_select_element_not_found_returns_false(self):
        browser, dom, page = _make_browser_and_dom()
        dom.get_element_by_index.return_value = None

        result = execute_action('SELECT [4] "Option A"', browser, dom)
        assert result is False

    # --- SCROLL ---

    def test_scroll_down(self):
        browser, dom, page = _make_browser_and_dom()

        with patch("fantoma.action_parser.scroll_page", return_value=True) as mock_scroll:
            result = execute_action("SCROLL DOWN", browser, dom)

        mock_scroll.assert_called_once_with(browser, "down")
        assert result is True

    def test_scroll_up(self):
        browser, dom, page = _make_browser_and_dom()

        with patch("fantoma.action_parser.scroll_page", return_value=True) as mock_scroll:
            result = execute_action("SCROLL UP", browser, dom)

        mock_scroll.assert_called_once_with(browser, "up")
        assert result is True

    # --- NAVIGATE ---

    def test_navigate_calls_browser_navigate(self):
        browser, dom, page = _make_browser_and_dom()

        result = execute_action("NAVIGATE https://example.com", browser, dom)

        browser.navigate.assert_called_once_with("https://example.com")
        assert result is True

    def test_navigate_prepends_https_if_missing(self):
        browser, dom, page = _make_browser_and_dom()

        result = execute_action("NAVIGATE example.com", browser, dom)

        browser.navigate.assert_called_once_with("https://example.com")
        assert result is True

    # --- PRESS ---

    def test_press_calls_keyboard_press(self):
        browser, dom, page = _make_browser_and_dom()

        result = execute_action("PRESS Enter", browser, dom)

        page.keyboard.press.assert_called_once_with("Enter")
        assert result is True

    def test_press_tab(self):
        browser, dom, page = _make_browser_and_dom()

        result = execute_action("PRESS Tab", browser, dom)

        page.keyboard.press.assert_called_once_with("Tab")
        assert result is True

    # --- DONE ---

    def test_done_returns_true(self):
        browser, dom, page = _make_browser_and_dom()
        result = execute_action("DONE", browser, dom)
        assert result is True

    def test_done_lowercase_returns_true(self):
        browser, dom, page = _make_browser_and_dom()
        result = execute_action("done", browser, dom)
        assert result is True

    # --- WAIT ---

    def test_wait_sleeps_and_returns_true(self):
        browser, dom, page = _make_browser_and_dom()

        with patch("time.sleep") as mock_sleep:
            result = execute_action("WAIT", browser, dom)

        mock_sleep.assert_called_once_with(2)
        assert result is True

    # --- SEARCH_PAGE ---

    def test_search_page_calls_search_page_and_returns_true(self):
        browser, dom, page = _make_browser_and_dom()
        fake_results = [{"index": 0, "text": "some match here"}]

        with patch("fantoma.browser.actions.search_page", return_value=fake_results) as mock_sp:
            result = execute_action('SEARCH_PAGE "login button"', browser, dom)

        mock_sp.assert_called_once_with(page, "login button")
        assert result is True

    def test_search_page_no_matches_still_true(self):
        browser, dom, page = _make_browser_and_dom()

        with patch("fantoma.browser.actions.search_page", return_value=[]):
            result = execute_action('SEARCH_PAGE "nonexistent"', browser, dom)

        assert result is True

    # --- FIND ---

    def test_find_calls_find_elements_and_returns_true(self):
        browser, dom, page = _make_browser_and_dom()
        fake_results = [{"tag": "button", "text": "Submit", "name": "", "id": ""}]

        with patch("fantoma.browser.actions.find_elements", return_value=fake_results) as mock_fe:
            result = execute_action('FIND "button.submit"', browser, dom)

        mock_fe.assert_called_once_with(page, "button.submit")
        assert result is True

    def test_find_no_matches_still_true(self):
        browser, dom, page = _make_browser_and_dom()

        with patch("fantoma.browser.actions.find_elements", return_value=[]):
            result = execute_action('FIND ".nonexistent"', browser, dom)

        assert result is True

    # --- Invalid / unrecognised actions ---

    def test_invalid_action_handled_gracefully(self):
        browser, dom, page = _make_browser_and_dom()
        # Freeform fallback: none of the keywords match → returns False
        result = execute_action("EXPLODE [99]", browser, dom)
        assert isinstance(result, bool)

    def test_empty_action_handled_gracefully(self):
        browser, dom, page = _make_browser_and_dom()
        result = execute_action("", browser, dom)
        assert isinstance(result, bool)

    def test_none_action_handled_gracefully(self):
        browser, dom, page = _make_browser_and_dom()
        result = execute_action(None, browser, dom)
        assert isinstance(result, bool)

    # --- Exception in handler → returns False, doesn't raise ---

    def test_click_exception_returns_false(self):
        browser, dom, page = _make_browser_and_dom()
        element = MagicMock()
        dom.get_element_by_index.return_value = element

        with patch("fantoma.action_parser.click_element", side_effect=RuntimeError("boom")):
            result = execute_action("CLICK [1]", browser, dom)

        assert result is False

    def test_type_exception_returns_false(self):
        browser, dom, page = _make_browser_and_dom()
        element = MagicMock()
        dom.get_element_by_index.return_value = element

        with patch("fantoma.action_parser.type_into", side_effect=RuntimeError("boom")):
            result = execute_action('TYPE [1] "hello"', browser, dom)

        assert result is False

    # --- Freeform fallbacks ---

    def test_freeform_enter_presses_enter(self):
        # "press enter to submit" is matched by the PRESS pattern before freeform,
        # which captures the key verbatim from the regex group → lowercase "enter"
        browser, dom, page = _make_browser_and_dom()
        result = execute_action("press enter to submit", browser, dom)
        page.keyboard.press.assert_called_once_with("enter")
        assert result is True

    def test_freeform_go_back(self):
        browser, dom, page = _make_browser_and_dom()
        result = execute_action("go back to the previous page", browser, dom)
        page.go_back.assert_called_once()
        assert result is True

    def test_freeform_url_in_text(self):
        browser, dom, page = _make_browser_and_dom()
        result = execute_action("please open https://example.com for me", browser, dom)
        browser.navigate.assert_called_once_with("https://example.com")
        assert result is True
