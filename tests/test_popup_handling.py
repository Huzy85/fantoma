"""Tests for auto-follow popup/new tab handling in BrowserEngine."""

from unittest.mock import MagicMock, call


def _make_engine():
    """Create a BrowserEngine with mocked internals."""
    from fantoma.browser.engine import BrowserEngine
    engine = BrowserEngine.__new__(BrowserEngine)
    engine._context = MagicMock()
    engine._page = MagicMock()
    engine._page.url = "https://original.com"
    engine._previous_page = None
    return engine


def test_popup_handler_registered_on_context():
    """_setup_popup_handling registers a 'page' event listener on the context."""
    engine = _make_engine()
    engine._setup_popup_handling()
    engine._context.on.assert_called_once()
    args = engine._context.on.call_args
    assert args[0][0] == "page"


def test_popup_switches_page():
    """When a new page opens, engine._page switches to it."""
    engine = _make_engine()
    original_page = engine._page

    engine._setup_popup_handling()
    # Extract the on_new_page callback
    on_new_page = engine._context.on.call_args[0][1]

    # Simulate a popup opening
    popup = MagicMock()
    popup.url = "https://oauth.provider.com/authorize"
    on_new_page(popup)

    assert engine._page is popup
    assert engine._previous_page is original_page
    popup.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=10000)


def test_popup_close_restores_previous_page():
    """When the popup closes, engine._page switches back to the original."""
    engine = _make_engine()
    original_page = engine._page

    engine._setup_popup_handling()
    on_new_page = engine._context.on.call_args[0][1]

    popup = MagicMock()
    popup.url = "https://oauth.provider.com/authorize"
    popup.on = MagicMock()
    on_new_page(popup)

    # Extract the close callback registered on the popup
    close_callback = popup.on.call_args[0][1]

    # Simulate popup closing
    close_callback()

    assert engine._page is original_page


def test_popup_close_fallback_to_remaining():
    """If the previous page is also closed, fall back to last remaining page."""
    engine = _make_engine()

    engine._setup_popup_handling()
    on_new_page = engine._context.on.call_args[0][1]

    popup = MagicMock()
    popup.url = "https://popup.com"
    popup.on = MagicMock()
    on_new_page(popup)

    close_callback = popup.on.call_args[0][1]

    # Previous page throws when accessed (it was closed)
    engine._previous_page.url = property(lambda s: (_ for _ in ()).throw(Exception("closed")))
    type(engine._previous_page).url = property(lambda s: (_ for _ in ()).throw(Exception("closed")))

    fallback_page = MagicMock()
    fallback_page.url = "https://fallback.com"
    engine._context.pages = [fallback_page]

    close_callback()

    assert engine._page is fallback_page


def test_no_context_skips_setup():
    """_setup_popup_handling is a no-op when context is None."""
    from fantoma.browser.engine import BrowserEngine
    engine = BrowserEngine.__new__(BrowserEngine)
    engine._context = None
    engine._page = None
    # Should not raise
    engine._setup_popup_handling()


def test_popup_load_timeout_doesnt_crash():
    """If the popup page fails to load, we still switch to it."""
    engine = _make_engine()

    engine._setup_popup_handling()
    on_new_page = engine._context.on.call_args[0][1]

    popup = MagicMock()
    popup.url = "about:blank"
    popup.wait_for_load_state.side_effect = Exception("Timeout")
    on_new_page(popup)

    # Should still switch despite timeout
    assert engine._page is popup


def test_multiple_popups_chain():
    """Opening popup A then popup B keeps the chain correct."""
    engine = _make_engine()
    original_page = engine._page

    engine._setup_popup_handling()
    on_new_page = engine._context.on.call_args[0][1]

    popup_a = MagicMock()
    popup_a.url = "https://popup-a.com"
    popup_a.on = MagicMock()
    on_new_page(popup_a)

    assert engine._page is popup_a
    assert engine._previous_page is original_page

    popup_b = MagicMock()
    popup_b.url = "https://popup-b.com"
    popup_b.on = MagicMock()
    on_new_page(popup_b)

    assert engine._page is popup_b
    assert engine._previous_page is popup_a
