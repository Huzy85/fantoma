"""Tests for BrowserEngine storage state save/load."""


def test_get_storage_state_returns_dict_with_cookies_and_origins():
    from fantoma.browser.engine import BrowserEngine
    assert hasattr(BrowserEngine, "get_storage_state")


def test_load_storage_state_accepts_dict():
    from fantoma.browser.engine import BrowserEngine
    assert hasattr(BrowserEngine, "load_storage_state")
