"""A signup wizard that stays on one URL, filled by login() with no model.

The April note said multi-step signup was "broken on same-URL SPA
transitions". Measured: the loop itself handled the second step, but the
success check fired after step one, because the page's "Create your
account" heading scored as logged in. These run a real Chromium against a
local three-step wizard and grade the page itself.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fantoma.browser.form_login import (
    _password_box_showing, _tick_consent_boxes, login,
)
from fantoma.dom.accessibility import AccessibilityExtractor

WIZARD = """<html><body><h1>Create your account</h1><div id="app"></div><script>
const app = document.getElementById('app');
function step1(){ app.innerHTML = `<form id="f"><label>Email <input type="email" id="e"></label>
  <button type="submit">Next</button></form>`;
  f.onsubmit = e => { e.preventDefault(); window.email = document.getElementById('e').value; step2(); }; }
function step2(){ app.innerHTML = `<form id="f"><label>Choose a password <input type="password" id="p"></label>
  <label><input type="checkbox" id="t"> I agree to the terms</label>
  <label><input type="checkbox" id="m"> Send me offers and news</label>
  <button type="submit">Create account</button></form>`;
  f.onsubmit = e => { e.preventDefault();
    if (!document.getElementById('t').checked) { document.title = 'terms required'; return; }
    window.offers = document.getElementById('m').checked; step3(); }; }
function step3(){ app.innerHTML = `<h2>Welcome, ` + window.email + `</h2><p>Your account is ready.</p>
  <a href="#">Dashboard</a> <a href="#">Log out</a>`; }
step1();
</script></body></html>"""


@pytest.fixture(scope="module")
def site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = WIZARD.encode()
            self.send_response(200)
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


class _Engine:
    humanizer = None

    def __init__(self, page):
        self._page = page

    def get_page(self):
        return self._page

    def screenshot(self, *a, **k):
        return b""


@pytest.fixture
def page(chromium, site, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    ctx = chromium.new_context()
    p = ctx.new_page()
    p.goto(f"{site}/signup")
    yield p
    ctx.close()


def test_three_step_wizard_on_one_url_is_completed(page):
    result = login(_Engine(page), AccessibilityExtractor(),
                   email="a@b.test", password="Secret123!", max_steps=4)

    assert result["success"] is True
    assert result["steps"] == 2
    assert result["fields_filled"] == ["Email", "Choose a password", "I agree to the terms"]
    assert "Your account is ready" in page.inner_text("body")
    assert page.evaluate("window.email") == "a@b.test"
    # The marketing box is a choice nobody asked us to make.
    assert page.evaluate("window.offers") is False


def test_a_password_box_means_not_done():
    assert _password_box_showing('- heading "Create your account"\n- textbox "Choose a password"')
    assert _password_box_showing('- textbox "Passcode"')
    assert not _password_box_showing('- heading "Welcome"\n- link "Log out"')
    assert not _password_box_showing("")


def test_only_consent_boxes_are_ticked():
    class Handle:
        def __init__(self):
            self.checked = False

        def check(self, timeout=0):
            self.checked = True

    handles = {}

    def get_element(page, dom, el):
        return handles.setdefault(el["name"], Handle())

    elements = [
        {"role": "checkbox", "name": "I agree to the Terms of Service", "state": "", "raw": {}},
        {"role": "checkbox", "name": "I accept the privacy policy", "state": " [checked]", "raw": {"checked": True}},
        {"role": "checkbox", "name": "Email me offers and updates", "state": "", "raw": {}},
        {"role": "checkbox", "name": "Remember me", "state": "", "raw": {}},
        {"role": "button", "name": "Agree and continue", "state": "", "raw": {}},
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("fantoma.browser.form_login._get_element", get_element)
        ticked = _tick_consent_boxes(None, None, elements)

    assert ticked == ["I agree to the Terms of Service"]
    assert set(handles) == {"I agree to the Terms of Service"}
