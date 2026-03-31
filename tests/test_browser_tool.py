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
