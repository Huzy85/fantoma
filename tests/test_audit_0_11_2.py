"""Checks for the 0.11.2 audit fixes: scheme and private-host policy,
server binding and request limits, file permissions, dialog/popup/crash
reporting, custom-widget clicks and iframe-only pages. Browser-backed
tests run a real Chromium against pages served from this process."""
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from fantoma.browser.domains import DomainPolicy, _is_private_host


# ---------------------------------------------------------------- domains
class TestSchemesAndPrivateHosts:
    def test_inert_schemes_always_pass(self):
        p = DomainPolicy(allowed=["example.com"])
        assert p.permits("about:blank") and p.permits("data:text/html,hi") and p.permits("")

    def test_file_and_chrome_refused_when_policy_active(self):
        p = DomainPolicy(allowed=["example.com"])
        for url in ("file:///etc/passwd", "chrome://settings", "view-source:https://example.com"):
            assert p.permits(url) is False, url

    def test_file_allowed_when_no_policy(self):
        assert DomainPolicy().permits("file:///tmp/x.html") is True

    def test_private_hosts_refused_under_blocklist_only(self):
        p = DomainPolicy(blocked=["evil.example"])
        for url in ("http://127.0.0.1:8080/", "http://10.0.0.5/", "http://192.168.1.1/",
                    "http://localhost/", "http://[::1]/", "http://169.254.169.254/latest/",
                    "http://2130706433/"):
            assert p.permits(url) is False, url
        assert p.permits("https://example.com/") is True

    def test_private_host_passes_when_named_in_allowlist(self):
        p = DomainPolicy(allowed=["localhost", "example.com"])
        assert p.permits("http://localhost:9000/") is True
        assert p.permits("http://127.0.0.1/") is False

    def test_private_host_helper(self):
        assert _is_private_host("0.0.0.0") and _is_private_host("fd00::1") and _is_private_host("foo.localhost")
        assert not _is_private_host("example.com") and not _is_private_host("8.8.8.8")


# ----------------------------------------------------------------- server
class TestServerHelpers:
    @pytest.fixture(autouse=True)
    def _server(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FANTOMA_PROFILE_BASE", str(tmp_path / "profiles"))
        import importlib
        import server
        importlib.reload(server)
        self.server = server
        yield
        importlib.reload(server)

    def test_bind_host_defaults(self):
        s = self.server
        assert s._bind_host("", None) == "127.0.0.1"
        assert s._bind_host("key", None) == "0.0.0.0"
        assert s._bind_host("", "0.0.0.0") == "0.0.0.0"

    def test_profile_dir_stays_under_base(self):
        s = self.server
        base = s.PROFILE_BASE
        assert s._safe_profile_dir("my-profile") == os.path.join(base, "my-profile")
        assert s._safe_profile_dir(os.path.join(base, "x")) == os.path.join(base, "x")
        assert s._safe_profile_dir("/etc") is None
        assert s._safe_profile_dir("../../etc") is None
        assert s._safe_profile_dir("") is None and s._safe_profile_dir(5) is None

    def test_request_proxy_ignored_without_key(self, monkeypatch):
        s = self.server
        monkeypatch.setattr(s, "ALLOW_REQUEST_PROXY", False)
        monkeypatch.setattr(s, "PROXY_URL", None)
        assert s._request_proxy({"proxy": "http://attacker:8080"}) is None
        monkeypatch.setattr(s, "ALLOW_REQUEST_PROXY", True)
        assert s._request_proxy({"proxy": "http://mine:8080"}) == "http://mine:8080"
        assert s._request_proxy({}) is None

    def test_watchdog_cap_exists(self):
        assert self.server._WATCHDOG_MAX_REQUESTED == 3600


# ------------------------------------------------------------ permissions
class TestFilePermissions:
    def test_session_files_are_private(self, tmp_path):
        from fantoma.session import SessionManager
        sm = SessionManager(str(tmp_path / "sessions"))
        fake_ctx = MagicMock()
        fake_ctx.storage_state.return_value = {"cookies": [{"name": "a", "value": "b"}], "origins": []}
        sm.save("example.com", "user@example.com", fake_ctx.storage_state(), "https://example.com/login")
        d = tmp_path / "sessions"
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
        files = list(d.iterdir())
        assert files, "nothing saved"
        assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)

    def test_form_memory_db_is_private(self, tmp_path):
        from fantoma.browser.form_memory import FormMemory
        FormMemory(str(tmp_path / "m" / "forms.db"))
        assert stat.S_IMODE((tmp_path / "m" / "forms.db").stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "m").stat().st_mode) == 0o700


# -------------------------------------------------------------- aria diff
def test_duplicate_names_numbered_linearly():
    from fantoma.dom.aria_diff import aria_snapshot
    page = MagicMock()
    page.locator.return_value.aria_snapshot.return_value = "\n".join('- button "Remove"' for _ in range(5))
    snap = aria_snapshot(page)
    keys = sorted(k for k in snap)
    assert ("button", "Remove") in snap
    assert ("button", "Remove #2") in snap and ("button", "Remove #5") in snap
    assert len(keys) == 5


# ---------------------------------------------------- action result notes
class TestActionNotes:
    def _tool(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma.__new__(Fantoma)
        f._last_tree, f._task = None, ""
        f.get_state = lambda task=None: {"url": "u", "aria_tree": "t", "errors": [], "tab_count": 2}
        return f

    def test_dialog_and_popup_reported(self):
        f = self._tool()
        f._engine = MagicMock()
        f._engine.take_dialog.return_value = {"type": "confirm", "message": "Delete this item?"}
        f._engine.take_popup_count.return_value = 1
        r = f._action_result(True, "u")
        assert r["changed"] is True
        assert any("confirm dialog was dismissed" in n and "Delete this item?" in n for n in r["notes"])
        assert any("opened 1 new tab" in n and "switch_tab" in n for n in r["notes"])

    def test_quiet_when_nothing_happened(self):
        f = self._tool()
        f._engine = MagicMock()
        f._engine.take_dialog.return_value = None
        f._engine.take_popup_count.return_value = 0
        r = f._action_result(True, "u")
        assert r["notes"] == [] and r["changed"] is False


# ------------------------------------------------------- real browser part
DIALOG_PAGE = """<html><body>
<button id="del" onclick="window.r = confirm('Delete this item?')">Delete</button>
<div id="custom" onclick="document.getElementById('out').textContent='clicked'" tabindex="0">Fancy div</div>
<span id="out"></span>
<a id="pop" href="/iframe" target="_blank">Open help</a>
</body></html>"""

IFRAME_ONLY = """<html><body><h1>Pay</h1>
<iframe id="card" srcdoc="&lt;input aria-label='Card number'&gt;&lt;button&gt;Pay now&lt;/button&gt;"></iframe>
</body></html>"""


@pytest.fixture(scope="module")
def site():
    pages = {"/dialog": DIALOG_PAGE, "/iframe": IFRAME_ONLY}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = pages.get(self.path, "<h1>missing</h1>").encode()
            self.send_response(200 if self.path in pages else 404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    exe = os.environ.get("FANTOMA_TEST_CHROMIUM") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as e:
        pytest.skip(f"playwright unavailable: {e}")
    try:
        browser = pw.chromium.launch(**({"executable_path": exe} if os.path.exists(exe) else {}))
    except Exception as e:
        pw.stop()
        pytest.skip(f"no Chromium available: {e}")
    yield browser
    browser.close()
    pw.stop()


def _engine_on(context):
    """A BrowserEngine wired to an existing Playwright context."""
    from fantoma.browser.engine import BrowserEngine
    e = BrowserEngine.__new__(BrowserEngine)
    e.humanizer = None
    e.last_dialog, e._popup_count, e._crashed = None, 0, set()
    e.last_status, e._last_status_url = None, ""
    from fantoma.browser.domains import DomainPolicy
    e.domain_policy = DomainPolicy()
    e._context = context
    e._page = context.new_page()
    e._install_page_hooks()
    e.get_page = lambda: e._page
    return e


class TestRealBrowser:
    def test_confirm_dialog_is_dismissed_and_reported(self, chromium, site):
        ctx = chromium.new_context()
        try:
            e = _engine_on(ctx)
            e._page.goto(site + "/dialog")
            e._page.click("#del")
            assert e._page.evaluate("window.r") is False
            d = e.take_dialog()
            assert d == {"type": "confirm", "message": "Delete this item?"}
            assert e.take_dialog() is None
        finally:
            ctx.close()

    def test_dialog_accept_policy(self, chromium, site, monkeypatch):
        monkeypatch.setenv("FANTOMA_DIALOGS", "accept")
        ctx = chromium.new_context()
        try:
            e = _engine_on(ctx)
            e._page.goto(site + "/dialog")
            e._page.click("#del")
            assert e._page.evaluate("window.r") is True
        finally:
            ctx.close()

    def test_site_opened_popup_is_counted(self, chromium, site):
        ctx = chromium.new_context()
        try:
            e = _engine_on(ctx)
            e._page.goto(site + "/dialog")
            with ctx.expect_page():
                e._page.click("#pop")
            assert e.take_popup_count() == 1
            assert e.take_popup_count() == 0
            # Tabs we open ourselves are not popups
            e.new_tab("about:blank")
            assert e.take_popup_count() == 0
        finally:
            ctx.close()

    def test_div_with_onclick_gets_clicked(self, chromium, site):
        from fantoma.browser.actions import click_element
        ctx = chromium.new_context()
        try:
            e = _engine_on(ctx)
            e._page.goto(site + "/dialog")
            assert click_element(e, "#custom") is True
            assert e._page.inner_text("#out") == "clicked"
        finally:
            ctx.close()

    def test_iframe_only_page_lists_frame_controls(self, chromium, site):
        from fantoma.dom.accessibility import AccessibilityExtractor
        ctx = chromium.new_context()
        try:
            page = ctx.new_page()
            page.goto(site + "/iframe")
            page.wait_for_load_state("networkidle")
            out = AccessibilityExtractor().extract(page, task="pay now")
            assert "Pay now" in out and "Card number" in out
        finally:
            ctx.close()

    def test_http_error_status_is_reported(self, chromium, site):
        ctx = chromium.new_context()
        try:
            e = _engine_on(ctx)
            from fantoma.browser.domains import DomainPolicy
            e.domain_policy = DomainPolicy()
            e.navigate(site + "/nothing-here")
            assert e.last_status == 404 and e._last_status_url == e._page.url
        finally:
            ctx.close()

    def test_closed_page_reports_crash(self, chromium):
        ctx = chromium.new_context()
        try:
            e = _engine_on(ctx)
            assert e.page_crashed() is False
            e._page.close()
            assert e.page_crashed() is True
        finally:
            ctx.close()
