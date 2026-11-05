"""Strict mode must not hand the browser a body it cannot decode.

A server that answers zstd when the client offers it (a few public sites do)
used to reach the page as raw compressed bytes, because the policy's own
fetch advertised zstd and Playwright does not decode it. The policy now asks
for gzip, deflate and br only."""

import gzip
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fantoma.browser.domains import DomainPolicy

PAGE = b"<html><body><table><tr><td id=t>Boston Bruins</td></tr></table></body></html>"


def _zstd(data: bytes):
    try:
        return subprocess.run(["zstd", "-c"], input=data, capture_output=True, check=True).stdout
    except Exception:
        return None


@pytest.fixture
def zstd_server():
    seen = []

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            ae = self.headers.get("Accept-Encoding", "")
            seen.append(ae)
            z = _zstd(PAGE) if "zstd" in ae else None
            if z is not None:
                body, enc = z, "zstd"
            else:
                body, enc = gzip.compress(PAGE), "gzip"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Encoding", enc)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        yield s.server_address[1], seen
    finally:
        s.shutdown()
        s.server_close()


@pytest.fixture(params=["chromium", "camoufox"])
def ctx(request):
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
            yield browser.new_context()
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


def test_strict_mode_never_offers_zstd_and_page_renders(ctx, zstd_server):
    if _zstd(PAGE) is None:
        pytest.skip("zstd binary not installed")
    port, seen = zstd_server
    DomainPolicy(allowed=["127.0.0.1"], strict=True).install(ctx)
    page = ctx.new_page()
    page.goto(f"http://127.0.0.1:{port}/", timeout=10000)
    assert page.inner_text("#t") == "Boston Bruins"
    assert seen and all("zstd" not in a for a in seen)
