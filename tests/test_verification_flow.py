"""Unit tests for Agent._get_verification() and Agent._enter_verification_code().

Covers all tiers of the verification flow (IMAP → callback → terminal → None)
and all strategies for entering a code into a page (ARIA textbox, DOM selectors,
fallback empty input, link navigation, missing input, exceptions).
"""
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent(**kwargs):
    """Construct an Agent without touching a browser or LLM."""
    with patch("fantoma.agent.LLMClient"):
        from fantoma.agent import Agent
        return Agent(llm_url="http://localhost:8080/v1", **kwargs)


def _make_dom(interactive_elements=None):
    """Return a mock AccessibilityExtractor-like DOM object."""
    dom = MagicMock()
    dom._last_interactive = interactive_elements or []
    return dom


def _make_browser(page=None):
    """Return a mock BrowserEngine that yields the given page."""
    browser = MagicMock()
    browser.get_page.return_value = page or MagicMock()
    return browser


# ---------------------------------------------------------------------------
# _get_verification — Tier 1: IMAP
# ---------------------------------------------------------------------------

class TestGetVerificationIMAP:
    def test_imap_returns_code(self):
        """When IMAP check_inbox succeeds, return its value immediately."""
        agent = _make_agent(
            email_imap={"host": "mail.example.com", "port": 993,
                        "user": "u", "password": "p"}
        )
        with patch("fantoma.browser.email_verify.check_inbox") as mock_inbox:
            mock_inbox.return_value = {"type": "code", "value": "837291", "subject": "Verify"}
            result = agent._get_verification("code", "example.com")
        assert result == "837291"
        mock_inbox.assert_called_once()

    def test_imap_returns_link(self):
        """IMAP can also return a link; the value is passed through unchanged."""
        agent = _make_agent(
            email_imap={"host": "mail.example.com", "port": 993,
                        "user": "u", "password": "p"}
        )
        with patch("fantoma.browser.email_verify.check_inbox") as mock_inbox:
            mock_inbox.return_value = {
                "type": "link",
                "value": "https://example.com/verify?token=xyz",
                "subject": "Verify",
            }
            result = agent._get_verification("link", "example.com")
        assert result == "https://example.com/verify?token=xyz"

    def test_imap_empty_falls_through_to_callback(self):
        """When IMAP returns None, the callback tier is tried."""
        cb = MagicMock(return_value="callback_code")
        agent = _make_agent(
            email_imap={"host": "mail.example.com", "port": 993,
                        "user": "u", "password": "p"},
            verification_callback=cb,
        )
        with patch("fantoma.browser.email_verify.check_inbox", return_value=None):
            result = agent._get_verification("code", "example.com")
        assert result == "callback_code"
        cb.assert_called_once()

    def test_imap_exception_propagates(self):
        """check_inbox exceptions are not caught by _get_verification — they propagate.

        This documents the current (unguarded) behaviour.  If defensive wrapping
        is added to agent.py the assertion should change to verify fallback instead.
        """
        agent = _make_agent(
            email_imap={"host": "mail.example.com", "port": 993,
                        "user": "u", "password": "p"},
        )
        with patch("fantoma.browser.email_verify.check_inbox",
                   side_effect=ConnectionRefusedError("IMAP down")):
            with pytest.raises(ConnectionRefusedError):
                agent._get_verification("code", "example.com")

    def test_imap_runtime_error_propagates(self):
        """RuntimeError from check_inbox is not swallowed."""
        agent = _make_agent(
            email_imap={"host": "mail.example.com", "port": 993,
                        "user": "u", "password": "p"},
        )
        with patch("fantoma.browser.email_verify.check_inbox",
                   side_effect=RuntimeError("timeout")):
            with pytest.raises(RuntimeError):
                agent._get_verification("code", "example.com")

    def test_imap_not_configured_skips_to_callback(self):
        """When no IMAP config, check_inbox is never imported; callback runs."""
        cb = MagicMock(return_value="only_callback")
        agent = _make_agent(verification_callback=cb)
        # check_inbox must never be called
        with patch("fantoma.browser.email_verify.check_inbox") as mock_inbox:
            result = agent._get_verification("code", "example.com")
        mock_inbox.assert_not_called()
        assert result == "only_callback"


# ---------------------------------------------------------------------------
# _get_verification — Tier 2: Callback
# ---------------------------------------------------------------------------

class TestGetVerificationCallback:
    def test_callback_returns_code(self):
        """Callback value is returned (stripped of whitespace)."""
        cb = MagicMock(return_value="  654321  ")
        agent = _make_agent(verification_callback=cb)
        result = agent._get_verification("code", "example.com")
        assert result == "654321"

    def test_callback_strips_whitespace(self):
        """Leading/trailing whitespace is stripped from callback output."""
        agent = _make_agent(verification_callback=lambda d, m: "\t  abc123\n")
        result = agent._get_verification("code", "example.com")
        assert result == "abc123"

    def test_callback_receives_domain_and_message(self):
        """Callback is called with (domain, message_string)."""
        received = {}

        def capturing_cb(domain, msg):
            received["domain"] = domain
            received["msg"] = msg
            return "ok"

        agent = _make_agent(verification_callback=capturing_cb)
        agent._get_verification("code", "test.domain.com")
        assert received["domain"] == "test.domain.com"
        assert "test.domain.com" in received["msg"]

    def test_callback_returns_none_falls_through_to_terminal(self):
        """If callback returns None/empty, terminal prompt is tried."""
        agent = _make_agent(verification_callback=lambda d, m: None)
        with patch("builtins.input", return_value="terminal_val"):
            result = agent._get_verification("code", "example.com")
        assert result == "terminal_val"

    def test_callback_exception_falls_through_to_terminal(self):
        """Exception in callback must not crash — terminal prompt runs next."""
        def bad_cb(d, m):
            raise ValueError("callback blew up")

        agent = _make_agent(verification_callback=bad_cb)
        with patch("builtins.input", return_value="terminal_after_cb_fail"):
            result = agent._get_verification("code", "example.com")
        assert result == "terminal_after_cb_fail"

    def test_callback_not_configured_skips_to_terminal(self):
        """When callback is None, terminal prompt is tried directly."""
        agent = _make_agent()
        with patch("builtins.input", return_value="terminal_only"):
            result = agent._get_verification("code", "example.com")
        assert result == "terminal_only"


# ---------------------------------------------------------------------------
# _get_verification — Tier 3: Terminal / None
# ---------------------------------------------------------------------------

class TestGetVerificationTerminal:
    def test_terminal_prompt_for_code(self):
        """input() is called with a code-specific prompt."""
        agent = _make_agent()
        with patch("builtins.input", return_value="111222") as mock_input:
            result = agent._get_verification("code", "example.com")
        assert result == "111222"
        prompt = mock_input.call_args[0][0]
        assert "code" in prompt.lower() or "example.com" in prompt

    def test_terminal_prompt_for_link(self):
        """input() prompt mentions 'link' when vtype is 'link'."""
        agent = _make_agent()
        with patch("builtins.input", return_value="https://example.com/v?t=1") as mock_input:
            result = agent._get_verification("link", "example.com")
        assert result == "https://example.com/v?t=1"
        prompt = mock_input.call_args[0][0]
        assert "link" in prompt.lower() or "example.com" in prompt

    def test_eoferror_returns_none(self):
        """EOFError (non-interactive stdin) causes None to be returned."""
        agent = _make_agent()
        with patch("builtins.input", side_effect=EOFError):
            result = agent._get_verification("code", "example.com")
        assert result is None

    def test_oserror_returns_none(self):
        """OSError (no tty) also causes None."""
        agent = _make_agent()
        with patch("builtins.input", side_effect=OSError("no tty")):
            result = agent._get_verification("code", "example.com")
        assert result is None

    def test_all_tiers_fail_returns_none(self):
        """When IMAP returns None, callback raises, and terminal raises EOFError → None."""
        agent = _make_agent(
            email_imap={"host": "mail.example.com", "port": 993,
                        "user": "u", "password": "p"},
            verification_callback=lambda d, m: (_ for _ in ()).throw(RuntimeError("cb fail")),
        )
        with patch("fantoma.browser.email_verify.check_inbox", return_value=None):
            with patch("builtins.input", side_effect=EOFError):
                result = agent._get_verification("code", "example.com")
        assert result is None


# ---------------------------------------------------------------------------
# _enter_verification_code — Strategy 1: ARIA textbox
# ---------------------------------------------------------------------------

class TestEnterVerificationCodeARIA:
    def test_aria_textbox_types_code_and_presses_enter(self):
        """When ARIA tree has a textbox, type_into is called and Enter is pressed."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)

        mock_handle = MagicMock()
        dom = _make_dom(interactive_elements=[{"role": "textbox", "index": 0}])
        dom.get_element_by_index.return_value = mock_handle

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "123456")

        mock_type.assert_called_once_with(browser, mock_handle, "123456")
        page.keyboard.press.assert_called_once_with("Enter")

    def test_aria_input_role_also_accepted(self):
        """Role 'input' (as well as 'textbox') triggers ARIA strategy."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)

        mock_handle = MagicMock()
        dom = _make_dom(interactive_elements=[{"role": "input", "index": 0}])
        dom.get_element_by_index.return_value = mock_handle

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "654321")

        mock_type.assert_called_once_with(browser, mock_handle, "654321")
        page.keyboard.press.assert_called_once_with("Enter")

    def test_aria_skips_when_get_element_returns_none(self):
        """If get_element_by_index returns None, the method falls to next strategy."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)

        dom = _make_dom(interactive_elements=[{"role": "textbox", "index": 0}])
        dom.get_element_by_index.return_value = None  # handle not found

        # Strategy 2 selectors also return None so we test fallback path
        page.query_selector.return_value = None
        page.query_selector_all.return_value = []

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "000000")

        mock_type.assert_not_called()
        # Warning should have been logged (no exception raised)

    def test_multiple_interactive_elements_picks_first_textbox(self):
        """With a button before a textbox, the textbox is still found and used."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)

        mock_handle = MagicMock()
        dom = _make_dom(interactive_elements=[
            {"role": "button", "index": 0},
            {"role": "textbox", "index": 1},
        ])

        def _get_by_index(pg, idx):
            return mock_handle if idx == 1 else None

        dom.get_element_by_index.side_effect = _get_by_index

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "999888")

        mock_type.assert_called_once_with(browser, mock_handle, "999888")
        page.keyboard.press.assert_called_once_with("Enter")


# ---------------------------------------------------------------------------
# _enter_verification_code — Strategy 2: DOM selectors
# ---------------------------------------------------------------------------

class TestEnterVerificationCodeDOMSelectors:
    def _make_setup(self, visible=True):
        """Create agent, browser, page, dom with no ARIA textboxes."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)
        dom = _make_dom(interactive_elements=[])  # no ARIA textboxes
        mock_input = MagicMock()
        mock_input.is_visible.return_value = visible
        return agent, browser, page, dom, mock_input

    def test_finds_code_input_by_name_selector(self):
        """input[name*='code'] selector leads to type_into + Enter."""
        agent, browser, page, dom, mock_input = self._make_setup()

        def _query(sel):
            if "code" in sel:
                return mock_input
            return None

        page.query_selector.side_effect = _query

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "246810")

        mock_type.assert_called_once_with(browser, mock_input, "246810")
        page.keyboard.press.assert_called_once_with("Enter")

    def test_invisible_element_skipped(self):
        """Invisible matches are skipped; next selector or strategy is tried."""
        agent, browser, page, dom, mock_input = self._make_setup(visible=False)
        page.query_selector.return_value = mock_input  # every selector hits it
        page.query_selector_all.return_value = []

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "135791")

        mock_type.assert_not_called()

    def test_selector_exception_skipped_continues(self):
        """An exception on one selector is swallowed; next selector is tried."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)
        dom = _make_dom(interactive_elements=[])

        good_handle = MagicMock()
        good_handle.is_visible.return_value = True
        call_count = {"n": 0}

        def _query(sel):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("playwright error")
            if "otp" in sel:
                return good_handle
            return None

        page.query_selector.side_effect = _query

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "777888")

        mock_type.assert_called_once()


# ---------------------------------------------------------------------------
# _enter_verification_code — Strategy 3: fallback empty input
# ---------------------------------------------------------------------------

class TestEnterVerificationCodeFallback:
    def test_uses_first_visible_empty_input(self):
        """When ARIA and selectors find nothing, any visible empty input is used."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)
        dom = _make_dom(interactive_elements=[])
        page.query_selector.return_value = None  # no selector matches

        empty_input = MagicMock()
        empty_input.is_visible.return_value = True
        empty_input.get_attribute.return_value = ""  # empty value

        page.query_selector_all.return_value = [empty_input]

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "121212")

        mock_type.assert_called_once_with(browser, empty_input, "121212")
        page.keyboard.press.assert_called_once_with("Enter")

    def test_skips_pre_filled_inputs(self):
        """Inputs with existing value are not used as code targets."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)
        dom = _make_dom(interactive_elements=[])
        page.query_selector.return_value = None

        prefilled = MagicMock()
        prefilled.is_visible.return_value = True
        prefilled.get_attribute.return_value = "existing@email.com"

        page.query_selector_all.return_value = [prefilled]

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "000111")

        mock_type.assert_not_called()

    def test_fallback_exception_handled_gracefully(self):
        """Exception in query_selector_all does not propagate."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)
        dom = _make_dom(interactive_elements=[])
        page.query_selector.return_value = None
        page.query_selector_all.side_effect = Exception("DOM exploded")

        # Must not raise
        with patch("fantoma.browser.actions.type_into"):
            agent._enter_verification_code(browser, dom, "999")


# ---------------------------------------------------------------------------
# _enter_verification_code — No input found
# ---------------------------------------------------------------------------

class TestEnterVerificationCodeNoInput:
    def test_no_textbox_found_logs_warning_no_crash(self):
        """When absolutely nothing is found, the method returns silently."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)
        dom = _make_dom(interactive_elements=[])
        page.query_selector.return_value = None
        page.query_selector_all.return_value = []

        # Should not raise
        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, "000000")

        mock_type.assert_not_called()

    def test_page_get_exception_propagates(self):
        """If browser.get_page() raises, the exception propagates out of
        _enter_verification_code (no defensive wrapper around the page fetch).

        This documents the current behaviour; if a try/except is added to
        agent.py around the get_page() call this test should be updated.
        """
        agent = _make_agent()
        browser = MagicMock()
        browser.get_page.side_effect = RuntimeError("browser gone")
        dom = _make_dom(interactive_elements=[])

        with patch("fantoma.browser.actions.type_into"):
            with pytest.raises(RuntimeError, match="browser gone"):
                agent._enter_verification_code(browser, dom, "123")


# ---------------------------------------------------------------------------
# _enter_verification_code — Code is a URL (link verification)
# ---------------------------------------------------------------------------

class TestEnterVerificationCodeIsLink:
    """The agent's login() method handles vtype='link' by calling browser.navigate()
    directly, bypassing _enter_verification_code entirely.  These tests verify
    that _enter_verification_code does NOT navigate when given a URL — it only
    types into an input field (treating the URL string as text to type).
    The navigation behaviour is tested at the login() integration level.
    """

    def test_url_string_typed_into_input_if_found(self):
        """A URL passed as 'code' is typed into the found input, not navigated to."""
        agent = _make_agent()
        page = MagicMock()
        browser = _make_browser(page)

        mock_handle = MagicMock()
        dom = _make_dom(interactive_elements=[{"role": "textbox", "index": 0}])
        dom.get_element_by_index.return_value = mock_handle

        url_code = "https://example.com/verify?token=abc123"

        with patch("fantoma.browser.actions.type_into") as mock_type:
            agent._enter_verification_code(browser, dom, url_code)

        mock_type.assert_called_once_with(browser, mock_handle, url_code)
        browser.navigate.assert_not_called()
