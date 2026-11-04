"""Third-audit fixes: redirect chains behind POST, CSRF on the keyless server,
MCP Host checks, proxy handling, error handling, DNS pool."""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from fantoma.browser import domains
from fantoma.browser.domains import DomainPolicy

ROOT = os.path.dirname(os.path.dirname(__file__))


class _Route:
    """Minimal Playwright route: scripted responses by URL, records methods."""

    def __init__(self, url, chain, method="GET"):
        self.request = MagicMock(url=url, method=method,
                                 headers={"cookie": "s=1", "accept": "*/*", "content-type": "text/plain"})
        self.request.is_navigation_request.return_value = False  # subresource unless a test says otherwise
        self.posts = []
        self.chain = chain
        self.fetched = []
        self.methods = []
        self.aborted = None
        self.fulfilled = None

    def fetch(self, url=None, headers=None, max_redirects=None, method=None, post_data=None):
        assert max_redirects == 0
        u = url or self.request.url
        self.fetched.append((u, headers))
        self.methods.append(method)
        self.posts.append(post_data)
        status, loc = self.chain[u]
        return MagicMock(status=status, headers={"location": loc} if loc else {})

    def abort(self, reason="failed"):
        self.aborted = reason

    def fulfill(self, **kw):
        self.fulfilled = kw


def _run(policy, route):
    policy._strict_fetch(route, route.request.url)


# ── 1. Redirect chains behind a POST ─────────────────────────

class TestPostChains:
    """Subresource requests: the handler walks the chain itself."""

    @pytest.mark.parametrize("status", [301, 302, 303])
    def test_post_chain_to_forbidden_host_is_blocked(self, status):
        r = _Route("http://a.test/", {"http://a.test/": (status, "/b"),
                                      "http://a.test/b": (302, "http://169.254.169.254/latest")},
                   method="POST")
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "blockedbyclient" and r.fulfilled is None
        # followed as GET, without the body or its headers
        assert r.methods == [None, "GET"]
        assert r.posts[1] == b""
        assert not {"content-type", "content-length", "transfer-encoding"} & {k.lower() for k in r.fetched[1][1]}

    def test_post_303_chain_is_answered_with_the_final_response(self):
        r = _Route("http://a.test/", {"http://a.test/": (303, "/b"), "http://a.test/b": (302, "/c"),
                                      "http://a.test/c": (200, None)}, method="POST")
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert list(r.fulfilled) == ["response"] and r.methods == [None, "GET", "GET"]

    @pytest.mark.parametrize("status", [307, 308])
    def test_post_307_cross_origin_is_refused(self, status):
        r = _Route("http://a.test/", {"http://a.test/": (status, "http://b.a.test/")}, method="POST")
        r.request.post_data_buffer = b"secret=1"
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "blockedbyclient" and len(r.fetched) == 1

    def test_post_307_endless_same_origin_chain_aborts(self):
        r = _Route("http://a.test/", {"http://a.test/": (307, "/b"), "http://a.test/b": (307, "/b")},
                   method="POST")
        r.request.post_data_buffer = b"x"
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "failed"

    @pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
    def test_navigation_redirect_is_answered_with_a_hop_page(self, status):
        r = _Route("http://a.test/", {"http://a.test/": (status, "/b")})
        r.request.is_navigation_request.return_value = True
        r.request.frame.parent_frame = None
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.fulfilled["status"] == 200 and len(r.fetched) == 1   # the browser makes the next request
        body = r.fulfilled["body"]
        assert "data-fantoma-hop" in body and 'location.replace("http://a.test/b")' in body
        assert r.fulfilled["headers"]["x-fantoma-hop"] == "1" and "no-store" in r.fulfilled["headers"]["cache-control"]
        assert "'none'" in r.fulfilled["headers"]["content-security-policy"]

    def test_navigation_redirect_to_a_forbidden_host_is_aborted(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "http://evil.test/")})
        r.request.is_navigation_request.return_value = True
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "blockedbyclient" and r.fulfilled is None

    @pytest.mark.parametrize("status", [307, 308])
    def test_post_307_navigation_is_refused(self, status):
        r = _Route("http://a.test/", {"http://a.test/": (status, "/b")}, method="POST")
        r.request.is_navigation_request.return_value = True
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "blockedbyclient" and r.fulfilled is None

    def test_non_http_location_is_refused(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "data:text/html,hi")})
        r.request.is_navigation_request.return_value = True
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "blockedbyclient"

    def test_hop_page_escapes_the_location(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "/x?a=</script><script>alert(1)//\"&b")})
        r.request.is_navigation_request.return_value = True
        _run(DomainPolicy(allowed=["a.test"]), r)
        body = r.fulfilled["body"]
        assert "</script><script>" not in body and "alert(1)" not in body.split("<script", 1)[1].split("</script>")[1]


# ── 11. Hop limit ────────────────────────────────────────────

def _chain(n):
    """n redirects then a 200, all on a.test."""
    c = {"http://a.test/": (302, "/h1")}
    for i in range(1, n):
        c[f"http://a.test/h{i}"] = (302, f"/h{i + 1}")
    c[f"http://a.test/h{n}"] = (200, None)
    return c


def test_exactly_ten_redirects_pass():
    r = _Route("http://a.test/", _chain(10))
    _run(DomainPolicy(allowed=["a.test"]), r)
    assert r.aborted is None and "response" in r.fulfilled


def test_eleven_redirects_abort():
    r = _Route("http://a.test/", _chain(11))
    _run(DomainPolicy(allowed=["a.test"]), r)
    assert r.aborted == "failed"


# ── 1. Real browsers: form POST -> 303 -> 302 -> refused host ─

@pytest.fixture(scope="module")
def post_server():
    hits = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _record(self):
            hits.append((self.command, f"{self.headers.get('Host', '').split(':')[0]}{self.path}"))

        def _send(self, status, headers=(), body=b""):
            self.send_response(status)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._record()
            port = self.server.server_address[1]
            if self.path.startswith("/form"):
                target = self.path.split("?t=")[-1] if "?t=" in self.path else "bad"
                page = (f"<form id=f method=post action='/{target}'><input name=x value=1></form>"
                        "<script>document.getElementById('f').submit()</script>").encode()
                self._send(200, [("Content-Type", "text/html")], page)
            elif self.path.startswith("/b"):
                self._send(302, [("Location", f"http://127.0.0.2:{port}/c")])
            else:
                self._send(200, [("Content-Type", "text/html")], b"<p>landed</p>")

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            self._record()
            if self.path.startswith("/bad"):
                self._send(303, [("Location", "/b")])
            else:
                self._send(303, [("Location", "/ok")])

    server = ThreadingHTTPServer(("0.0.0.0", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()


def _post_check(ctx, port, hits):
    DomainPolicy(allowed=["localhost"], strict=True).install(ctx)
    page = ctx.new_page()
    page.goto(f"http://localhost:{port}/form?t=ok", timeout=10000)
    page.wait_for_url(f"http://localhost:{port}/ok", timeout=8000)
    assert "landed" in page.content()
    assert ("GET", "localhost/ok") in hits
    hits.clear()
    try:
        page.goto(f"http://localhost:{port}/form?t=bad", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(2500)  # not time.sleep: route handlers only run while Playwright waits
    assert not [h for h in hits if h[1].startswith("127.0.0.2")], hits
    assert "landed" not in page.content() or page.url.endswith("/form?t=bad")


class TestPostChainInRealBrowsers:
    def test_chromium(self, post_server):
        sync_api = pytest.importorskip("playwright.sync_api")
        port, hits = post_server
        hits.clear()
        exe = os.environ.get("FANTOMA_TEST_CHROMIUM") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        pw = browser = None
        try:
            pw = sync_api.sync_playwright().start()
            browser = pw.chromium.launch(**({"executable_path": exe} if os.path.exists(exe) else {}))
        except Exception as e:
            if pw:
                pw.stop()
            pytest.skip(f"no Chromium: {e}")
        try:
            _post_check(browser.new_context(), port, hits)
        finally:
            browser.close()
            pw.stop()

    def test_camoufox(self, post_server):
        cf = pytest.importorskip("camoufox.sync_api")
        port, hits = post_server
        hits.clear()
        try:
            cm = cf.Camoufox(headless=True)
            browser = cm.__enter__()
        except Exception as e:
            pytest.skip(f"no Camoufox: {e}")
        try:
            _post_check(browser.new_context(), port, hits)
        finally:
            cm.__exit__(None, None, None)


# ── server fixtures ──────────────────────────────────────────

@pytest.fixture
def srv():
    spec = importlib.util.spec_from_file_location("fantoma_server_fixd", os.path.join(ROOT, "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.API_KEY = ""
    mod.INSECURE_NO_AUTH = False
    mod.ALLOW_PRIVATE = False
    mod.app.config["TESTING"] = True
    mod._fantoma = MagicMock()
    return mod


LOOP = {"REMOTE_ADDR": "127.0.0.1"}


# ── 2. CSRF on the keyless loopback server ───────────────────

class TestKeylessCsrf:
    def test_text_plain_body_is_415(self, srv):
        r = srv.app.test_client().post("/run", data='{"task":"x"}', content_type="text/plain",
                                       environ_overrides=LOOP)
        assert r.status_code == 415 and r.is_json

    def test_no_content_type_with_a_body_is_415(self, srv):
        r = srv.app.test_client().post("/run", data='{"task":"x"}', environ_overrides=LOOP)
        assert r.status_code == 415

    def test_empty_json_post_works(self, srv):
        assert srv.app.test_client().post("/stop", json={}, environ_overrides=LOOP).status_code == 200

    def test_json_content_type_with_charset_is_fine(self, srv):
        r = srv.app.test_client().post("/run", data='{}', content_type="application/json; charset=utf-8",
                                       environ_overrides=LOOP)
        assert r.status_code == 400 and "task" in r.get_json()["error"]

    @pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Origin": "null"},
                                         {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}])
    def test_browser_requests_get_403_when_keyless(self, srv, headers):
        c = srv.app.test_client()
        assert c.post("/run", json={"task": "x"}, headers=headers, environ_overrides=LOOP).status_code == 403
        assert c.get("/state", headers=headers, environ_overrides=LOOP).status_code == 403

    @pytest.mark.parametrize("headers", [{}, {"Sec-Fetch-Site": "none"}, {"Sec-Fetch-Site": "same-origin"}])
    def test_scripts_and_curl_pass(self, srv, headers):
        r = srv.app.test_client().post("/run", json={"task": ""}, headers=headers, environ_overrides=LOOP)
        assert r.status_code == 400  # reached the handler

    def test_health_is_exempt(self, srv):
        r = srv.app.test_client().get("/health", headers={"Origin": "https://x.example"},
                                      environ_overrides=LOOP)
        assert r.status_code == 200

    def test_with_a_key_origin_is_not_refused(self, srv):
        srv.API_KEY = "k"
        r = srv.app.test_client().post("/run", json={"task": ""}, headers={
            "Origin": "https://app.example", "X-API-Key": "k"}, environ_overrides=LOOP)
        assert r.status_code == 400


# ── 4. Proxy headers in the keyless path ─────────────────────

class TestKeylessBehindProxy:
    @pytest.mark.parametrize("h", ["X-Forwarded-For", "Forwarded", "X-Real-IP", "Via"])
    def test_proxy_headers_get_503(self, srv, h):
        r = srv.app.test_client().get("/state", headers={h: "203.0.113.9"}, environ_overrides=LOOP)
        assert r.status_code == 503 and "key" in r.get_json()["error"]

    def test_with_a_key_they_pass(self, srv):
        srv.API_KEY = "k"
        r = srv.app.test_client().get("/state", headers={"X-Forwarded-For": "1.2.3.4", "X-API-Key": "k"},
                                      environ_overrides=LOOP)
        assert r.status_code != 503

    def test_health_is_exempt(self, srv):
        r = srv.app.test_client().get("/health", headers={"X-Forwarded-For": "1.2.3.4"},
                                      environ_overrides=LOOP)
        assert r.status_code == 200


# ── 6. Proxy re-serialised ───────────────────────────────────

class TestProxyParsing:
    @pytest.mark.parametrize("proxy", [
        "http://user@evil.example@proxy.example.org:3128",
        "http://proxy.example.org:3128,http://127.0.0.1:3128",
        "http://proxy.example.org:3128;x",
        "http://proxy.example.org:3128 http://127.0.0.1:1",
        "http://proxy.example.org\t:3128",
        "http://proxy.example.org:3128/path",
    ])
    def test_ambiguous_strings_are_refused(self, srv, proxy):
        assert srv._proxy_problem(proxy)

    def test_rebuilt_from_parsed_parts_with_credentials_split_out(self, srv):
        out, err = srv._parse_proxy("socks5://al%40ice:p%3Aw@proxy.example.org:1080")
        assert err is None
        assert out == {"server": "socks5://proxy.example.org:1080", "username": "al@ice", "password": "p:w"}
        out, err = srv._parse_proxy("proxy.example.org:3128")
        assert out == {"server": "http://proxy.example.org:3128"}

    def test_request_proxy_hands_over_the_rebuilt_dict(self, srv):
        srv.ALLOW_REQUEST_PROXY = True
        got = srv._request_proxy({"proxy": "http://u:p@proxy.example.org:3128"})
        assert got == {"server": "http://proxy.example.org:3128", "username": "u", "password": "p"}


# ── 7. /login has no request proxy ───────────────────────────

def test_login_ignores_a_proxy_field(srv):
    srv._fantoma.login.return_value = {"success": True}
    r = srv.app.test_client().post("/login", json={"url": "https://shop.example.org/",
                                                   "proxy": "http://127.0.0.1:3128"},
                                   environ_overrides=LOOP)
    assert r.status_code == 200


# ── 8. Error handling ────────────────────────────────────────

class TestErrors:
    def test_library_value_error_is_a_500_not_a_400(self, srv):
        srv._fantoma.press_key.side_effect = ValueError("internal detail /etc/x")
        r = srv.app.test_client().post("/press_key", json={"key": "Enter"}, environ_overrides=LOOP)
        assert r.status_code == 500 and "/etc/x" not in r.get_data(as_text=True)

    def test_library_key_error_does_not_echo_the_key(self, srv):
        srv._fantoma.click.side_effect = KeyError("hidden_internal_name")
        r = srv.app.test_client().post("/click", json={"element_id": 1}, environ_overrides=LOOP)
        assert r.status_code == 500 and "hidden_internal_name" not in r.get_data(as_text=True)

    def test_caller_mistakes_are_400_with_our_text(self, srv):
        c = srv.app.test_client()
        r = c.post("/click", json={}, environ_overrides=LOOP)
        assert r.status_code == 400 and r.get_json()["error"] == "missing field 'element_id'"
        r = c.post("/read", json={"max_chars": "lots"}, environ_overrides=LOOP)
        assert r.status_code == 400

    def test_malformed_json_is_a_json_400(self, srv):
        r = srv.app.test_client().post("/click", data="{not json", content_type="application/json",
                                       environ_overrides=LOOP)
        assert r.status_code == 400 and r.is_json

    def test_unknown_route_is_json(self, srv):
        r = srv.app.test_client().get("/nope", environ_overrides=LOOP)
        assert r.status_code == 404 and r.is_json

    def test_logged_text_is_masked(self, srv, caplog):
        srv._fantoma.click.side_effect = RuntimeError("boom hunter2")
        with caplog.at_level("ERROR"):
            srv.app.test_client().post("/click", json={"element_id": 1}, environ_overrides=LOOP)
        assert "boom" in caplog.text  # logged, via _masked
        assert srv._masked("boom hunter2", {"password": "hunter2"}) == "boom [REDACTED]"


# ── 9. allowed_domains cleaning does no DNS ──────────────────

def test_clean_allowed_domains_never_resolves(srv, monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: calls.append(a) or [])
    out, err = srv._clean_allowed_domains(["a.example.org", "*.b.example.org"] * 40)
    assert err is None and calls == []
    assert srv._clean_allowed_domains(["127.1"])[1] and srv._clean_allowed_domains(["x.lan"])[1]


# ── 10. DNS pool is bounded ──────────────────────────────────

@pytest.mark.real_dns
def test_hung_lookups_cannot_add_threads(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: release.wait(5) or [])
    before = threading.active_count()
    errors = []
    try:
        for i in range(domains._RESOLVE_WORKERS + 4):
            try:
                domains._resolve_all(f"slow{i}.example.org", timeout=0.05)
            except OSError as e:
                errors.append(str(e))
        assert threading.active_count() - before <= domains._RESOLVE_WORKERS
        assert any("busy" in e for e in errors)  # saturated pool fails closed
        assert domains._resolves_private("another.example.org") is True
    finally:
        release.set()
        time.sleep(0.3)
    # the pool recovers once the resolver answers
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))])
    assert domains._resolves_private("fine.example.org") is False


# ── 5. WebSocket guard knows about private hosts ─────────────

_NODE = shutil.which("node")


def _ws_verdicts(urls, policy):
    script = domains._ws_guard_script(policy.allowed, policy.blocked, policy.block_private)
    harness = """
const vm = require('vm');
const script = %s, urls = %s;
class WS { constructor(u) { this.u = u; } }
WS.CONNECTING = 0; WS.OPEN = 1; WS.CLOSING = 2; WS.CLOSED = 3;
const win = {WebSocket: WS, location: {href: 'http://page.example/'}};
const ctx = vm.createContext({window: win, location: {href: 'http://page.example/'}, URL, DOMException});
vm.runInContext(script, ctx);
const out = {};
for (const u of urls) { try { new win.WebSocket(u); out[u] = true; } catch (e) { out[u] = false; } }
console.log(JSON.stringify(out));
""" % (json.dumps(script), json.dumps(urls))
    res = subprocess.run([_NODE, "-e", harness], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@pytest.mark.skipif(not _NODE, reason="node not installed")
class TestWsGuardPrivate:
    PRIVATE = ["ws://127.0.0.1/", "ws://127.1/", "ws://0x7f000001/", "ws://2130706433/", "ws://0177.0.0.1/",
               "ws://10.1.2.3/", "ws://172.16.0.1/", "ws://192.168.1.1/", "ws://169.254.169.254/",
               "ws://100.64.0.9/", "ws://0.0.0.0/", "ws://[::1]/", "ws://[::]/", "ws://[fe80::1]/",
               "ws://[fd00::1]/", "ws://[::ffff:127.0.0.1]/", "ws://[::ffff:a9fe:a9fe]/",
               "ws://[64:ff9b::7f00:1]/", "ws://localhost/", "ws://app.localhost/", "ws://printer.local/",
               "ws://nas.lan/", "ws://box.home/", "ws://r.home.arpa/", "ws://svc.internal/",
               "ws://metadata.google.internal/", "ws://metadata/", "ws://localhost./"]
    PUBLIC = ["ws://example.org/", "ws://93.184.216.34/", "ws://[2606:4700::1111]/", "ws://100.63.0.1/",
              "ws://172.32.0.1/", "ws://lan.example.org/", "ws://internal.example.org/"]

    def test_private_hosts_are_refused_with_block_private_alone(self):
        got = _ws_verdicts(self.PRIVATE + self.PUBLIC, DomainPolicy(block_private=True))
        assert [u for u in self.PRIVATE if got[u]] == []
        assert [u for u in self.PUBLIC if not got[u]] == []

    def test_without_block_private_nothing_changes(self):
        got = _ws_verdicts(["ws://127.0.0.1/"], DomainPolicy(blocked=["x.test"]))
        assert got["ws://127.0.0.1/"] is True

    def test_allow_list_may_name_a_private_host(self):
        got = _ws_verdicts(["ws://127.0.0.1/", "ws://10.0.0.1/"], DomainPolicy(allowed=["127.0.0.1"], block_private=True))
        assert got == {"ws://127.0.0.1/": True, "ws://10.0.0.1/": False}


def test_guard_is_injected_for_block_private_alone():
    ctx = MagicMock()
    DomainPolicy(block_private=True).install(ctx)
    ctx.add_init_script.assert_called_once()
    assert "blockPrivate = true" in ctx.add_init_script.call_args[0][0]


# ── 3 and 12. MCP HTTP ───────────────────────────────────────

def _mcp_client(host, token, extra=""):
    from mcp.server.fastmcp import FastMCP
    from starlette.testclient import TestClient
    from fantoma import mcp_server
    m = FastMCP("fantoma-test")
    security = mcp_server._transport_security(host, token, extra)
    if security is not None:
        m.settings.transport_security = security
    app = m.streamable_http_app()
    if token:
        app = mcp_server._BearerAuth(app, token)
    return TestClient(app)


_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"}}}
_HDRS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class TestMcpHosts:
    def _post(self, c, host, token="tok"):
        return c.post("/mcp", json=_INIT, headers={**_HDRS, "Host": host,
                                                   "Authorization": f"Bearer {token}"})

    def test_library_default_rejects_a_non_loopback_host(self):
        """The bug: construction with the loopback default, host changed later."""
        from mcp.server.fastmcp import FastMCP
        from starlette.testclient import TestClient
        m = FastMCP("x")
        m.settings.host = "0.0.0.0"
        with TestClient(m.streamable_http_app()) as c:
            assert c.post("/mcp", json=_INIT, headers={**_HDRS, "Host": "mcp.example.org:8767"}).status_code == 421

    def test_named_bind_address_is_accepted(self):
        with _mcp_client("192.0.2.10", "tok") as c:
            assert self._post(c, "192.0.2.10:8767").status_code == 200
            assert self._post(c, "evil.example:8767").status_code == 421

    def test_wildcard_bind_with_token_accepts_any_host(self):
        with _mcp_client("0.0.0.0", "tok") as c:
            assert self._post(c, "mcp.example.org:8767").status_code == 200

    def test_wildcard_bind_with_named_hosts_restricts_to_them(self):
        with _mcp_client("0.0.0.0", "tok", "mcp.example.org") as c:
            assert self._post(c, "mcp.example.org").status_code == 200
            assert self._post(c, "mcp.example.org:8767").status_code == 200
            assert self._post(c, "evil.example").status_code == 421

    def test_token_is_still_required(self):
        with _mcp_client("0.0.0.0", "tok") as c:
            assert self._post(c, "mcp.example.org", token="wrong").status_code == 401

    def test_loopback_keeps_the_library_default(self):
        from fantoma import mcp_server
        assert mcp_server._transport_security("127.0.0.1", "", "") is None


def test_bearer_scheme_is_case_insensitive():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient
    from fantoma.mcp_server import _BearerAuth
    c = TestClient(_BearerAuth(Starlette(routes=[Route("/x", lambda r: PlainTextResponse("in"))]), "s3cret"))
    for scheme in ("Bearer", "bearer", "BEARER"):
        assert c.get("/x", headers={"Authorization": f"{scheme} s3cret"}).text == "in"
    assert c.get("/x", headers={"Authorization": "Basic s3cret"}).status_code == 401
    assert c.get("/x", headers={"Authorization": "bearer nope"}).status_code == 401
    assert c.get("/x", headers={"Authorization": "bearer"}).status_code == 401
