#!/usr/bin/env python3
"""Live check for stable element numbers. No LLM needed.

Serves a page with a list. One button reorders the list and inserts a new
item at the top; another removes an item; another adds a second button that
looks the same as an existing one. The script reads the page, acts, then uses
an element number from BEFORE the change and checks what happened:

* after the reorder and insert, the old number still hits the same item;
* after a removal, the old number is refused with "is gone, re-read the page";
* after a look-alike appears, the old number is refused and nothing is clicked.

    python tools/stable_refs_live.py                    # Camoufox, headless
    python tools/stable_refs_live.py --browser chromium

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

PAGE = b"""<!doctype html><html><head><title>Stable refs</title></head><body>
<h1>List</h1>
<div id="result" role="status">none</div>
<button id="shuffle" onclick="shuffle()">Shuffle</button>
<button id="remove" onclick="removeB()">Remove B</button>
<button id="dup" onclick="dup()">Duplicate Save</button>
<ul id="list"></ul>
<button id="swaprows" onclick="swapRows()">Swap rows</button>
<ul id="rows"></ul>
<script>
let rows = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo'];
function drawRows() {
  const ul = document.getElementById('rows'); ul.innerHTML = '';
  for (const n of rows) {
    const li = document.createElement('li'); const a = document.createElement('a');
    a.href = '#' + n; a.textContent = 'Row ' + n;
    const b = document.createElement('button'); b.textContent = 'Remove';
    b.onclick = () => { document.getElementById('result').textContent = 'removed ' + n; };
    li.appendChild(a); li.appendChild(b); ul.appendChild(li);
  }
}
function swapRows() { [rows[1], rows[3]] = [rows[3], rows[1]]; drawRows(); }
drawRows();
let items = ['Item A', 'Item B', 'Item C', 'Item D'];
function draw() {
  const ul = document.getElementById('list'); ul.innerHTML = '';
  for (const n of items) {
    const li = document.createElement('li'); const b = document.createElement('button');
    b.textContent = n; b.onclick = () => { document.getElementById('result').textContent = 'hit ' + n; };
    li.appendChild(b); ul.appendChild(li);
  }
}
function shuffle() { items = ['Item New'].concat(items.slice().reverse()); draw(); }
function removeB() { items = items.filter(x => x !== 'Item B'); draw(); }
function dup() {
  const b = document.createElement('button'); b.textContent = 'Save';
  b.onclick = () => { document.getElementById('result').textContent = 'hit Save (second)'; };
  document.body.insertBefore(b, document.getElementById('list'));
}
items.push('Save'); draw();
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


FAILS = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(label)


def number_of(browser, name):
    return next(e["index"] for e in browser._dom._last_interactive
                if e["role"] == "button" and e["name"] == name)


def result_text(browser):
    return browser._engine.get_page().inner_text("#result").strip()


def fresh(browser, url):
    browser.navigate(url)
    browser._dom.reset_refs()
    browser.get_state()


def run(browser, url):
    print("reorder and insert")
    fresh(browser, url)
    item_b = number_of(browser, "Item B")
    shuffle = number_of(browser, "Shuffle")
    r = browser.click(shuffle)           # reorders; the action re-reads the page
    check("shuffle clicked", r["success"])
    check("Item B kept its number after re-read", number_of(browser, "Item B") == item_b)
    check("the new item got a new number",
          number_of(browser, "Item New") not in (item_b, shuffle, number_of(browser, "Item A")))
    r = browser.click(item_b)            # number from before the change
    check("old number hit Item B", result_text(browser) == "hit Item B", result_text(browser))
    check("click reported success", r["success"])

    print("reorder with no re-read in between")
    fresh(browser, url)
    item_c = number_of(browser, "Item C")
    browser._engine.get_page().click("#shuffle")      # page changes behind our back
    el = browser._dom.get_element_by_index(browser._engine.get_page(), item_c)
    check("resolved without a new read", el is not None)
    if el is not None:
        el.click()
        check("hit Item C", result_text(browser) == "hit Item C", result_text(browser))

    print("element removed")
    fresh(browser, url)
    item_b = number_of(browser, "Item B")
    browser.click(number_of(browser, "Remove B"))
    r = browser.click(item_b)
    check("refused", r["success"] is False)
    check("message says gone", any("is gone, re-read the page" in n for n in r.get("notes", [])),
          str(r.get("notes")))
    check("nothing was clicked", result_text(browser) == "none", result_text(browser))

    print("look-alike appears")
    fresh(browser, url)
    save = number_of(browser, "Save")
    browser.click(number_of(browser, "Duplicate Save"))
    r = browser.click(save)
    check("refused", r["success"] is False)
    check("message asks for a re-read", any("re-read the page" in n for n in r.get("notes", [])),
          str(r.get("notes")))
    check("nothing was clicked", result_text(browser) == "none", result_text(browser))
    new_save = [e["index"] for e in browser._dom._last_interactive
                if e["name"] == "Save"]
    check("after a re-read both Saves are addressable", len(set(new_save)) == 2, str(new_save))


def run_same_length_reorder(browser, url):
    print("same-length reorder of identical buttons with different row labels")
    fresh(browser, url)
    page = browser._engine.get_page()
    bravo = next((e["index"] for e in browser._dom._last_interactive
                  if e["name"] == "Remove" and e.get("_context") == "Row Bravo"), None)
    check("Remove for Row Bravo is listed with its row label", bravo is not None)
    if bravo is None:
        return
    page.click("#swaprows")                      # rows 2 and 4 swap, same count
    el = browser._dom.get_element_by_index(page, bravo)
    check("resolved to a live element", el is not None)
    if el is not None:
        el.click()
        check("hit the Bravo row, not whatever sits at its old position",
              result_text(browser) == "removed Bravo", result_text(browser))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="camoufox", choices=["camoufox", "chromium"])
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    browser = Fantoma(headless=True, browser=args.browser)
    print(f"stable refs live check  browser={args.browser}")
    try:
        browser.start()
        run(browser, url)
        run_same_length_reorder(browser, url)
    finally:
        try:
            browser.stop()
        except Exception as e:
            print("stop failed:", e)
        server.shutdown()
    print(f"{len(FAILS)} failed" if FAILS else "all passed")
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
