#!/usr/bin/env python3
"""Live check against real public websites. No LLM needed.

Unit tests prove the code does what it was written to do; this proves it
still works on the real web, where markup, bot protection and browser
builds change underneath it. It drives Fantoma directly (no model), so a
failure is Fantoma's, never a model's.

Two kinds of check:

* read   — open a page, read it as Markdown, and require text that only the
           correct page contains. Also require the page not be reported as
           blocked.
* action — tick a checkbox and choose a dropdown option on public test
           pages made for automation practice, then read the state back
           from the live page.

    python tools/live_read_check.py                 # Camoufox
    python tools/live_read_check.py --browser chromium
    python tools/live_read_check.py --only read

Exit code is the number of failed checks, so CI turns red on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (url, text the correct page contains, lowercased). Sites chosen because
# they exist for testing scrapers or are stable reference pages.
READS = [
    ("https://example.com", "example domain"),
    ("https://books.toscrape.com/", "a light in the attic"),
    ("https://quotes.toscrape.com/", "albert einstein"),
    ("https://en.wikipedia.org/wiki/Python_(programming_language)", "guido van rossum"),
    ("https://docs.python.org/3/library/json.html", "json"),
    ("https://the-internet.herokuapp.com/tables", "jsmith@gmail.com"),
]

CHECKBOXES = "https://the-internet.herokuapp.com/checkboxes"
DROPDOWN = "https://the-internet.herokuapp.com/dropdown"


def _index_of(browser, predicate):
    state = browser.get_state(task="tick the first checkbox; select Option 2")
    del state
    for i, el in enumerate(browser._dom._last_interactive):
        if predicate(el):
            return i
    return None


def check_reads(browser) -> list[dict]:
    results = []
    for url, expect in READS:
        started = time.time()
        try:
            page = browser.read(url)
            md = page["markdown"].lower()
            ok = expect in md and not page["blocked"]
            why = ("" if ok else
                   f"blocked={page['blocked']!r}" if page["blocked"] else
                   f"missing {expect!r} ({len(md)} chars read)")
        except Exception as e:
            ok, why = False, f"error: {e}"
        results.append({"check": f"read {url}", "ok": ok, "why": why,
                        "secs": round(time.time() - started, 1)})
    return results


def check_actions(browser) -> list[dict]:
    results = []

    # Checkbox: the first box starts unticked. Click it through the element
    # list, exactly as a model would, then read the live DOM.
    started = time.time()
    try:
        browser.navigate(CHECKBOXES)
        page = browser._engine.get_page()
        before = page.evaluate("() => document.querySelectorAll('input[type=checkbox]')[0].checked")
        idx = _index_of(browser, lambda el: el["role"] == "checkbox")
        if idx is None:
            raise RuntimeError("no checkbox offered to the model")
        browser.click(idx)
        after = page.evaluate("() => document.querySelectorAll('input[type=checkbox]')[0].checked")
        ok = after != before
        why = "" if ok else f"checkbox unchanged ({before} -> {after})"
    except Exception as e:
        ok, why = False, f"error: {e}"
    results.append({"check": "tick a checkbox", "ok": ok, "why": why,
                    "secs": round(time.time() - started, 1)})

    # Dropdown: click the OPTION, as small models do. Fantoma must turn that
    # into a real selection.
    started = time.time()
    try:
        browser.navigate(DROPDOWN)
        page = browser._engine.get_page()
        idx = _index_of(browser, lambda el: el["role"] == "option" and el["name"] == "Option 2")
        if idx is None:
            raise RuntimeError("'Option 2' not offered to the model")
        browser.click(idx)
        value = page.evaluate("() => document.querySelector('#dropdown').value")
        shown = browser.get_state()["aria_tree"]
        ok = value == "2" and 'selected: "Option 2"' in shown
        why = "" if ok else f"dropdown value={value!r}"
    except Exception as e:
        ok, why = False, f"error: {e}"
    results.append({"check": "choose a dropdown option by clicking it", "ok": ok, "why": why,
                    "secs": round(time.time() - started, 1)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--browser", default="camoufox", choices=["camoufox", "chromium"])
    parser.add_argument("--only", choices=["read", "action"])
    parser.add_argument("--json", help="also write results to this file")
    args = parser.parse_args()

    from fantoma.browser_tool import Fantoma

    browser = Fantoma(headless=True, browser=args.browser)
    browser.start()
    results = []
    try:
        if args.only in (None, "read"):
            results += check_reads(browser)
        if args.only in (None, "action"):
            results += check_actions(browser)
    finally:
        browser.stop()

    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"{mark}  {r['secs']:>5}s  {r['check']}" + (f"  — {r['why']}" if r["why"] else ""))
    failed = sum(1 for r in results if not r["ok"])
    print(f"\n{len(results) - failed}/{len(results)} passed ({args.browser})")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"browser": args.browser, "results": results}, f, indent=2)
    return failed


if __name__ == "__main__":
    sys.exit(main())
