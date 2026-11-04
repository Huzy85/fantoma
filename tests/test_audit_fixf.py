"""Fifth-audit fixes: cross-origin subresource redirects refused, one URL
parser check for every fetch, service workers blocked, hop-page loop limit,
no Referer or lost fragment on hop pages, hop pages trusted only when the
policy served them, nav_status keyed without fragment."""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock
from urllib.parse import parse_qs, quote

import pytest

from fantoma.browser.domains import DomainPolicy, parse_url, resolve_location, settle_hops, on_hop_page


# ── mock route, as in the earlier audit tests ────────────────

class _Route:
    def __init__(self, url, chain, method="GET", nav=False, headers=None):
        self.request = MagicMock(url=url, method=method, headers={"accept": "*/*"})
        self.request.is_navigation_request.return_value = nav
        self.request.frame.parent_frame = None
        self.chain, self.fetched, self.aborted, self.fulfilled = chain, [], None, None
        self.extra = headers or {}

    def fetch(self, url=None, headers=None, max_redirects=None, method=None, post_data=None):
        u = url or self.request.url
        self.fetched.append(u)
        status, loc = self.chain[u]
        h = {"location": loc} if loc else {}
        h.update(self.extra)
        return MagicMock(status=status, headers=h)

    def abort(self, reason="failed"):
        self.aborted = reason

    def fulfill(self, **kw):
        self.fulfilled = kw


def _run(policy, route):
    policy._strict_fetch(route, route.request.url)


# ── 1. cross-origin subresource redirects ────────────────────

class TestSubresourceOrigin:
    @pytest.mark.parametrize("target", ["http://b.a.test/x", "https://a.test/x", "http://a.test:81/x"])
    def test_any_origin_change_is_refused_before_a_fetch(self, target):
        r = _Route("http://a.test/", {"http://a.test/": (302, target)})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "blockedbyclient" and r.fulfilled is None and len(r.fetched) == 1

    def test_origin_change_on_a_later_hop_is_refused(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "/b"), "http://a.test/b": (302, "http://b.a.test/c")})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted == "blockedbyclient" and r.fetched == ["http://a.test/", "http://a.test/b"]

    def test_same_origin_chain_walks_and_set_cookie_is_stripped(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "/b"), "http://a.test/b": (200, None)},
                   headers={"set-cookie": "x=1", "Set-Cookie2": "y=2", "content-type": "text/plain"})
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.aborted is None
        assert {k.lower() for k in r.fulfilled["headers"]} == {"content-type"}

    def test_a_navigation_still_crosses_origins_through_a_hop_page(self):
        r = _Route("http://a.test/", {"http://a.test/": (302, "http://b.a.test/")}, nav=True)
        _run(DomainPolicy(allowed=["a.test"]), r)
        assert r.fulfilled["status"] == 200 and "data-fantoma-hop" in r.fulfilled["body"]


# ── 2. one parser check ──────────────────────────────────────

class TestParseUrl:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.2:80\\@localhost:80/x",
        "http://169.254.169.254\\@allowed.example/",
        "http://allowed.example\\.evil.example/",
        "http://a b@allowed.example/",
        "http://x\t.evil/",
        "http://user\x01@allowed.example/",
    ])
    def test_refused(self, url):
        assert parse_url(url) is None
        assert not DomainPolicy(allowed=["allowed.example", "localhost"]).permits(url)

    def test_normal_urls_parse(self):
        assert parse_url("https://user:pw@example.com:8443/a\\b?q=1\\2#f").hostname == "example.com"
        assert parse_url("about:blank").scheme == "about"

    @pytest.mark.parametrize("loc", ["/\\evil.example/x", "\\\\evil.example/x", "/a\tb", "http://a.test\\@b/"])
    def test_locations_with_backslashes_or_controls_are_refused(self, loc):
        assert resolve_location("http://a.test/p", loc) is None

    def test_relative_locations_resolve(self):
        assert resolve_location("http://a.test/p/q", "../r?x=1") == "http://a.test/r?x=1"

    def test_server_url_check_uses_it(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fantoma_server_fixf", os.path.join(os.path.dirname(os.path.dirname(__file__)), "server.py"))
        srv = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(srv)
        srv.ALLOW_PRIVATE = False
        assert srv._url_problem("http://127.0.0.2:80\\@example.com/") is not None


# ── 4 5 6 8 9: hop page behaviour ────────────────────────────

class TestHopPage:
    def _nav(self, policy, url="http://a.test/", to="/b#keep"):
        r = _Route(url, {url: (302, to)}, nav=True)
        _run(policy, r)
        return r

    def test_loop_is_refused_after_ten_hop_pages(self):
        p = DomainPolicy(allowed=["a.test"])
        r = _Route("http://a.test/", {"http://a.test/": (302, "/b")}, nav=True)
        for _ in range(10):
            r.fulfilled = r.aborted = None
            _run(p, r)
            assert r.fulfilled is not None
        r.fulfilled = None
        _run(p, r)
        assert r.aborted == "blockedbyclient" and r.fulfilled is None

    def test_a_real_answer_resets_the_run(self):
        p = DomainPolicy(allowed=["a.test"])
        hop = _Route("http://a.test/", {"http://a.test/": (302, "/b")}, nav=True)
        real = _Route("http://a.test/ok", {"http://a.test/ok": (200, None)}, nav=True)
        real.request.frame = hop.request.frame      # same frame
        for _ in range(9):
            _run(p, hop)
        _run(p, real)
        for _ in range(10):
            hop.fulfilled = hop.aborted = None
            _run(p, hop)
            assert hop.fulfilled is not None
        _run(p, hop)
        assert hop.aborted == "blockedbyclient"

    def test_hop_page_sends_no_referrer_and_keeps_the_fragment(self):
        r = self._nav(DomainPolicy(allowed=["a.test"]), to="/b")
        assert 'name="referrer" content="no-referrer"' in r.fulfilled["body"]
        assert r.fulfilled["headers"]["referrer-policy"] == "no-referrer"
        assert 't.indexOf("#")<0' in r.fulfilled["body"] and "t+=location.hash" in r.fulfilled["body"]

    def test_only_served_urls_count_as_hop_pages(self):
        p = DomainPolicy(allowed=["a.test"])
        assert not p.is_hop_url("http://a.test/")
        self._nav(p)
        assert p.is_hop_url("http://a.test/#frag")
        ok = _Route("http://a.test/", {"http://a.test/": (200, None)}, nav=True)
        _run(p, ok)
        assert not p.is_hop_url("http://a.test/")

    def test_page_with_the_attribute_but_no_policy_record_is_not_a_hop(self):
        page = MagicMock(url="http://evil.test/")
        page.evaluate.return_value = True
        assert on_hop_page(page) is False
        assert settle_hops(page, 200) is True
        page.evaluate.assert_not_called()

    def test_nav_status_ignores_fragments(self):
        p = DomainPolicy()
        p._remember_status("http://x/y#a", 404)
        assert p.status_for("http://x/y#b") == 404 and p.status_for("http://x/y") == 404


# ── real browsers ────────────────────────────────────────────

@pytest.fixture
def server():
    hits, holder = [], {}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", loc=None, ctype="text/html", extra=()):
            self.send_response(code)
            if loc:
                self.send_header("Location", loc)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            iface = self.server.server_address[0]
            hits.append({"iface": iface, "path": self.path, "ref": self.headers.get("Referer"),
                         "cookie": self.headers.get("Cookie")})
            p, _, q = self.path.partition("?")
            port = holder["port"]
            if iface == "127.0.0.2":
                return self._send(200, b"SECRET", ctype="text/plain",
                                  extra=[("Access-Control-Allow-Origin", "*")])
            if p == "/redir":
                return self._send(302, loc=parse_qs(q).get("to", [""])[0])
            if p == "/private":
                return self._send(200, b"PRIVATE", ctype="text/plain",
                                  extra=[("Set-Cookie", "injected=1; Path=/")])
            if p == "/page":
                return self._send(200, b"<h1 id=m>page</h1>")
            if p == "/cycA":
                return self._send(302, loc=f"http://localhost:{port}/cycB")
            if p == "/cycB":
                return self._send(302, loc=f"http://localhost:{port}/cycA")
            if p == "/r1":
                return self._send(302, loc=f"http://localhost:{port}/after")
            if p == "/after":
                return self._send(200, b"<h1 id=m>after</h1>")
            if p == "/fake":
                return self._send(200, b"<html data-fantoma-hop='1'><body>fake</body></html>")
            if p == "/sw.js":
                return self._send(200, b"self.addEventListener('message', ev => { fetch(ev.data)"
                                  b".then(r => r.text()).then(t => ev.source.postMessage('ok:' + t),"
                                  b" e => ev.source.postMessage('err'))});", ctype="application/javascript")
            return self._send(404, b"nf")

    s1 = ThreadingHTTPServer(("127.0.0.1", 0), H)
    holder["port"] = s1.server_address[1]
    servers = [s1]
    try:
        servers.append(ThreadingHTTPServer(("127.0.0.2", holder["port"]), H))
    except OSError:
        pass
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        yield holder["port"], hits
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()


@pytest.fixture(params=["chromium", "camoufox"])
def browser_ctx(request):
    """A context with service workers blocked, as the engine makes it."""
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
        try:
            yield browser.new_context(service_workers="block")
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
            yield browser.new_context(service_workers="block")
        finally:
            cm.__exit__(None, None, None)


_FETCH = "(u) => fetch(u).then(r => r.text().then(t => 'ok:' + r.status + ':' + t), e => 'err:' + e.name)"


class TestRealBrowsers:
    def _setup(self, ctx, port):
        DomainPolicy(allowed=["localhost", "127.0.0.1"], strict=True).install(ctx)
        ctx.add_cookies([{"name": "auth", "value": "1", "url": f"http://127.0.0.1:{port}/"}])
        page = ctx.new_page()
        page.goto(f"http://localhost:{port}/page", timeout=10000)
        return page

    def test_cross_origin_subresource_redirect_is_refused_and_no_cookie_lands(self, browser_ctx, server):
        port, hits = server
        page = self._setup(browser_ctx, port)
        hits.clear()
        r = page.evaluate(_FETCH, f"http://localhost:{port}/redir?to=http://127.0.0.1:{port}/private")
        assert r.startswith("err:")
        assert not [h for h in hits if h["path"].startswith("/private")]
        assert not [c for c in browser_ctx.cookies(f"http://localhost:{port}/") if c["name"] == "injected"]
        ok = page.evaluate(_FETCH, f"http://localhost:{port}/redir?to=/page")
        assert ok.startswith("ok:200")

    def test_backslash_location_is_refused_for_fetch_and_navigation(self, browser_ctx, server):
        port, hits = server
        page = self._setup(browser_ctx, port)
        hits.clear()
        bad = quote(f"http://127.0.0.2:{port}\\@localhost:{port}/x", safe="")
        assert page.evaluate(_FETCH, f"http://localhost:{port}/redir?to={bad}").startswith("err:")
        page2 = browser_ctx.new_page()
        with pytest.raises(Exception):
            page2.goto(f"http://localhost:{port}/redir?to={bad}", timeout=8000)
        assert not [h for h in hits if h["iface"] == "127.0.0.2"]

    def test_service_worker_cannot_reach_a_refused_host(self, browser_ctx, server):
        port, hits = server
        page = self._setup(browser_ctx, port)
        hits.clear()
        try:
            out = page.evaluate("""async (u) => {
                const reg = await Promise.race([navigator.serviceWorker.register('/sw.js'),
                    new Promise((_, j) => setTimeout(() => j(new Error('timeout')), 4000))]);
                const sw = reg.active || reg.installing || reg.waiting;
                return await Promise.race([new Promise(res => {
                    navigator.serviceWorker.addEventListener('message', ev => res(ev.data)); sw.postMessage(u); }),
                    new Promise(res => setTimeout(() => res('timeout'), 4000))]);
            }""", f"http://127.0.0.2:{port}/s")
        except Exception:
            out = "blocked"
        assert not str(out).startswith("ok:")
        assert not [h for h in hits if h["iface"] == "127.0.0.2"]

    def test_redirect_loop_ends_fast(self, browser_ctx, server):
        port, hits = server
        page = self._setup(browser_ctx, port)
        hits.clear()
        try:
            page.goto(f"http://localhost:{port}/cycA", timeout=8000)
        except Exception:
            pass
        settle_hops(page, 5000)
        n = len(hits)
        page.wait_for_timeout(1500)
        assert n <= 14 and len(hits) == n

    def test_no_referrer_and_fragment_kept(self, browser_ctx, server):
        port, hits = server
        page = self._setup(browser_ctx, port)
        hits.clear()
        page.evaluate("(u) => { const a = document.createElement('a'); a.href = u; a.id = 'l'; a.textContent = 'x'; "
                      "document.body.appendChild(a); }", f"http://localhost:{port}/r1?token=SECRET")
        page.click("#l")
        settle_hops(page)
        assert [h["ref"] for h in hits if h["path"] == "/after"] == [None]
        page.goto(f"http://localhost:{port}/r1#frag", timeout=10000)
        settle_hops(page)
        assert page.url.endswith("/after#frag")

    def test_a_page_with_the_hop_attribute_does_not_stall_settle(self, browser_ctx, server):
        import time
        port, _ = server
        page = self._setup(browser_ctx, port)
        page.goto(f"http://localhost:{port}/fake", timeout=10000)
        t0 = time.monotonic()
        assert settle_hops(page) is True and time.monotonic() - t0 < 1.5
