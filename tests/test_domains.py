"""Domain policy: which hosts the browser may contact."""

from unittest.mock import MagicMock

import pytest

from fantoma.browser.domains import DomainBlocked, DomainPolicy


class TestPermits:
    def test_empty_policy_allows_everything(self):
        p = DomainPolicy()
        assert not p.active and p.permits("https://anything.example/x")

    def test_allow_list_includes_subdomains(self):
        p = DomainPolicy(allowed=["example.com"])
        assert p.permits("https://example.com/")
        assert p.permits("https://www.example.com/a")
        assert not p.permits("https://example.com.evil.net/")
        assert not p.permits("https://notexample.com/")

    def test_wildcard_means_subdomains_only(self):
        p = DomainPolicy(allowed=["*.example.com"])
        assert p.permits("https://api.example.com/")
        assert not p.permits("https://example.com/")

    def test_block_list_wins_over_allow_list(self):
        p = DomainPolicy(allowed=["example.com"], blocked=["ads.example.com"])
        assert not p.permits("https://ads.example.com/x.png")
        assert p.permits("https://shop.example.com/")

    def test_non_network_schemes_are_always_allowed(self):
        p = DomainPolicy(allowed=["example.com"])
        for url in ("data:text/html,hi", "about:blank", "blob:https://x/1"):
            assert p.permits(url)

    def test_comma_separated_string_and_case(self):
        p = DomainPolicy(allowed="Example.com, other.org")
        assert p.permits("https://EXAMPLE.com/") and p.permits("https://other.org/")

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("FANTOMA_ALLOWED_DOMAINS", "example.com")
        assert DomainPolicy.from_env().allowed == ["example.com"]
        assert DomainPolicy.from_env(allowed=[]).allowed == []

    def test_check_raises(self):
        with pytest.raises(DomainBlocked, match="evil.example"):
            DomainPolicy(blocked=["evil.example"]).check("https://evil.example/steal?x=1")


class TestInstall:
    def _handler(self, **kw):
        ctx = MagicMock()
        DomainPolicy(allowed=["example.com"], **kw).install(ctx)
        return ctx, ctx.route.call_args[0][1]

    def _route(self, url):
        r = MagicMock()
        r.request.url = url
        return r

    def test_disallowed_requests_are_aborted(self):
        _, handler = self._handler(strict=True)
        bad = self._route("https://tracker.evil/pixel.gif?c=secret")
        handler(bad)
        bad.abort.assert_called_once_with("blockedbyclient")
        bad.fetch.assert_not_called()

    def test_strict_checks_where_a_redirect_goes(self):
        _, handler = self._handler(strict=True)
        route = self._route("https://example.com/r")
        route.fetch.return_value = MagicMock(status=302, headers={"location": "https://evil.example/x"})
        handler(route)
        route.fetch.assert_called_once_with(max_redirects=0)
        route.abort.assert_called_once_with("blockedbyclient")
        route.fulfill.assert_not_called()

    def test_strict_passes_allowed_responses_through(self):
        _, handler = self._handler(strict=True)
        route = self._route("https://example.com/")
        resp = MagicMock(status=200, headers={})
        route.fetch.return_value = resp
        handler(route)
        route.fulfill.assert_called_once_with(response=resp)

    def test_strict_follows_a_relative_same_site_redirect(self):
        _, handler = self._handler(strict=True)
        route = self._route("https://example.com/a")
        route.fetch.return_value = MagicMock(status=301, headers={"location": "/b"})
        handler(route)
        route.fulfill.assert_called_once()

    def test_loose_mode_uses_the_browsers_own_networking(self):
        _, handler = self._handler(strict=False)
        ok = self._route("https://example.com/app.js")
        handler(ok)
        ok.continue_.assert_called_once()
        ok.fetch.assert_not_called()

    def test_a_handler_error_never_leaves_the_request_hanging(self):
        _, handler = self._handler(strict=True)
        route = self._route("https://example.com/")
        route.fetch.side_effect = RuntimeError("network down")
        handler(route)
        route.abort.assert_called_once()

    def test_websockets_are_guarded_in_the_page(self):
        ctx, _ = self._handler()
        script = ctx.add_init_script.call_args[0][0]
        assert '["example.com"]' in script and "SecurityError" in script

    def test_strict_is_the_default_and_env_can_turn_it_off(self, monkeypatch):
        monkeypatch.delenv("FANTOMA_DOMAIN_STRICT", raising=False)
        assert DomainPolicy(allowed=["a.com"]).strict
        monkeypatch.setenv("FANTOMA_DOMAIN_STRICT", "0")
        assert not DomainPolicy(allowed=["a.com"]).strict

    def test_unicode_patterns_match_punycode_hosts(self):
        p = DomainPolicy(blocked=["bücher.de"])
        assert not p.permits("https://xn--bcher-kva.de/")
        assert not p.permits("https://bücher.de/")

    def test_inactive_policy_installs_nothing(self):
        ctx = MagicMock()
        DomainPolicy().install(ctx)
        ctx.route.assert_not_called()
        ctx.add_init_script.assert_not_called()


class TestFantomaNavigate:
    def test_blocked_navigation_reports_why(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._engine.navigate.side_effect = DomainBlocked("evil.example is not permitted")
        f._action_result = lambda ok, pre=None: {"success": ok}
        r = f.navigate("https://evil.example/")
        assert r["success"] is False and "not permitted" in r["error"]

    def test_read_refuses_to_describe_the_old_page(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f.navigate = lambda url: {"success": False, "error": "evil.example is not permitted"}
        with pytest.raises(RuntimeError, match="not permitted"):
            f.read("https://evil.example/")


# ── Real browser: redirects and WebSockets ────────────────────

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@pytest.fixture(scope="module")
def redirect_server():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(f"{self.headers.get('Host', '').split(':')[0]}{self.path}")
            port = self.server.server_address[1]
            if self.path.startswith("/r"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{port}/secret?x=1")
                self.end_headers()
                return
            body = (b"<h1 id=s>start</h1><script>try{new WebSocket('ws://127.0.0.1:"
                    + str(port).encode() + b"/ws');s.textContent='opened'}"
                    b"catch(e){s.textContent='threw'}</script>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], hits
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


class TestInRealBrowser:
    def test_strict_stops_a_redirect_to_another_host(self, chromium, redirect_server):
        port, hits = redirect_server
        hits.clear()
        ctx = chromium.new_context()
        try:
            DomainPolicy(allowed=["localhost"], strict=True).install(ctx)
            page = ctx.new_page()
            with pytest.raises(Exception, match="BLOCKED_BY_CLIENT"):
                page.goto(f"http://localhost:{port}/r", timeout=8000)
            assert not [h for h in hits if h.startswith("127.0.0.1")]
        finally:
            ctx.close()

    def test_websocket_to_another_host_throws(self, chromium, redirect_server):
        port, _ = redirect_server
        ctx = chromium.new_context()
        try:
            DomainPolicy(allowed=["localhost"]).install(ctx)
            page = ctx.new_page()
            page.goto(f"http://localhost:{port}/page", timeout=8000)
            assert page.inner_text("#s") == "threw"
            assert "native code" in page.evaluate("WebSocket.toString()")
        finally:
            ctx.close()
