"""Tests for 3-level environment escalation (cookies/proxy/fingerprint)."""

from unittest.mock import MagicMock, patch, call


def test_resilience_config_default_retry_levels():
    from fantoma.config import ResilienceConfig
    cfg = ResilienceConfig()
    assert cfg.retry_levels == 3


def test_resilience_config_disable_env_escalation():
    from fantoma.config import ResilienceConfig
    cfg = ResilienceConfig(retry_levels=1)
    assert cfg.retry_levels == 1


def test_engine_clear_cookies():
    from fantoma.browser.engine import BrowserEngine
    engine = BrowserEngine.__new__(BrowserEngine)
    engine._context = MagicMock()
    engine.clear_cookies()
    engine._context.clear_cookies.assert_called_once()


def test_engine_restart_with_new_fingerprint():
    from fantoma.browser.engine import BrowserEngine
    engine = BrowserEngine.__new__(BrowserEngine)
    engine.headless = True
    engine.profile_dir = None
    engine.humanizer = None
    engine.accessibility = True
    engine.proxy = None
    engine._trace = False
    engine._trace_dir = "/tmp/traces"
    engine._trace_active = False
    engine._context = MagicMock()
    engine._browser = MagicMock()
    engine._page = MagicMock()
    engine._camoufox_cm = MagicMock()
    engine._persistent = False

    with patch.object(engine, "stop") as mock_stop:
        with patch.object(engine, "start") as mock_start:
            engine.restart_with_new_fingerprint()
            mock_stop.assert_called_once()
            mock_start.assert_called_once()


def test_executor_has_env_escalation_method():
    from fantoma.executor import Executor
    assert hasattr(Executor, "_try_env_escalation")


def test_executor_env_level_init():
    """Executor should initialize _env_level to 1."""
    from fantoma.executor import Executor
    from fantoma.config import FantomaConfig
    mock_browser = MagicMock()
    mock_llm = MagicMock()
    mock_escalation = MagicMock()
    config = FantomaConfig()
    executor = Executor(mock_browser, mock_llm, config, mock_escalation)
    assert executor._env_level == 1
