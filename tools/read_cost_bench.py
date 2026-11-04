#!/usr/bin/env python3
"""What one navigator step spends on page reads. No model needed.

Opens each page, then runs the same read path a navigator step runs, N times:
ARIA snapshot before, element listing, content summary for the state tracker,
then (as after an action that may have changed the page) the ARIA snapshot
for the diff, the content summary again, and the blocker check.

Reads and evaluates are counted at the Playwright class level, so the numbers
do not depend on Fantoma's own bookkeeping and the same script measures old
and new code.

    python tools/read_cost_bench.py --browser chromium
    python tools/read_cost_bench.py --browser camoufox --runs 5
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAGES = [
    ("wikipedia United_Kingdom", "https://en.wikipedia.org/wiki/United_Kingdom"),
    ("saucedemo login", "https://www.saucedemo.com/"),
]

COUNTS = {"reads": 0, "evals": 0}


def _wrap(cls, name, key):
    original = getattr(cls, name)

    def counted(self, *a, **kw):
        COUNTS[key] += 1
        return original(self, *a, **kw)

    setattr(cls, name, counted)


def install_counters(page) -> None:
    _wrap(type(page), "evaluate", "evals")
    _wrap(type(page.main_frame), "evaluate", "evals")
    loc = page.locator("body")
    _wrap(type(loc), "evaluate", "evals")
    _wrap(type(loc), "aria_snapshot", "reads")


def one_step(browser, page, task: str) -> None:
    """The read path of one navigator step that performs one action."""
    from fantoma.browser.page_state import classify_blocker
    from fantoma.dom.aria_diff import aria_diff, aria_snapshot

    dom = browser._dom
    cached = hasattr(dom, "begin_step")
    if cached:
        snap = dom.begin_step(page)
        before = aria_snapshot(page, snap)
    else:
        before = aria_snapshot(page)
    dom.extract(page, task=task, mode="navigate")
    dom.extract_content(page)[:800]
    # ... the action runs here ...
    if cached:
        dom.invalidate()
        after = aria_snapshot(page, dom._snap_for(page))
    else:
        after = aria_snapshot(page)
    aria_diff(before, after)
    dom.extract_content(page)[:800]
    classify_blocker(page)
    if cached:
        dom.end_step()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="chromium", choices=["chromium", "camoufox"])
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    from fantoma.browser_tool import Fantoma

    browser = Fantoma(headless=True, browser=args.browser)
    print(f"read cost per step  browser={args.browser} {args.label}")
    print(f"{'page':<26}{'reads':>7}{'evals':>7}{'secs/step':>11}{'min':>7}{'max':>7}")
    try:
        browser.start()
        installed = False
        for name, url in PAGES:
            browser.navigate(url)
            page = browser._engine.get_page()
            if not installed:
                install_counters(page)
                installed = True
            one_step(browser, page, "log in")  # warm-up, not counted
            reads, evals, secs = [], [], []
            for _ in range(args.runs):
                COUNTS["reads"] = COUNTS["evals"] = 0
                t0 = time.monotonic()
                one_step(browser, page, "log in")
                secs.append(time.monotonic() - t0)
                reads.append(COUNTS["reads"])
                evals.append(COUNTS["evals"])
            print(f"{name:<26}{statistics.mean(reads):>7.1f}{statistics.mean(evals):>7.1f}"
                  f"{statistics.mean(secs):>11.2f}{min(secs):>7.2f}{max(secs):>7.2f}")
    finally:
        try:
            browser.stop()
        except Exception as e:
            print("stop failed:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
