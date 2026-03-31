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
