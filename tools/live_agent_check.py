#!/usr/bin/env python3
"""Can a small model drive Fantoma on real sites? Graded on the live page.

Each task runs in its own process (a browser driver crash then costs one
task, not the run). The agent's own report is ignored for grading: after the
run the browser is kept open and the page itself is checked (is the box
ticked, is the item in the cart, did we reach the secure area). Read tasks
are graded on the answer, since the answer is the product there.

    python tools/live_agent_check.py --llm http://localhost:11434/v1 --model qwen2.5:3b
    python tools/live_agent_check.py ... --only herokuapp-dropdown --runs 3

The action cache is off, so every run is the model's own work. Single runs
of a small model are noisy; use --runs 3 before drawing conclusions.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# name, start url, task, check kind, check. Kinds:
#   url   final URL contains the text
#   js    JavaScript run on the final page returns true
#   answer the agent's answer contains the text (read tasks only)
TASKS = [
    ("herokuapp-checkboxes", "https://the-internet.herokuapp.com/checkboxes",
     "Tick the first checkbox on this page if it is not already ticked.",
     "js", "() => [...document.querySelectorAll('input[type=checkbox]')].every(c => c.checked)"),
    ("herokuapp-dropdown", "https://the-internet.herokuapp.com/dropdown",
     "Select 'Option 2' from the dropdown on this page.",
     "js", "() => document.querySelector('#dropdown').value === '2'"),
    ("herokuapp-inputs", "https://the-internet.herokuapp.com/inputs",
     "Type the number 42 into the input box on this page.",
     "js", "() => document.querySelector('input[type=number]').value === '42'"),
    ("herokuapp-login", "https://the-internet.herokuapp.com/login",
     "Log in with username 'tomsmith' and password 'SuperSecretPassword!'.",
     "url", "/secure"),
    ("saucedemo-login", "https://www.saucedemo.com",
     "Log in with username 'standard_user' and password 'secret_sauce'.",
     "url", "inventory"),
    ("saucedemo-add-to-cart", "https://www.saucedemo.com",
     "Log in with username 'standard_user' and password 'secret_sauce', then add "
     "the 'Sauce Labs Fleece Jacket' to the cart.",
     "js", "() => !!document.querySelector('#remove-sauce-labs-fleece-jacket')"),
    ("books-read-price", "https://books.toscrape.com/",
     "What is the price of the book 'A Light in the Attic'?",
     "answer", "51.77"),
    ("books-navigate-category", "https://books.toscrape.com/",
     "Open the 'Travel' category and tell me the title of the first book listed.",
     "answer", "himalayas"),
    ("wikipedia-read", "https://en.wikipedia.org/wiki/Python_(programming_language)",
     "Who designed the Python programming language?",
     "answer", "rossum"),
]


def run_one(name: str, llm: str, model: str, browser: str, max_steps: int, timeout: int) -> dict:
    from fantoma import Agent

    _, url, task, kind, check = next(t for t in TASKS if t[0] == name)
    agent = Agent(llm_url=llm, model=model, browser=browser, headless=True,
                  max_steps=max_steps, action_cache=False, validate=False)
    started = time.time()
    result = agent.run(task, start_url=url, deadline_s=timeout, keep_open=True)
    ok, detail = False, ""
    try:
        page = agent.fantoma._engine.get_page() if agent.fantoma._engine else None
        final_url = (page.url if page else result.final_url) or ""
        if kind == "url":
            ok = check in final_url
            detail = f"ended at {final_url}"
        elif kind == "js":
            ok = bool(page and page.evaluate(check))
            detail = f"page check {'true' if ok else 'false'} at {final_url}"
        else:
            answer = str(result.data or "")
            ok = check.lower() in answer.lower()
            detail = f"answer: {answer[:120]!r}"
    except Exception as e:
        detail = f"check failed: {e}"
    finally:
        try:
            agent.fantoma.stop()
        except Exception:
            pass
    return {
        "task": name, "ok": ok, "agent_said_success": bool(result.success),
        "steps": result.steps_taken, "secs": round(time.time() - started, 1),
        "detail": detail, "error": (result.error or "")[:200],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--llm", required=True, help="OpenAI-compatible base URL")
    p.add_argument("--model", default="auto")
    p.add_argument("--browser", default="camoufox", choices=["camoufox", "chromium"])
    p.add_argument("--only", help="comma-separated task names")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--max-steps", type=int, default=15)
    p.add_argument("--timeout", type=int, default=420, help="seconds per task")
    p.add_argument("--json", help="write results here")
    p.add_argument("--child", help=argparse.SUPPRESS)
    args = p.parse_args()

    if args.child:
        print(json.dumps(run_one(args.child, args.llm, args.model, args.browser,
                                 args.max_steps, args.timeout)))
        return 0

    names = [t[0] for t in TASKS]
    if args.only:
        names = [n for n in names if n in args.only.split(",")]
    results = []
    for name in names:
        for run in range(args.runs):
            cmd = [sys.executable, __file__, "--child", name, "--llm", args.llm,
                   "--model", args.model, "--browser", args.browser,
                   "--max-steps", str(args.max_steps), "--timeout", str(args.timeout)]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True,
                                     timeout=args.timeout + 120)
                line = [l for l in out.stdout.splitlines() if l.startswith("{")]
                res = json.loads(line[-1]) if line else {
                    "task": name, "ok": False, "detail": "no result",
                    "error": out.stderr[-300:]}
            except subprocess.TimeoutExpired:
                res = {"task": name, "ok": False, "detail": "timed out"}
            res["run"] = run + 1
            results.append(res)
            flag = "PASS" if res.get("ok") else "FAIL"
            said = "" if res.get("ok") == res.get("agent_said_success") else \
                "  (agent claimed success)" if res.get("agent_said_success") else ""
            print(f"{flag}  {name:<26} run {run + 1}  {res.get('steps', '?'):>2} steps "
                  f"{res.get('secs', '?'):>6}s  {res.get('detail', '')}{said}"
                  + (f"  error: {res['error']}" if res.get("error") and not res.get("ok") else ""),
                  flush=True)

    passed = sum(1 for r in results if r.get("ok"))
    print(f"\n{passed}/{len(results)} passed ({args.model}, {args.browser})")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"model": args.model, "browser": args.browser, "results": results}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
