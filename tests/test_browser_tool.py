# tests/test_browser_tool.py
import pytest
from unittest.mock import MagicMock, patch


class TestFantomaLifecycle:
    """Test Fantoma start/stop/restart and get_state."""

    def test_start_returns_state_dict(self):
        """start() should return a dict with url, title, aria_tree, errors, tab_count."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "https://example.com"
            mock_page.title.return_value = "Example"

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = "[1] link 'Home'"
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    state = f.start("https://example.com")

                    assert isinstance(state, dict)
                    assert state["url"] == "https://example.com"
                    assert state["title"] == "Example"
                    assert state["aria_tree"] == "[1] link 'Home'"
                    assert state["errors"] == []
                    assert "tab_count" in state
                    mock_engine.start.assert_called_once()
                    mock_engine.navigate.assert_called_once_with("https://example.com")

    def test_start_without_url(self):
        """start() with no URL should start browser without navigating."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "about:blank"
            mock_page.title.return_value = ""

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = ""
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    state = f.start()
                    mock_engine.navigate.assert_not_called()

    def test_stop_closes_browser(self):
        """stop() should call browser.stop()."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "about:blank"
            mock_page.title.return_value = ""

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = ""
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    f.start()
                    f.stop()
                    mock_engine.stop.assert_called_once()

    def test_stop_without_start_is_safe(self):
        """stop() before start() should not raise."""
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f.stop()  # Should not raise

    def test_get_state_returns_current_page(self):
        """get_state() should return fresh state from current page."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "https://example.com/page2"
            mock_page.title.return_value = "Page 2"

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = "[1] button 'Submit'"
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    f.start()
                    state = f.get_state()
                    assert state["url"] == "https://example.com/page2"
                    assert state["aria_tree"] == "[1] button 'Submit'"

    def test_restart_returns_fresh_state(self):
        """restart() should stop and start with fresh fingerprint."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "https://example.com"
            mock_page.title.return_value = "Example"

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = ""
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    f.start("https://example.com")
                    state = f.restart()
                    assert isinstance(state, dict)
                    # stop called at least once (from restart)
                    assert mock_engine.stop.call_count >= 1


class TestFantomaActions:
    """Test click, type_text, scroll, navigate, press_key, select."""

    def _make_fantoma(self):
        """Create a Fantoma with mocked internals for action testing."""
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._dom = MagicMock()

        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        mock_page.url = "https://example.com"
        mock_page.title.return_value = "Test"
        f._dom.extract.return_value = "[1] button 'Click me'"
        f._dom._last_interactive = []
        return f, mock_page

    def test_click_returns_result_with_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        mock_element = MagicMock()
        f._dom.get_element_by_index.return_value = mock_element

        with patch("fantoma.browser_tool.click_element", return_value=True):
            with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                with patch("fantoma.browser_tool.wait_for_dom_stable"):
                    result = f.click(1)

        assert result["success"] is True
        assert "state" in result
        assert result["state"]["url"] == "https://example.com"

    def test_click_element_not_found(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        f._dom.get_element_by_index.return_value = None

        with patch("fantoma.browser_tool.detect_errors", return_value=[]):
            result = f.click(999)

        assert result["success"] is False

    def test_type_text_returns_result_with_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        mock_element = MagicMock()
        f._dom.get_element_by_index.return_value = mock_element

        with patch("fantoma.browser_tool.type_into", return_value=True):
            with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                with patch("fantoma.browser_tool.wait_for_dom_stable"):
                    result = f.type_text(1, "hello")

        assert result["success"] is True
        assert "state" in result

    def test_navigate_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()

        with patch("fantoma.browser_tool.dismiss_consent"):
            with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                with patch("fantoma.browser_tool.wait_for_dom_stable"):
                    result = f.navigate("https://other.com")

        assert result["success"] is True
        assert "state" in result
        f._engine.navigate.assert_called_once_with("https://other.com")

    def test_scroll_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()

        with patch("fantoma.browser_tool.scroll_page", return_value=True):
            with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                result = f.scroll("down")

        assert result["success"] is True
        assert "state" in result

    def test_press_key_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()

        with patch("fantoma.browser_tool.detect_errors", return_value=[]):
            result = f.press_key("Enter")

        assert result["success"] is True
        page.keyboard.press.assert_called_once_with("Enter")

    def test_select_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        mock_element = MagicMock()
        f._dom.get_element_by_index.return_value = mock_element

        with patch("fantoma.browser_tool.detect_errors", return_value=[]):
            with patch("fantoma.browser_tool.wait_for_dom_stable"):
                result = f.select(1, "Option A")

        assert result["success"] is True
        mock_element.select_option.assert_called_once_with(label="Option A")


class TestFantomaTabs:
    """Test tab management methods."""

    def _make_fantoma(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._dom = MagicMock()
        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        mock_page.url = "https://example.com"
        mock_page.title.return_value = "Test"
        f._dom.extract.return_value = ""
        f._dom._last_interactive = []
        return f, mock_page

    def test_new_tab_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        f._engine.new_tab.return_value = 1

        with patch("fantoma.browser_tool.detect_errors", return_value=[]):
            result = f.new_tab("https://other.com")

        assert "state" in result
        f._engine.new_tab.assert_called_once_with("https://other.com")

    def test_list_tabs(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        mock_ctx = MagicMock()
        mock_page1 = MagicMock()
        mock_page1.url = "https://example.com"
        mock_page2 = MagicMock()
        mock_page2.url = "https://other.com"
        mock_ctx.pages = [mock_page1, mock_page2]
        f._engine._context = mock_ctx

        tabs = f.list_tabs()
        assert len(tabs) == 2
        assert tabs[0]["url"] == "https://example.com"
        assert tabs[1]["url"] == "https://other.com"


class TestFantomaLogin:
    """Test login delegation."""

    def test_login_navigates_and_fills(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._dom = MagicMock()
        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        mock_page.url = "https://example.com/dashboard"
        mock_page.title.return_value = "Dashboard"
        f._dom.extract.return_value = ""
        f._dom._last_interactive = []

        with patch("fantoma.browser_tool.form_login") as mock_form_login:
            mock_form_login.return_value = {
                "success": True, "steps": 1, "url": "https://example.com/dashboard",
                "fields_filled": ["email", "password"],
            }
            with patch("fantoma.browser_tool._looks_logged_in", return_value=True):
                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    result = f.login("https://example.com/login",
                                     email="test@test.com", password="pass")

        assert result["success"] is True


class TestFantomaExtract:
    """Test extraction with and without LLM."""

    def test_extract_without_llm_returns_aria(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()  # No llm_url
        f._engine = MagicMock()
        f._dom = MagicMock()
        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        f._dom.extract.return_value = "[1] heading 'Books'\n[2] text 'Python for Beginners'"
        f._dom._last_interactive = []

        result = f.extract("What books are listed?")
        assert "Books" in result
        assert "Python for Beginners" in result

    def test_extract_with_llm_calls_chat(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma(llm_url="http://localhost:8080/v1")
        f._engine = MagicMock()
        f._dom = MagicMock()
        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        # Mock the locator chain so main.count() returns 0 (no <main> element)
        mock_locator = MagicMock()
        mock_locator.count.return_value = 0
        mock_page.locator.return_value = mock_locator
        mock_page.inner_text.return_value = "Book: Python, Price: $10"
        f._dom.extract.return_value = ""
        f._dom._last_interactive = []
        f._llm = MagicMock()
        f._llm.chat.return_value = '{"title": "Python", "price": "$10"}'

        result = f.extract("Extract books", schema={"title": str, "price": str})
        f._llm.chat.assert_called_once()


class TestFantomaUtilities:
    """Test cookie and storage methods."""

    def test_get_cookies(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._engine.get_cookies.return_value = [{"name": "sid", "value": "abc"}]
        cookies = f.get_cookies()
        assert cookies == [{"name": "sid", "value": "abc"}]

    def test_get_storage_state(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._engine.get_storage_state.return_value = {"cookies": [], "origins": []}
        state = f.get_storage_state()
        assert "cookies" in state
