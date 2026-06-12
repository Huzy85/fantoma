#!/usr/bin/env python3
"""Single real-site task runner for Phase 0 live validation.

Runs ONE task through a local-llm-only Agent (no cloud escalation) and prints a
one-line JSON result. Invoke under `timeout` for a hard wall-clock guard, one
task at a time so only a single local-llm slot is used. No credentials, no email,
no signups — read/extract tasks only.
"""
import json
import os
import sys
import time

from fantoma import Agent


def main():
    task = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
    llm = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8081/v1")
    model = os.environ.get("PHASE0_MODEL", "Qwen3.6-35B-A3B")
    max_steps = int(os.environ.get("PHASE0_MAX_STEPS", "15"))

    t0 = time.time()
    try:
        agent = Agent(
            llm_url=llm,
            model=model,
            escalation=[llm],            # local-llm only — never escalate to cloud
            escalation_models=[model],
            max_steps=max_steps,
            headless=True,
            browser="camoufox",
        )
        res = agent.run(task, start_url=url)
        out = {
            "task": task, "url": url, "llm": llm, "model": model,
            "success": bool(res.success), "steps": res.steps_taken,
            "secs": round(time.time() - t0, 1),
            "error": res.error or "", "data": (res.data or "")[:500],
        }
        try:
            agent.fantoma.stop()  # clean up the browser so sequential runs don't EPIPE
        except Exception:
            pass
    except Exception as e:
        out = {
            "task": task, "url": url, "llm": llm, "model": model,
            "success": False, "steps": 0, "secs": round(time.time() - t0, 1),
            "error": f"EXC: {type(e).__name__}: {e}", "data": "",
        }
    print(json.dumps(out))


if __name__ == "__main__":
    main()
