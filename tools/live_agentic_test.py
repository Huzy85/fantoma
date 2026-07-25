#!/usr/bin/env python3
"""Agentic test — multi-step tasks on real sites, through the full agent.

live_site_matrix.py measures the read path: open one page, extract a fact.
That is a single navigator step and it says nothing about whether the agent
can actually *do* things. This measures the act path — search, click through,
fill a form, then report — which is what invokes the planner and where a
browser agent normally falls over.

Uses /run (the full Agent: planner + navigator + cache), not /extract.

    FANTOMA_MCP_BACKENDS=http://host:7860,http://host:7861 \
        python3 tools/live_agentic_test.py --workers 3
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (difficulty, url, task, expect_token_or_None)
# Read-only tasks against public pages. Nothing here creates an account or
# submits data to a third party.
TASKS = [
    # ── 2 steps: interact once, then read. ──
    ("2step", "https://duckduckgo.com",
     "Search for 'python programming language' and tell me the title of the first result.",
     None),
    ("2step", "https://en.wikipedia.org",
     "Search for 'Ada Lovelace' and tell me what she is known for.",
     "lovelace"),
    ("2step", "https://pypi.org",
     "Search for the package 'flask' and tell me its latest version number.",
     None),

    # ── Navigate + read a specific value off a page. ──
    ("navigate", "https://github.com/python/cpython",
     "Find how many stars this repository has and report the number.",
     None),
    ("navigate", "https://news.ycombinator.com",
     "Open the comments page for the top story and tell me the title of that story.",
     None),

    # ── Forms: fill and submit, then read the response. ──
    ("form", "https://the-internet.herokuapp.com/login",
     "Log in with username 'tomsmith' and password 'SuperSecretPassword!', "
     "then tell me what the page says after logging in.",
     "secure"),
    ("form", "https://www.w3schools.com/html/html_forms.asp",
     "Find the example form on this page, type 'Fantoma' into the first name "
     "field, and tell me what the form looks like.",
     None),

    # ── Multi-hop: two pages, carrying information between them. ──
    ("multihop", "https://news.ycombinator.com",
     "Find the top story, then tell me both its title and how many points it has.",
     None),
]


def run_task(case, timeout):
    tier, url, task, expect = case
    from fantoma import mcp_server as m

    t0 = time.time()
    try:
        r = m.fantoma_run(task, url=url, timeout=timeout)
        secs = round(time.time() - t0, 1)
        answer = (r.data or "").strip().replace("\n", " ")
        if not r.success:
            return {"tier": tier, "url": url, "status": "FAIL", "secs": secs,
                    "steps": r.steps_taken, "detail": (r.error or "")[:100]}
        if not answer:
            return {"tier": tier, "url": url, "status": "EMPTY", "secs": secs,
                    "steps": r.steps_taken, "detail": "succeeded with no content"}
        if expect and expect not in answer.lower():
            return {"tier": tier, "url": url, "status": "WRONG", "secs": secs,
                    "steps": r.steps_taken,
                    "detail": f"want {expect!r}: {answer[:70]}"}
        return {"tier": tier, "url": url, "status": "PASS", "secs": secs,
                "steps": r.steps_taken, "detail": answer[:70]}
    except Exception as e:
        return {"tier": tier, "url": url, "status": "EXC",
                "secs": round(time.time() - t0, 1), "steps": 0,
                "detail": f"{type(e).__name__}: {str(e)[:80]}"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--tier")
    p.add_argument("--json")
    args = p.parse_args()

    logging.disable(logging.CRITICAL)
    cases = [c for c in TASKS if not args.tier or c[0] == args.tier]
    print(f"Agentic test — {len(cases)} multi-step tasks, {args.workers} workers")
    print(f"Backends: {os.environ.get('FANTOMA_MCP_BACKENDS', '(default)')}\n")

    results = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_task, c, args.timeout) for c in cases]
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            results.append(r)
            print(f"  [{r['tier']:<8}] {r['status']:<5} {r['secs']:>6.1f}s "
                  f"{r['steps']:>2} steps  {r['url'][:34]:<34} {r['detail'][:52]}",
                  flush=True)

    elapsed = round(time.time() - t0, 1)
    counts: dict[str, int] = {}
    by_tier: dict[str, list] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        by_tier.setdefault(r["tier"], []).append(r["status"])

    passed = counts.get("PASS", 0)
    done = [r for r in results if r["status"] == "PASS"]
    print("\n" + "=" * 72)
    print(f"RESULT: {passed}/{len(results)} passed in {elapsed}s")
    print("Breakdown:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if done:
        print(f"Passing tasks: avg {sum(r['secs'] for r in done)/len(done):.1f}s, "
              f"avg {sum(r['steps'] for r in done)/len(done):.1f} steps")
    print("\nBy difficulty:")
    for tier in ("2step", "navigate", "form", "multihop"):
        if tier in by_tier:
            st = by_tier[tier]
            print(f"  {tier:<9} {st.count('PASS')}/{len(st)}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"elapsed": elapsed, "results": results}, fh, indent=2)

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
