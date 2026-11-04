"""Engine capability helpers and the full-Chrome headless option."""
from fantoma.browser.engine import BrowserEngine


class _NewPage:
    webmcp = object()

    def aria_snapshot(self, *, mode=None):
        return ""

    def get_by_ref(self, ref):
        return None


class _OldPage:
    """Playwright 1.58 style: no page-level aria_snapshot, no refs."""

    def locator(self, sel):
        return None


class _NoModePage:
    def aria_snapshot(self):
        return ""

    def get_by_ref(self, ref):
        return None


def _engine(page):
    e = BrowserEngine()
    e._page = page
    return e


def test_refs_true_on_new_page():
    assert _engine(_NewPage()).supports_aria_refs() is True


def test_refs_false_on_old_page():
    assert _engine(_OldPage()).supports_aria_refs() is False


def test_refs_false_without_mode_argument():
    assert _engine(_NoModePage()).supports_aria_refs() is False


def test_no_page_reports_false():
    e = _engine(None)
    assert e.supports_aria_refs() is False
    assert e.supports_webmcp() is False


def test_webmcp_follows_attribute():
    assert _engine(_NewPage()).supports_webmcp() is True
    assert _engine(_OldPage()).supports_webmcp() is False


def test_full_headless_default_off(monkeypatch):
    monkeypatch.delenv("FANTOMA_CHROMIUM_FULL_HEADLESS", raising=False)
    assert BrowserEngine().chromium_full_headless is False


def test_full_headless_option_and_env(monkeypatch):
    assert BrowserEngine(chromium_full_headless=True).chromium_full_headless is True
    monkeypatch.setenv("FANTOMA_CHROMIUM_FULL_HEADLESS", "1")
    assert BrowserEngine().chromium_full_headless is True
    assert BrowserEngine(chromium_full_headless=False).chromium_full_headless is False
