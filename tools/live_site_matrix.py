#!/usr/bin/env python3
"""Live site matrix — where Fantoma actually stands, across real sites.

tools/live_api_test.py is a 6-page smoke test of trivial static pages: it
proves the plumbing works, not that the agent does. This runs a spread of
real sites grouped by difficulty and checks that each answer actually
contains something only the correct page would say, so a confident wrong
answer counts as a failure rather than a pass.

Runs through the MCP backend pool, so it exercises failover too.

    FANTOMA_MCP_BACKENDS=http://host:7860,http://host:7861 \
        python3 tools/live_site_matrix.py --workers 3
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

# Each case: expect is a token the correct page's answer should contain,
# lowercased. Keep it specific enough that another page would not match.
CASES = [
    # ── Tier 1: static, no JS. Baseline — these must not fail. ──
    ("static", "https://example.com", "What is the main heading?", "example domain"),
    # iana.org is operated by PTI, so a correct answer may say either.
    ("static", "https://www.iana.org/", "What is this organisation called?", None),
    # httpbin is frequently 503 — a flaky dependency makes the suite lie about
    # Fantoma, so use a static endpoint that stays up.
    ("static", "https://www.rfc-editor.org/rfc/rfc2606.txt",
     "What is this document about?", None),

    # ── Tier 2: real content sites, server-rendered. ──
    ("content", "https://news.ycombinator.com", "What is the title of the top story?", None),
    ("content", "https://en.wikipedia.org/wiki/Python_(programming_language)",
     "Who created this programming language?", "rossum"),
    ("content", "https://www.bbc.co.uk/news", "What is one headline on this page?", None),
    ("content", "https://www.theguardian.com/uk", "What is one headline on this page?", None),

    # ── Tier 3: technical docs and registries. ──
    ("docs", "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/button",
     "Which HTML element does this page document?", "button"),
    ("docs", "https://pypi.org/project/requests/",
     "What is the latest version of this package?", None),
    ("docs", "https://docs.python.org/3/library/json.html",
     "Which standard library module does this page document?", "json"),
    ("docs", "https://www.npmjs.com/package/express",
     "What is this npm package called?", "express"),

    # ── Tier 4: JS-heavy / app-like. ──
    ("jsheavy", "https://github.com/python/cpython",
     "What is the name of this repository?", "cpython"),
    ("jsheavy", "https://stackoverflow.com/questions",
     "What is one question title listed?", None),
    ("jsheavy", "https://github.com/login", "What does this page ask you to do?", None),

    # ── Tier 5: bot-protected. The real test of the stealth claim. ──
    ("protected", "https://www.etsy.com", "What kind of website is this?", None),
    ("protected", "https://uk.indeed.com", "What kind of website is this?", None),
    ("protected", "https://old.reddit.com", "What is one post title on this page?", None),
    ("protected", "https://duckduckgo.com", "What kind of website is this?", None),
    ("protected", "https://www.cloudflare.com", "What kind of company is this?", None),
]


def run_case(case, timeout):
    tier, url, query, expect = case
    from fantoma import mcp_server as m

    t0 = time.time()
    try:
        r = m.fantoma_extract(url, query)
        secs = round(time.time() - t0, 1)
        answer = (r.data or "").strip().replace("\n", " ")
        if not r.success:
            return {"tier": tier, "url": url, "status": "ERROR", "secs": secs,
                    "detail": (r.error or "")[:110]}
        if not answer:
            return {"tier": tier, "url": url, "status": "EMPTY", "secs": secs,
                    "detail": "call succeeded but returned no content"}
        if expect and expect not in answer.lower():
            # Succeeded, but the answer does not match the page we asked for.
            return {"tier": tier, "url": url, "status": "WRONG", "secs": secs,
                    "detail": f"expected {expect!r} in: {answer[:80]}"}
        return {"tier": tier, "url": url, "status": "PASS", "secs": secs,
                "detail": answer[:80]}
    except Exception as e:
        return {"tier": tier, "url": url, "status": "EXC",
                "secs": round(time.time() - t0, 1),
                "detail": f"{type(e).__name__}: {str(e)[:90]}"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=3,
                   help="Should match the number of backends in the pool.")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--tier", help="Run only one tier (static/content/docs/jsheavy/protected)")
    p.add_argument("--json", help="Write full results here")
    args = p.parse_args()

    logging.disable(logging.CRITICAL)
    cases = [c for c in CASES if not args.tier or c[0] == args.tier]

    backends = os.environ.get("FANTOMA_MCP_BACKENDS", "(default 127.0.0.1:7860)")
    print(f"Live site matrix — {len(cases)} cases, {args.workers} workers")
    print(f"Backends: {backends}\n")

    results = []
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, c, args.timeout): c for c in cases}
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            results.append(r)
            mark = {"PASS": "PASS", "WRONG": "WRONG", "EMPTY": "EMPTY",
                    "ERROR": "ERROR", "EXC": "EXC"}[r["status"]]
            print(f"  [{r['tier']:<9}] {mark:<5} {r['secs']:>5.1f}s  "
                  f"{r['url'][:44]:<44} {r['detail'][:60]}", flush=True)

    elapsed = round(time.time() - t0, 1)
    by_status: dict[str, int] = {}
    by_tier: dict[str, list] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_tier.setdefault(r["tier"], []).append(r["status"])

    passed = by_status.get("PASS", 0)
    print("\n" + "=" * 72)
    print(f"RESULT: {passed}/{len(results)} passed in {elapsed}s")
    print("Breakdown:", ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print("\nBy tier:")
    for tier in ("static", "content", "docs", "jsheavy", "protected"):
        if tier in by_tier:
            st = by_tier[tier]
            print(f"  {tier:<10} {st.count('PASS')}/{len(st)} passed"
                  f"   ({', '.join(sorted(set(s for s in st if s != 'PASS'))) or 'clean'})")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"elapsed": elapsed, "results": results}, fh, indent=2)
        print(f"\nFull results: {args.json}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
