"""Final fixes: service worker lock (real browsers), hop forgery stall, zstd fallback."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fantoma.browser.domains import DomainPolicy, settle_hops
from fantoma.browser.engine import BrowserEngine

HITS = []

REG = """async ([u, how]) => {
    let reg;
    try {
      if (how === 'proto') reg = await ServiceWorkerContainer.prototype.register.call(navigator.serviceWorker, '/sw.js');
      else if (how === 'delete') {
        try { delete navigator.serviceWorker.register; } catch (e) {}
        reg = await navigator.serviceWorker.register('/sw.js');
      } else if (how === 'iframe') {
        const f = document.createElement('iframe'); document.body.appendChild(f);
        const w = f.contentWindow;
        reg = await w.ServiceWorkerContainer.prototype.register.call(w.navigator.serviceWorker, '/sw.js');
      }
    } catch (e) { return 'threw:' + e.name; }
    if (!reg) return 'undefined';
    await Promise.race([navigator.serviceWorker.ready, new Promise(r => setTimeout(r, 3000))]);
    const sw = reg.active || reg.installing || reg.waiting;
    if (!sw) return 'no-worker';
    return await new Promise(res => {
        navigator.serviceWorker.addEventListener('message', ev => res(ev.data));
        sw.postMessage(u);
        setTimeout(() => res('timeout'), 4000);
    });
}"""


@pytest.fixture(scope="module")
def servers():
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            HITS.append((self.server.server_address[0], self.path))
            if self.server.server_address[0] == "127.0.0.2":
                body, ct = b"SECRET", "text/plain"
            elif self.path == "/sw.js":
                body = (b"self.addEventListener('message', ev => { fetch(ev.data).then(r => r.text())"
                        b".then(t => ev.source.postMessage('ok:' + t), e => ev.source.postMessage('err'));});")
                ct = "application/javascript"
            elif self.path == "/zst":
                body, ct = b"\x28\xb5\x2f\xfd junk", "text/plain"
            else:
                body, ct = b"<h1>page</h1>", "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ct)
            if self.path == "/zst":
                self.send_header("Content-Encoding", "zstd")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    s1 = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = s1.server_address[1]
    try:
        s2 = ThreadingHTTPServer(("127.0.0.2", port), H)
    except OSError:
        s2 = None
    for s in (s1, s2):
        if s:
            threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        yield port, s2 is not None
    finally:
        for s in (s1, s2):
            if s:
                s.shutdown()
                s.server_close()


@pytest.fixture(params=["chromium", "camoufox"])
def engine(request):
    e = BrowserEngine(headless=True, browser_engine=request.param,
                      allowed_domains=["localhost", "127.0.0.1"])
    try:
        e.start()
    except Exception as ex:
        try:
            e.stop()
        except Exception:
            pass
        pytest.skip(f"no {request.param}: {ex}")
    try:
        yield e
    finally:
        e.stop()


@pytest.mark.parametrize("how", ["proto", "delete", "iframe"])
def test_service_worker_register_bypasses_do_not_reach_refused_host(engine, servers, how):
    port, has_alias = servers
    if not has_alias:
        pytest.skip("127.0.0.2 not available")
    page = engine._page
    page.goto(f"http://localhost:{port}/p", timeout=10000)
    HITS.clear()
    try:  # Patchright evaluates in an isolated world by default; a page's own script runs in the main one
        r = page.evaluate(REG, [f"http://127.0.0.2:{port}/s", how], isolated_context=False)
    except TypeError:
        r = page.evaluate(REG, [f"http://127.0.0.2:{port}/s", how])
    assert not r.startswith("ok:"), r
    assert not [h for h in HITS if h[0] == "127.0.0.2"]
    assert not [h for h in HITS if h[1] == "/sw.js"]


def test_hop_forgery_stalls_once(servers):
    class P:
        url = "http://x.test/fake"

        def __init__(self):
            self.waits = 0

        def evaluate(self, *_a):
            return True

        def wait_for_timeout(self, ms):
            self.waits += 1
            import time
            time.sleep(ms / 1000)

    import fantoma.browser.domains as d
    pol = DomainPolicy(allowed=["x.test"], strict=True)
    pol._hop_urls.add("http://x.test/fake")
    page = P()
    orig = d._policy_of
    d._policy_of = lambda pg: pol
    try:
        assert settle_hops(page, timeout_ms=200) is False
        assert not pol.is_hop_url(page.url)
        before = page.waits
        assert settle_hops(page, timeout_ms=200) is True
        assert page.waits == before
    finally:
        d._policy_of = orig
        d._POLICIES.pop(page, None)


def test_zstd_always_answered_is_retried_then_aborted(servers, caplog):
    import logging
    port, _ = servers
    e = BrowserEngine(headless=True, browser_engine="chromium", allowed_domains=["localhost"])
    try:
        e.start()
    except Exception as ex:
        pytest.skip(f"no Chromium: {ex}")
    try:
        HITS.clear()
        with caplog.at_level(logging.WARNING, logger="fantoma.domains"):
            with pytest.raises(Exception):
                e._page.goto(f"http://localhost:{port}/zst", timeout=10000)
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "retrying with identity" in msgs and "aborted" in msgs
        assert len([h for h in HITS if h[1] == "/zst"]) == 2
    finally:
        e.stop()


@pytest.fixture(params=[True, False], ids=["locked", "unlocked-control"])
def vanilla_page(request, servers):
    """Stock Playwright Chromium (what the auditor used), with and without the lock."""
    sync_api = pytest.importorskip("playwright.sync_api")
    from fantoma.browser.stealth import apply_sw_lock
    pw = browser = None
    try:
        pw = sync_api.sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as ex:
        if pw:
            pw.stop()
        pytest.skip(f"no stock Chromium: {ex}")
    try:
        ctx = browser.new_context(service_workers="block")
        DomainPolicy(allowed=["localhost", "127.0.0.1"], strict=True).install(ctx)
        if request.param:
            apply_sw_lock(ctx)
        yield request.param, ctx.new_page()
    finally:
        browser.close()
        pw.stop()


def test_stock_chromium_lock(vanilla_page, servers):
    locked, page = vanilla_page
    port, has_alias = servers
    if not has_alias:
        pytest.skip("127.0.0.2 not available")
    page.goto(f"http://localhost:{port}/p", timeout=10000)
    HITS.clear()
    r = page.evaluate(REG, [f"http://127.0.0.2:{port}/s", "proto"])
    reached = [h for h in HITS if h[0] == "127.0.0.2"]
    if locked:
        assert not reached and not [h for h in HITS if h[1] == "/sw.js"], r
    elif not reached:
        pytest.skip("this Chromium build did not reproduce the bypass; control is moot")
