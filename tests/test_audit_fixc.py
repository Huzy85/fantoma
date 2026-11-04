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
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"dev.lan": ["192.168.1.5"]}))
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
        _, err = srv._clean_allowed_domains(["localtest.example.org"])
        assert err and "private" in err
        ok, err = srv._clean_allowed_domains(["fine.example.org"])
        assert err is None and ok == ["fine.example.org"]


# ── 2. Multi-hop redirects ───────────────────────────────────

class _Route:
    """Minimal Playwright route: scripted responses by URL."""
    def __init__(self, url, chain, method="GET"):
        self.request = MagicMock(url=url, method=method, headers={"cookie": "s=1", "accept": "*/*"})
        self.chain = chain
        self.fetched = []
        self.aborted = None
        self.fulfilled = None

    def fetch(self, url=None, headers=None, max_redirects=None):
        assert max_redirects == 0
        u = url or self.request.url
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

    def test_verified_chain_is_returned_as_one_redirect_to_the_end(self):
        r = _Route("http://a.test/", {"http://a.test/": (301, "/b"), "http://a.test/b": (302, "/c"),
                                      "http://a.test/c": (200, None)})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.fulfilled["status"] == 302 and r.fulfilled["headers"]["location"] == "http://a.test/c"

    def test_cookie_is_not_forwarded_on_followed_hops(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "/b"), "http://a.test/b": (200, None)})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert "cookie" not in {k.lower() for k in r.fetched[1][1]}

    def test_loop_stops_after_ten_hops(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "/"), })
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "failed" and len(r.fetched) == 11

    def test_post_307_is_checked_but_not_replayed(self):
        r = _Route("http://a.test/", {"http://a.test/": (307, "/b")}, method="POST")
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert len(r.fetched) == 1 and r.fulfilled is not None

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
    time.sleep(1.5)
    with pytest.raises(Exception):
        page.goto(f"http://localhost:{port}/a", timeout=8000)
    time.sleep(0.5)
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
