#!/usr/bin/env python3
"""Flow test — multi-step journeys verified by end state, not by prose.

Two mistakes make agent testing lie to you, and both are easy to make:

1. **Tasks with a single-step shortcut.** Ask "search Wikipedia for Ada
   Lovelace and say what she's known for" and a good agent just navigates
   to the article URL — one step. It passes, and you have learned nothing
   about multi-step behaviour. A real flow must be impossible to shortcut:
   you cannot reach a checkout page without adding to a cart first.

2. **Grading the agent's prose.** If you string-match the answer, an agent
   that read the wrong page but wrote a plausible sentence passes. Grade
   the *browser's* end state instead — what URL are we on, what does the
   page now contain. The agent cannot talk its way past that.

So each flow here has checkpoints, and each checkpoint is verified against
the live page after the agent finishes. Partial credit is the point: a
6-step flow that reaches step 4 tells you where it breaks, which
pass/fail never does.

Every target is a site published expressly for automation practice, so
there is no rate-limiting, no terms problem, and no live third party
affected.

    FANTOMA_MCP_BACKENDS=http://host:7860,... python3 tools/live_flow_test.py
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A flow is: a task for the agent, then checkpoints proving what happened.
# Each checkpoint is (label, kind, needle) where kind is "url" or "text".
FLOWS = [
    {
        "name": "saucedemo-login",
        "url": "https://www.saucedemo.com",
        "task": ("Log in with username 'standard_user' and password "
                 "'secret_sauce'. Tell me what page you land on."),
        "checkpoints": [
            ("reached inventory", "url", "inventory"),
            ("products visible", "text", "backpack"),
        ],
    },
    {
        "name": "saucedemo-add-to-cart",
        "url": "https://www.saucedemo.com",
        "task": ("Log in with username 'standard_user' and password "
                 "'secret_sauce', then add the 'Sauce Labs Backpack' to the "
                 "cart. Tell me how many items are in the cart."),
        "checkpoints": [
            ("reached inventory", "url", "inventory"),
            # Only present once something is actually in the cart.
            ("cart badge shows an item", "text", "remove"),
        ],
    },
    {
        "name": "saucedemo-checkout",
        "url": "https://www.saucedemo.com",
        "task": ("Log in with username 'standard_user' and password "
                 "'secret_sauce', add the 'Sauce Labs Backpack' to the cart, "
                 "open the cart, and click Checkout. Tell me what the "
                 "checkout page asks for."),
        "checkpoints": [
            ("reached checkout", "url", "checkout"),
            ("asks for personal details", "text", "first name"),
        ],
    },
    {
        "name": "herokuapp-login",
        "url": "https://the-internet.herokuapp.com/login",
        "task": ("Log in with username 'tomsmith' and password "
                 "'SuperSecretPassword!'. Tell me what the page says."),
        "checkpoints": [
            ("reached secure area", "url", "secure"),
            ("success banner shown", "text", "logged into a secure area"),
        ],
    },
    {
        "name": "herokuapp-dropdown",
        "url": "https://the-internet.herokuapp.com/dropdown",
        "task": "Select 'Option 2' from the dropdown on this page.",
        "checkpoints": [
            ("option 2 actually selected", "state", 'option "Option 2" [selected]'),
        ],
    },
    {
        "name": "herokuapp-checkboxes",
        "url": "https://the-internet.herokuapp.com/checkboxes",
        "task": "Tick the first checkbox on this page if it is not already ticked.",
        # The page ships with the SECOND box already ticked, so "a checked box
        # exists" proves nothing. Both ticked is the only end state that means
        # the agent ticked the first one.
        "checkpoints": [
            ("both checkboxes now ticked", "state_count", ("[checked]", 2)),
        ],
    },
    {
        "name": "herokuapp-inputs",
        "url": "https://the-internet.herokuapp.com/inputs",
        "task": "Type the number 42 into the input box on this page.",
        "checkpoints": [
            ("input actually contains 42", "state", '[value="42"]'),
        ],
    },
    {
        "name": "expandtesting-signup",
        "url": "https://practice.expandtesting.com/register",
        "task": ("Register a new account with username 'fantoma_probe_9x' and "
                 "password 'Str0ngPass!2026', confirming the password if asked. "
                 "Tell me what the site says afterwards."),
        "checkpoints": [
            ("left the register page", "url", "login"),
        ],
    },
]


def _verify(body, state, checkpoints):
    """Score checkpoints against the live page, not against the agent's prose.

    A checkpoint has to be FALSE before the agent acts, or it measures nothing.
    Three of these used to assert "the URL still contains /checkboxes" while
    the flow STARTED on that URL, so an agent that did nothing at all scored
    them as passes — a model that errored on every call and never issued a
    single action scored 4 of 6 here. Anything that asserts a state change now
    reads the live ARIA tree via keep_session, where the control itself reports
    [checked], [selected] or its value.

    Kinds:
      url         final URL contains needle
      text        title/answer contains needle (loose; never load-bearing)
      state       live ARIA tree contains needle
      state_count live ARIA tree contains needle at least N times
    """
    url = (body.get("final_url") or "").lower()
    title = (body.get("final_title") or "").lower()
    answer = (body.get("data") or "").lower()
    tree = (state or {}).get("aria_tree") or ""
    hits = []
    for label, kind, needle in checkpoints:
        if kind == "state":
            ok = needle.lower() in tree.lower()
        elif kind == "state_count":
            token, want = needle
            ok = tree.lower().count(token.lower()) >= want
        elif kind == "url":
            ok = needle.lower() in url
        else:
            ok = needle.lower() in f"{title} {answer}"
        hits.append((label, ok))

    needs_live = any(k in ("state", "state_count") for _, k, _ in checkpoints)
    if needs_live and not tree:
        return hits, "no live page state — cannot verify, treat as failure"
    return hits, ("" if url else "run reported no final_url")


def run_flow(flow, timeout):
    from fantoma import mcp_server as m

    t0 = time.time()
    pool = m._pool_instance()
    try:
        with pool.acquire() as backend:
            # Clear any prior session, run the flow, then inspect the live
            # page before tearing it down — that inspection is the grade.
            try:
                m._post(backend, "/stop", {}, timeout=60.0, retry_transport=True)
            except Exception:
                pass
            # keep_session hands the live browser back so the end state can be
            # read off the page itself. Without it /run tears the browser down
            # and all that survives is the agent's own account of what it did.
            body = m._post(backend, "/run",
                           {"task": flow["task"], "url": flow["url"],
                            "timeout": timeout, "keep_session": True},
                           timeout=timeout + 30)
            try:
                state = m._get(backend, "/state", timeout=60.0)
            except Exception:
                state = {}
            hits, verr = _verify(body, state, flow["checkpoints"])
            try:
                m._post(backend, "/stop", {}, timeout=60.0, retry_transport=True)
            except Exception:
                pass
    except Exception as e:
        return {"name": flow["name"], "agent_ok": False, "passed": 0,
                "total": len(flow["checkpoints"]), "secs": round(time.time()-t0, 1),
                "steps": 0, "detail": f"{type(e).__name__}: {str(e)[:70]}"}

    secs = round(time.time() - t0, 1)
    passed = sum(1 for _, ok in hits if ok)
    failed_at = next((label for label, ok in hits if not ok), None)
    return {
        "name": flow["name"],
        "agent_ok": bool(body.get("success")),
        "passed": passed,
        "total": len(flow["checkpoints"]),
        "secs": secs,
        "steps": body.get("steps_taken") or 0,
        "detail": verr or (f"stopped at: {failed_at}" if failed_at else "all checkpoints met"),
        "answer": (body.get("data") or "")[:70].replace("\n", " "),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--timeout", type=int, default=240)
    p.add_argument("--flow", help="Run one flow by name")
    p.add_argument("--json")
    args = p.parse_args()

    logging.disable(logging.CRITICAL)
    flows = [f for f in FLOWS if not args.flow or f["name"] == args.flow]
    print(f"Flow test — {len(flows)} multi-step journeys, verified by end state")
    print(f"Backends: {os.environ.get('FANTOMA_MCP_BACKENDS', '(default)')}\n")

    results = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for r in concurrent.futures.as_completed(
                [pool.submit(run_flow, f, args.timeout) for f in flows]):
            res = r.result()
            results.append(res)
            mark = "PASS" if res["passed"] == res["total"] else (
                "PART" if res["passed"] else "FAIL")
            print(f"  {mark}  {res['name']:<24} {res['passed']}/{res['total']} "
                  f"checkpoints  {res['secs']:>6.1f}s {res['steps']:>2} steps  "
                  f"{res['detail'][:44]}", flush=True)

    elapsed = round(time.time() - t0, 1)
    full = sum(1 for r in results if r["passed"] == r["total"])
    cp_hit = sum(r["passed"] for r in results)
    cp_tot = sum(r["total"] for r in results)
    print("\n" + "=" * 72)
    print(f"Flows fully completed : {full}/{len(results)}")
    print(f"Checkpoints reached   : {cp_hit}/{cp_tot}")
    print(f"Elapsed               : {elapsed}s")
    print("\nNote: checkpoints are read off the live browser after the run, so an"
          "\nagent cannot pass by describing a page it never reached.")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"elapsed": elapsed, "results": results}, fh, indent=2)
    return 0 if full == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
