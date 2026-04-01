"""Tests for Playwright trace recording (opt-in via trace=True)."""

import os
import tempfile
from unittest.mock import MagicMock, patch


def test_trace_config_default_off():
    from fantoma.config import BrowserConfig
    cfg = BrowserConfig()
    assert cfg.trace is False


def test_fantoma_accepts_trace_param():
    from fantoma.browser_tool import Fantoma
    with patch("fantoma.browser_tool.BrowserEngine"):
        f = Fantoma(trace=True)
        assert f.config.browser.trace is True


def test_engine_start_enables_tracing(tmp_path):
    from fantoma.browser.engine import BrowserEngine

    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    with patch("fantoma.browser.engine.Camoufox") as MockCamo:
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_browser)
        mock_cm.__exit__ = MagicMock(return_value=False)
        MockCamo.return_value = mock_cm

        engine = BrowserEngine(headless=True, trace=True)
        engine.start()

        mock_context.tracing.start.assert_called_once()


def test_engine_stop_saves_trace(tmp_path):
    from fantoma.browser.engine import BrowserEngine

    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_context.pages = [mock_page]

    with patch("fantoma.browser.engine.Camoufox") as MockCamo:
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_context)
        mock_cm.__exit__ = MagicMock(return_value=False)
        MockCamo.return_value = mock_cm

        engine = BrowserEngine(headless=True, trace=True,
                               trace_dir=str(tmp_path))
        engine.start()
        engine._page = mock_page
        engine._context = mock_context

        mock_page.url = "https://example.com/page"
        engine.stop()

        mock_context.tracing.stop.assert_called_once()
        call_args = mock_context.tracing.stop.call_args
        assert str(tmp_path) in call_args.kwargs.get("path", "")


def test_engine_no_trace_by_default():
    from fantoma.browser.engine import BrowserEngine

    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    with patch("fantoma.browser.engine.Camoufox") as MockCamo:
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_browser)
        mock_cm.__exit__ = MagicMock(return_value=False)
        MockCamo.return_value = mock_cm

        engine = BrowserEngine(headless=True)
        engine.start()

        mock_context.tracing.start.assert_not_called()
