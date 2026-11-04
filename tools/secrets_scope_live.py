#!/usr/bin/env python3
"""Live check for site-scoped secrets. No LLM needed.

Serves one page on two hostnames that both point at this machine
(127.0.0.1 and localhost), scopes a secret to the first, then drives the
real browser through Fantoma's secret-revealing wrapper:

* on the first site the placeholder is replaced and the value is typed;
* on the second site the fill is refused and the field stays empty.

    python tools/secrets_scope_live.py                 # Camoufox, headless
    python tools/secrets_scope_live.py --browser chromium

Exit code is the number of failed checks.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fantoma.browser_tool import Fantoma  # noqa: E402
from fantoma.fast_path import SecretRefused, _Revealing  # noqa: E402

VALUE = "live-check-S3cret"
PAGE = b"""<html><body><h1>Sign in</h1>
<input type="password" aria-label="Password" id="p"></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


def _try_fill(browser, secrets, url):
    """Open `url`, try to type <secret:pw>; return (typed_value, error)."""
    browser.navigate(url)
    browser.get_state()
    idx = browser._dom.find_by_signature("textbox", "Password")
    if idx is None:
        return None, "password box not found"
    try:
        _Revealing(browser, secrets).type_text(idx, "<secret:pw>")
    except SecretRefused as e:
        err = str(e)
    else:
        err = ""
    return browser._engine.get_page().input_value("#p"), err


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="camoufox")
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    site_a = f"http://127.0.0.1:{port}/"
    site_b = f"http://localhost:{port}/"
    secrets = {f"http://127.0.0.1:{port}": {"pw": VALUE}}

    failures = 0
    browser = Fantoma(headless=True, browser=args.browser)
    try:
        browser.start()
        typed, err = _try_fill(browser, secrets, site_a)
        ok = typed == VALUE and not err
        print(f"site A {site_a}: {'ALLOWED, value typed' if ok else 'FAIL'}"
              f" (field holds {len(typed or '')} chars)")
        failures += not ok

        typed, err = _try_fill(browser, secrets, site_b)
        ok = typed == "" and "not allowed on this site" in err
        print(f"site B {site_b}: {'REFUSED' if ok else 'FAIL'}"
              f" (field holds {len(typed or '')} chars; error: {err!r})")
        failures += not ok
    finally:
        try:
            browser.stop()
        finally:
            server.shutdown()
    print("PASS" if not failures else f"{failures} FAILED")
    return failures


if __name__ == "__main__":
    sys.exit(main())
