"""Second-audit fixes: DNS-aware private check, multi-hop redirects, CGNAT."""

import ipaddress
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from fantoma.browser import domains
from fantoma.browser.domains import DomainPolicy, _is_private_host, _resolves_private
from fantoma.netutil import is_private_address


def _fake_getaddrinfo(mapping, calls=None):
    def fake(host, port, *a, **k):
        if calls is not None:
            calls.append(host)
        if host not in mapping:
            raise socket.gaierror(-2, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]]
    return fake


# ── 3. CGNAT and name rules ──────────────────────────────────

@pytest.mark.parametrize("host", ["100.64.0.1", "100.127.255.254", "printer.lan", "nas.home",
                                  "router.home.arpa", "host.docker.internal"])
def test_cgnat_and_local_suffixes_are_private(host):
    assert _is_private_host(host) is True


@pytest.mark.parametrize("host", ["100.63.255.255", "100.128.0.1", "example.com", "lan.example.com"])
def test_neighbours_of_cgnat_are_public(host):
    assert _is_private_host(host) is False


def test_client_and_domains_share_one_definition():
    from fantoma.llm.client import is_self_hosted
    assert is_private_address(ipaddress.ip_address("100.100.1.1"))
    assert is_self_hosted("http://100.100.1.1:8080/v1")
    assert is_self_hosted("http://box.lan:8080/v1")


# ── 1. DNS-aware block_private ───────────────────────────────

@pytest.mark.real_dns
class TestDnsResolution:
    def test_name_pointing_at_loopback_is_refused(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"127.0.0.1.nip.io": ["127.0.0.1"]}))
        p = DomainPolicy(block_private=True)
        assert p.permits("http://127.0.0.1.nip.io/") is False

    def test_any_private_answer_refuses(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo",
                            _fake_getaddrinfo({"mix.example.org": ["93.184.216.34", "169.254.169.254"]}))
        assert DomainPolicy(block_private=True).permits("https://mix.example.org/") is False

    def test_cgnat_answer_refuses(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"ts.example.org": ["100.88.1.2"]}))
        assert DomainPolicy(block_private=True).permits("https://ts.example.org/") is False

    def test_public_answer_passes(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"ok.example.org": ["93.184.216.34"]}))
        assert DomainPolicy(block_private=True).permits("https://ok.example.org/") is True

    def test_lookup_failure_fails_closed_only_in_block_private(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({}))
        assert DomainPolicy(block_private=True).permits("https://nope.example.org/") is False
        assert DomainPolicy(blocked=["other.example"]).permits("https://nope.example.org/") is True
        assert DomainPolicy().permits("https://nope.example.org/") is True

    def test_non_block_private_never_resolves(self, monkeypatch):
        calls = []
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"a.example.org": ["127.0.0.1"]}, calls))
        assert DomainPolicy(blocked=["x.test"]).permits("http://a.example.org/") is True
        assert calls == []

    def test_cache_is_used_then_expires(self, monkeypatch):
        calls = []
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"c.example.org": ["93.184.216.34"]}, calls))
        p = DomainPolicy(block_private=True)
        p.permits("http://c.example.org/a")
        p.permits("http://c.example.org/b")
        assert calls == ["c.example.org"]
        monkeypatch.setattr(domains, "_RESOLVE_TTL", 0.0)
        p.permits("http://c.example.org/c")
        assert calls == ["c.example.org", "c.example.org"]

    def test_hung_resolver_times_out_and_refuses(self, monkeypatch):
        release = threading.Event()

        def hang(*a, **k):
            release.wait(5)
            return []
        monkeypatch.setattr(socket, "getaddrinfo", hang)
        t = time.monotonic()
        try:
            with pytest.raises(OSError):
                domains._resolve_all("slow.example.org", timeout=0.2)
            assert time.monotonic() - t < 2
        finally:
            release.set()

    def test_literal_ips_are_not_looked_up(self, monkeypatch):
        calls = []
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({}, calls))
        assert _resolves_private("8.8.8.8") is False
        assert _resolves_private("127.0.0.1") is True
        assert calls == []

    def test_explicit_private_allow_entry_skips_lookup(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"dev.lan": ["10.1.2.5"]}))
        assert DomainPolicy(allowed=["dev.lan"], block_private=True).permits("http://dev.lan/") is True

    def test_server_url_problem_resolves(self, monkeypatch):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fantoma_server_fixc", os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"))
        srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(srv)
        srv.ALLOW_PRIVATE = False
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(
            {"localtest.example.org": ["127.0.0.1"], "fine.example.org": ["93.184.216.34"]}))
        assert srv._url_problem("http://localtest.example.org/") is not None
        assert srv._url_problem("http://fine.example.org/") is None
        # Cleaning does no DNS (a hundred entries would outlast the watchdog);
        # literals and local suffixes are still refused textually.
        _, err = srv._clean_allowed_domains(["localtest.example.org"])
        assert err is None
        _, err = srv._clean_allowed_domains(["127.0.0.1"])
        assert err and "private" in err
        ok, err = srv._clean_allowed_domains(["fine.example.org"])
        assert err is None and ok == ["fine.example.org"]


# ── 2. Multi-hop redirects ───────────────────────────────────

class _Route:
    """Minimal Playwright route: scripted responses by URL."""
    def __init__(self, url, chain, method="GET"):
        self.request = MagicMock(url=url, method=method, headers={"cookie": "s=1", "accept": "*/*"})
        self.request.is_navigation_request.return_value = False  # subresource: the handler walks the chain
        self.chain = chain
        self.fetched = []
        self.methods = []
        self.posts = []
        self.aborted = None
        self.fulfilled = None

    def fetch(self, url=None, headers=None, max_redirects=None, method=None, post_data=None):
        assert max_redirects == 0
        u = url or self.request.url
        self.methods.append(method)
        self.posts.append(post_data)
        self.fetched.append((u, headers))
        status, loc = self.chain[u]
        return MagicMock(status=status, headers={"location": loc} if loc else {})

    def abort(self, reason="failed"):
        self.aborted = reason

    def fulfill(self, **kw):
        self.fulfilled = kw


def _run(policy, route):
    policy._strict_fetch(route, route.request.url)


class TestMultiHop:
    def test_second_hop_to_forbidden_host_is_blocked(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "http://a.test/b"),
                                      "http://a.test/b": (302, "http://evil.test/c")})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "blockedbyclient" and r.fulfilled is None
        assert [u for u, _ in r.fetched] == ["http://a.test/", "http://a.test/b"]

    def test_verified_chain_is_answered_with_the_final_response_never_a_3xx(self):
        r = _Route("http://a.test/", {"http://a.test/": (301, "/b"), "http://a.test/b": (302, "/c"),
                                      "http://a.test/c": (200, None)})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert "response" in r.fulfilled and r.fulfilled["response"].status == 200 and "set-cookie" not in {k.lower() for k in r.fulfilled.get("headers", {})}

    def test_cookie_is_not_forwarded_on_followed_hops(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "/b"), "http://a.test/b": (200, None)})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert "cookie" not in {k.lower() for k in r.fetched[1][1]}

    def test_loop_stops_after_ten_hops(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "/"), })
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "failed" and len(r.fetched) == 11

    def test_post_307_same_origin_subresource_replays_the_body(self):
        r = _Route("http://a.test/", {"http://a.test/": (307, "/b"), "http://a.test/b": (200, None)},
                   method="POST")
        r.request.post_data_buffer = b"x=1"
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.methods == [None, "POST"] and r.posts == [None, b"x=1"]
        assert r.fulfilled["response"].status == 200

    def test_plain_response_is_fulfilled_untouched(self):
        r = _Route("http://a.test/", {"http://a.test/": (200, None)})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert "response" in r.fulfilled


@pytest.fixture(scope="module")
def hop_server():
    """A -> B -> C where C sits on 127.0.0.2, a host the policy does not allow."""
    hits = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(f"{self.headers.get('Host', '').split(':')[0]}{self.path}")
            port = self.server.server_address[1]
            if self.path.startswith("/a"):
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{port}/b")
                self.end_headers()
                return
            if self.path.startswith("/b"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.2:{port}/c")
                self.end_headers()
                return
            body = b"<img src='/a.png'><script>fetch('/a.json').catch(()=>{})</script>" if self.path == "/page" else b"secret"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("0.0.0.0", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1], hits
    server.shutdown()
    server.server_close()


def _check_chain(ctx, port, hits):
    DomainPolicy(allowed=["localhost"], strict=True).install(ctx)
    page = ctx.new_page()
    page.goto(f"http://localhost:{port}/page", timeout=8000)
    page.wait_for_timeout(1500)  # not time.sleep: the sync API only runs route handlers while waiting
    try:
        page.goto(f"http://localhost:{port}/a", timeout=8000)  # a hop page now, so no error here
    except Exception:
        pass
    page.wait_for_timeout(1500)
    assert not [h for h in hits if h.startswith("127.0.0.2")], hits


class TestMultiHopInRealBrowsers:
    def test_chromium(self, hop_server):
        sync_api = pytest.importorskip("playwright.sync_api")
        port, hits = hop_server
        hits.clear()
        exe = os.environ.get("FANTOMA_TEST_CHROMIUM") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        try:
            pw = sync_api.sync_playwright().start()
            browser = pw.chromium.launch(**({"executable_path": exe} if os.path.exists(exe) else {}))
        except Exception as e:
            pytest.skip(f"no Chromium: {e}")
        try:
            ctx = browser.new_context()
            _check_chain(ctx, port, hits)
        finally:
            browser.close()
            pw.stop()

    def test_camoufox(self, hop_server):
        cf = pytest.importorskip("camoufox.sync_api")
        port, hits = hop_server
        hits.clear()
        try:
            cm = cf.Camoufox(headless=True)
            browser = cm.__enter__()
        except Exception as e:
            pytest.skip(f"no Camoufox: {e}")
        try:
            _check_chain(browser.new_context(), port, hits)
        finally:
            cm.__exit__(None, None, None)


# ── 8. MCP HTTP auth ─────────────────────────────────────────

def test_mcp_http_refuses_non_loopback_without_token(monkeypatch):
    from fantoma import mcp_server
    assert mcp_server._http_problem("0.0.0.0", "", False)
    assert mcp_server._http_problem("0.0.0.0", "tok", False) is None
    assert mcp_server._http_problem("0.0.0.0", "", True) is None
    assert mcp_server._http_problem("127.0.0.1", "", False) is None
    monkeypatch.setenv("FANTOMA_MCP_TRANSPORT", "http")
    monkeypatch.setenv("FANTOMA_MCP_HOST", "0.0.0.0")
    monkeypatch.delenv("FANTOMA_MCP_TOKEN", raising=False)
    monkeypatch.delenv("FANTOMA_MCP_INSECURE", raising=False)
    with pytest.raises(SystemExit):
        mcp_server.main()


def test_mcp_bearer_middleware():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient
    from fantoma.mcp_server import _BearerAuth
    inner = Starlette(routes=[Route("/x", lambda r: PlainTextResponse("in"))])
    c = TestClient(_BearerAuth(inner, "s3cret"))
    assert c.get("/x").status_code == 401
    assert c.get("/x", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/x", headers={"Authorization": "Bearer s3cret"}).text == "in"


# ── 4. Version strings ───────────────────────────────────────

def test_version_strings_match_pyproject():
    import json
    import re
    import fantoma
    root = os.path.dirname(os.path.dirname(__file__))
    want = re.search(r'^version\s*=\s*"([^"]+)"', open(os.path.join(root, "pyproject.toml")).read(), re.M).group(1)
    assert fantoma.__version__ == want
    sj = json.load(open(os.path.join(root, "server.json")))
    assert sj["version"] == want
    assert all(p["version"] == want for p in sj.get("packages", []))


# ── server: 5 (open remote guard, block_private at import), 6, L9 ─────

@pytest.fixture
def srv():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fantoma_server_fixc2", os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.API_KEY = ""
    mod.ALLOW_PRIVATE = False
    mod.app.config["TESTING"] = True
    mod._fantoma = MagicMock()
    return mod


def test_import_turns_block_private_on(monkeypatch):
    import importlib.util
    monkeypatch.delenv("FANTOMA_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("FANTOMA_BLOCK_PRIVATE", "")
    spec = importlib.util.spec_from_file_location(
        "fantoma_server_fixc3", os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"))
    importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    assert os.environ["FANTOMA_BLOCK_PRIVATE"] == "1"


def test_keyless_remote_client_gets_503_but_loopback_and_health_pass(srv):
    c = srv.app.test_client()
    r = c.get("/state", environ_overrides={"REMOTE_ADDR": "10.1.2.3"})
    assert r.status_code == 503
    assert c.get("/health", environ_overrides={"REMOTE_ADDR": "10.1.2.3"}).status_code == 200
    assert c.get("/health", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    assert c.get("/state", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code != 503
    srv.INSECURE_NO_AUTH = True
    assert c.get("/state", environ_overrides={"REMOTE_ADDR": "10.1.2.3"}).status_code != 503


@pytest.mark.parametrize("proxy", ["http://127.0.0.1:3128", "socks5://10.0.0.5:1080", "http://box.lan:3128",
                                   "http://100.64.1.1:3128", "ftp://proxy.example.org:21", "file:///etc/passwd"])
def test_run_rejects_bad_proxy(srv, proxy):
    c = srv.app.test_client()
    r = c.post("/run", json={"task": "x", "proxy": proxy})
    assert r.status_code == 400 and "proxy" in r.get_json()["error"]


def test_public_proxy_is_accepted_by_the_check(srv):
    assert srv._proxy_problem("http://proxy.example.org:3128") is None
    assert srv._proxy_problem("socks5://proxy.example.org:1080") is None
    assert srv._proxy_problem(None) is None
    srv.ALLOW_PRIVATE = True
    assert srv._proxy_problem("http://127.0.0.1:3128") is None


@pytest.mark.parametrize("route,body", [
    ("/press_key", {}), ("/select", {"element_id": 1}), ("/click", {}), ("/type", {"element_id": 1}),
    ("/press_key", [1, 2]), ("/scroll", [1]),
])
def test_handlers_answer_400_json_on_bad_bodies(srv, route, body):
    r = srv.app.test_client().post(route, json=body)
    assert r.status_code == 400 and r.is_json


def test_unexpected_error_is_generic_json_500(srv):
    srv._fantoma.press_key.side_effect = RuntimeError("secret path /etc/x")
    r = srv.app.test_client().post("/press_key", json={"key": "Enter"})
    assert r.status_code == 500 and r.is_json
    assert "/etc/x" not in r.get_data(as_text=True) and "log id" in r.get_json()["error"]


# ── 7. CLI history and log are owner-only ────────────────────

def test_cli_history_and_log_are_0600(tmp_path, monkeypatch):
    from fantoma import cli
    monkeypatch.setattr(cli, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cli, "HISTORY_FILE", tmp_path / "cfg" / "history.json")
    monkeypatch.setattr(cli, "LOG_FILE", tmp_path / "cfg" / "fantoma.log")
    old = os.umask(0o022)
    try:
        cli._add_history("log in with password hunter2", True)
        cli._add_log("hello")
    finally:
        os.umask(old)
    for f in (cli.HISTORY_FILE, cli.LOG_FILE):
        assert (f.stat().st_mode & 0o777) == 0o600
    # A file that already exists with loose permissions is tightened too.
    os.chmod(cli.HISTORY_FILE, 0o644)
    cli._add_history("again", True)
    assert (cli.HISTORY_FILE.stat().st_mode & 0o777) == 0o600


# ── 10. Benchmark notification config ────────────────────────

def test_benchmark_telegram_comes_only_from_env(monkeypatch, tmp_path):
    import importlib.util
    root = os.path.dirname(os.path.dirname(__file__))
    spec = importlib.util.spec_from_file_location("bench_runner_fixc", os.path.join(root, "benchmark", "runner.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for k in ("FANTOMA_BENCH_TELEGRAM_TOKEN", "FANTOMA_BENCH_TELEGRAM_CHAT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mod._load_telegram_config() is None
    monkeypatch.setenv("FANTOMA_BENCH_TELEGRAM_TOKEN", "t")
    monkeypatch.setenv("FANTOMA_BENCH_TELEGRAM_CHAT", "c")
    assert mod._load_telegram_config() == ("t", "c")
    for name in ("runner.py", "run_docker.sh", "run_overnight.sh"):
        assert "nano" + "bot" not in open(os.path.join(root, "benchmark", name)).read()
