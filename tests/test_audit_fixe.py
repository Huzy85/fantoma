"""Fourth-audit fixes: the browser never follows a 3xx itself (stateful-server
tests in real Chromium and Camoufox), no POST body on follow-up hops, the
WebSocket guard cannot be bypassed through the prototype, DNS lookup dedupe,
and the keyless server's Host check and JSON-only POSTs."""

import importlib.util
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from fantoma.browser import domains
from fantoma.browser.domains import DomainPolicy, settle_hops

ROOT = os.path.dirname(os.path.dirname(__file__))


# ── a server that answers the checker one way and the browser another ──

@pytest.fixture
def stateful():
    hits = []      # (host, method, path, content-length, content-type, body)
    counts = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body=b"", loc=None, ctype="text/html"):
            self.send_response(status)
            if loc:
                self.send_header("Location", loc)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _handle(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            host = self.headers.get("Host", "").split(":")[0]
            path = self.path.split("?")[0]
            hits.append((host, self.command, path, self.headers.get("Content-Length"),
                         self.headers.get("Content-Type"), body))
            counts[path] = counts.get(path, 0) + 1
            port = self.server.server_address[1]
            secret = f"http://127.0.0.2:{port}/secret"
            if path == "/r1":                      # first answer fine, second a refused host
                return self._send(302, loc=f"http://localhost:{port}/r2")
            if path == "/r2":
                if counts[path] == 1:
                    return self._send(200, b"<h1 id=m>r2 clean</h1>")
                return self._send(302, loc=secret)
            if path == "/s1":
                return self._send(302, loc=f"http://localhost:{port}/s2")
            if path == "/s2":
                if counts[path] == 1:
                    return self._send(200, b"s2 clean", ctype="text/plain")
                return self._send(302, loc=secret)
            if path == "/sbad":
                return self._send(302, loc=secret)
            if path == "/secret":
                return self._send(200, b"<h1>SECRET</h1>")
            if path.startswith("/form"):
                kind = path[len("/form"):]
                return self._send(200, (f"<form id=f method=post action='/post{kind}'>"
                                        "<input name=pw value=hunter2></form>").encode())
            if path in ("/post303", "/post302"):
                return self._send(int(path[-3:]), loc=f"http://localhost:{port}/r1")
            if path in ("/post307", "/post308"):
                return self._send(int(path[-3:]), loc=f"http://localhost:{port}/r1")
            if path == "/postok":
                return self._send(303, loc=f"http://localhost:{port}/after")
            if path == "/after":
                return self._send(200, b"<h1 id=m>after</h1>")
            if path == "/sub302":                  # subresource POST 302 -> GET /g
                return self._send(302, loc=f"http://localhost:{port}/g")
            if path == "/g":
                return self._send(200, b"got", ctype="text/plain")
            if path == "/page":
                return self._send(200, b"<h1 id=m>page</h1>")
            return self._send(404, b"nf")

        do_GET = do_POST = do_HEAD = _handle

    server = ThreadingHTTPServer(("0.0.0.0", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], hits, counts
    finally:
        server.shutdown()
        server.server_close()


def _forbidden(hits):
    return [h for h in hits if h[0] == "127.0.0.2"]


@pytest.fixture(params=["chromium", "camoufox"])
def ctx(request):
    """A browser context with the strict policy installed; allows localhost only."""
    if request.param == "chromium":
        sync_api = pytest.importorskip("playwright.sync_api")
        exe = os.environ.get("FANTOMA_TEST_CHROMIUM") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        pw = browser = None
        try:
            pw = sync_api.sync_playwright().start()
            browser = pw.chromium.launch(**({"executable_path": exe} if os.path.exists(exe) else {}))
        except Exception as e:
            if pw:
                pw.stop()
            pytest.skip(f"no Chromium: {e}")
        context = browser.new_context()
        try:
            yield context
        finally:
            try:
                context.close()
            finally:
                browser.close()
                pw.stop()
    else:
        cf = pytest.importorskip("camoufox.sync_api")
        try:
            cm = cf.Camoufox(headless=True)
            browser = cm.__enter__()
        except Exception as e:
            pytest.skip(f"no Camoufox: {e}")
        try:
            yield browser.new_context()
        finally:
            cm.__exit__(None, None, None)


def _page(ctx, policy=None):
    (policy or DomainPolicy(allowed=["localhost"], strict=True)).install(ctx)
    return ctx.new_page()


# ── 1. The browser must not follow a 3xx on its own ──────────

class TestBrowserNeverFollowsA3xx:
    def test_get_navigation_second_answer_is_checked(self, ctx, stateful):
        port, hits, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/r1", timeout=10000)
        settle_hops(page)
        page.wait_for_timeout(1500)
        assert not _forbidden(hits), hits
        assert "SECRET" not in page.content()

    def test_clean_get_chain_lands_on_the_final_url(self, ctx, stateful):
        port, hits, counts = stateful
        counts["/r2"] = 0
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/r1", timeout=10000)
        settle_hops(page)
        assert page.url == f"http://localhost:{port}/r2"
        assert page.inner_text("#m") == "r2 clean"

    def test_history_has_no_hop_page(self, ctx, stateful):
        if "firefox" in ctx.browser.browser_type.name:
            pytest.skip("Camoufox's history.back() / page.go_back() does nothing, with or without a policy")
        port, hits, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/page", timeout=10000)
        page.goto(f"http://localhost:{port}/after", timeout=10000)
        page.goto(f"http://localhost:{port}/s1", timeout=10000)  # /s1 -> /s2 (200 text)
        settle_hops(page)
        page.go_back()
        settle_hops(page)
        assert page.url.endswith("/after"), page.url

    def test_subresource_second_answer_is_checked(self, ctx, stateful):
        port, hits, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/page", timeout=10000)
        text = page.evaluate("(p) => fetch('/s1').then(r => r.text())", port)
        assert text == "s2 clean"
        page.wait_for_timeout(500)
        assert not _forbidden(hits), hits

    def test_subresource_chain_to_a_refused_host_fails(self, ctx, stateful):
        port, hits, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/page", timeout=10000)
        out = page.evaluate("() => fetch('/sbad').then(r => 'ok:' + r.status, e => 'err')")
        assert out == "err"
        page.wait_for_timeout(500)
        assert not _forbidden(hits), hits

    def test_post_303_chain_is_checked(self, ctx, stateful):
        port, hits, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/form303", timeout=10000)
        page.evaluate("document.getElementById('f').submit()")
        page.wait_for_timeout(2500)
        settle_hops(page)
        assert not _forbidden(hits), hits
        assert "SECRET" not in page.content()

    def test_post_303_clean_lands_with_a_get(self, ctx, stateful):
        port, hits, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/formok", timeout=10000)
        page.evaluate("document.getElementById('f').submit()")
        page.wait_for_timeout(1500)
        settle_hops(page)
        assert page.url == f"http://localhost:{port}/after"
        assert [h for h in hits if h[2] == "/after"][0][1] == "GET"

    @pytest.mark.parametrize("kind", ["307", "308"])
    def test_post_307_navigation_is_refused(self, ctx, stateful, kind):
        port, hits, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/form{kind}", timeout=10000)
        page.evaluate("document.getElementById('f').submit()")
        page.wait_for_timeout(2500)
        assert not _forbidden(hits), hits
        assert not [h for h in hits if h[2] == "/r1"]   # never followed at all
        assert "SECRET" not in page.content()

    def test_post_302_subresource_follow_up_carries_no_body(self, ctx, stateful):
        port, hits, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/page", timeout=10000)
        text = page.evaluate("""() => fetch('/sub302', {method: 'POST', body: 'pw=hunter2',
                                headers: {'Content-Type': 'text/plain'}}).then(r => r.text())""")
        assert text == "got"
        follow = [h for h in hits if h[2] == "/g"]
        assert follow and follow[0][1] == "GET"
        assert follow[0][3] in (None, "0") and follow[0][4] is None and follow[0][5] == b""


# ── 3. WebSocket guard cannot be bypassed ────────────────────

_WS_PROBE = """() => {
  const out = {};
  const url = 'ws://127.0.0.1:9/';
  const tryit = (name, f) => { try { f(); out[name] = 'allowed'; } catch (e) { out[name] = e.name; } };
  tryit('direct', () => new WebSocket(url));
  tryit('proto', () => new (WebSocket.prototype.constructor)(url));
  const G = window.WebSocket;  // (Camoufox hands out a fresh object for the bare name, so compare via window)
  try { window.WebSocket = function () {}; } catch (e) {}
  try { delete window.WebSocket; } catch (e) {}
  out.same = (window.WebSocket === G);
  tryit('after_delete', () => new window.WebSocket(url));
  const f = document.createElement('iframe'); document.body.appendChild(f);
  tryit('iframe', () => new f.contentWindow.WebSocket(url));
  tryit('iframe_proto', () => new (f.contentWindow.WebSocket.prototype.constructor)(url));
  return out;
}"""


class TestWebSocketGuard:
    def test_prototype_constructor_and_reassignment_do_not_bypass(self, ctx, stateful):
        port, _, _ = stateful
        page = _page(ctx)
        page.goto(f"http://localhost:{port}/page", timeout=10000)
        out = page.evaluate(_WS_PROBE)
        assert out["direct"] == "SecurityError"
        assert out["proto"] == "SecurityError"
        assert out["same"] is True
        assert out["after_delete"] == "SecurityError"
        assert out["iframe"] == "SecurityError", out
        assert out["iframe_proto"] == "SecurityError", out


# ── 6. DNS lookups ───────────────────────────────────────────

@pytest.mark.real_dns
class TestDnsPool:
    def test_pool_is_sixteen(self):
        assert domains._RESOLVE_WORKERS == 16

    def test_same_host_lookups_share_one_worker(self, monkeypatch):
        import socket
        import time
        gate = threading.Event()
        calls = []

        def slow(host, *a, **k):
            calls.append(host)
            gate.wait(3)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", slow)
        results = []
        threads = [threading.Thread(target=lambda: results.append(domains._resolve_all("dedupe.example.org")))
                   for _ in range(40)]
        for t in threads:
            t.start()
        time.sleep(0.3)
        gate.set()
        for t in threads:
            t.join(5)
        assert len(results) == 40, "no lookup was refused as busy"
        assert calls == ["dedupe.example.org"]


# ── 4 and 5. Server ──────────────────────────────────────────

@pytest.fixture
def srv():
    spec = importlib.util.spec_from_file_location("fantoma_server_fixe", os.path.join(ROOT, "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.API_KEY = ""
    mod.INSECURE_NO_AUTH = False
    mod.ALLOW_PRIVATE = False
    mod.app.config["TESTING"] = True
    mod._fantoma = MagicMock()
    return mod


LOOP = {"REMOTE_ADDR": "127.0.0.1"}


class TestKeylessHostCheck:
    @pytest.mark.parametrize("host", ["attacker.example:7860", "attacker.example", "127.0.0.1.evil.example:7860",
                                      "localhost.evil.example", "10.0.0.5:7860"])
    def test_foreign_host_is_403(self, srv, host):
        c = srv.app.test_client()
        r = c.get("/state", headers={"Host": host, "Sec-Fetch-Site": "same-origin"}, environ_overrides=LOOP)
        assert r.status_code == 403 and r.is_json
        r = c.post("/stop", headers={"Host": host}, json={}, environ_overrides=LOOP)
        assert r.status_code == 403

    @pytest.mark.parametrize("host", ["localhost:7860", "localhost", "127.0.0.1:7860", "[::1]:7860", "[::1]",
                                      "LOCALHOST:7860"])
    def test_loopback_hosts_pass(self, srv, host):
        r = srv.app.test_client().get("/state", headers={"Host": host}, environ_overrides=LOOP)
        assert r.status_code != 403

    def test_health_is_exempt(self, srv):
        r = srv.app.test_client().get("/health", headers={"Host": "attacker.example"}, environ_overrides=LOOP)
        assert r.status_code == 200

    def test_with_a_key_the_host_is_not_checked(self, srv):
        srv.API_KEY = "k"
        r = srv.app.test_client().get("/state", headers={"Host": "fantoma.internal.example", "X-API-Key": "k"},
                                      environ_overrides=LOOP)
        assert r.status_code != 403


class TestEveryPostNeedsJson:
    def test_empty_post_without_content_type_is_415(self, srv):
        r = srv.app.test_client().post("/stop", environ_overrides=LOOP)
        assert r.status_code == 415 and r.is_json

    def test_empty_form_post_is_415(self, srv):
        r = srv.app.test_client().post("/stop", data=b"", content_type="application/x-www-form-urlencoded",
                                       environ_overrides=LOOP)
        assert r.status_code == 415

    def test_empty_post_with_json_content_type_works(self, srv):
        r = srv.app.test_client().post("/stop", data=b"", content_type="application/json",
                                       environ_overrides=LOOP)
        assert r.status_code == 200

    def test_applies_with_a_key_too(self, srv):
        srv.API_KEY = "k"
        r = srv.app.test_client().post("/stop", headers={"X-API-Key": "k"}, environ_overrides=LOOP)
        assert r.status_code == 415
