# tests/test_patchright.py

from unittest.mock import MagicMock, patch


def test_browser_config_default_camoufox():
    from fantoma.config import BrowserConfig
    cfg = BrowserConfig()
    assert cfg.browser_engine == "camoufox"


def test_browser_config_chromium():
    from fantoma.config import BrowserConfig
    cfg = BrowserConfig(browser_engine="chromium")
    assert cfg.browser_engine == "chromium"


def test_agent_browser_param():
    from fantoma.agent import Agent
    with patch("fantoma.agent.BrowserEngine"):
        with patch("fantoma.agent.LLMClient"):
            agent = Agent(
                llm_url="http://localhost:8080/v1",
                browser="chromium"
            )
            assert agent.config.browser.browser_engine == "chromium"


def test_engine_chromium_import_guard():
    """If patchright not installed, should raise clear error."""
    from fantoma.browser.engine import BrowserEngine
    import sys

    # Temporarily make patchright unimportable
    with patch.dict(sys.modules, {"patchright": None, "patchright.sync_api": None}):
        engine = BrowserEngine(headless=True, browser_engine="chromium")
        try:
            engine.start()
            assert False, "Should have raised ImportError"
        except ImportError as e:
            assert "pip install fantoma[chromium]" in str(e)


def test_engine_camoufox_unchanged():
    """Default engine behaviour (Camoufox) unchanged."""
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

        engine = BrowserEngine(headless=True)
        engine.start()
        MockCamo.assert_called_once()
