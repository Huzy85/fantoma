"""Mid-run benchmark sweep — lightweight assessment without GPT-4o cost.

Uses heuristics + optional Hermes LLM (local, port 8082) to estimate pass
rate while the benchmark is still running. Hermes is best-effort: if it's
busy or slow, the sweep falls back to heuristics only.

Usage:
  # One-shot sweep of a completed/in-progress run:
  python3 -m benchmark.sweep benchmark/results/2026-04-06_150519

  # Watch mode — auto-sweeps every 15 new completions:
  python3 -m benchmark.sweep benchmark/results/2026-04-06_150519 --watch

  # Custom interval, skip LLM:
  python3 -m benchmark.sweep <run_dir> --watch --interval 20 --no-llm
"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx


# Phrases that strongly suggest the agent failed to complete the task
FAILURE_PHRASES = [
    "i could not find",
    "i couldn't find",
    "i was unable",
    "i'm unable",
    "unable to find",
    "unable to locate",
    "unable to access",
    "unable to retrieve",
    "could not be found",
    "could not find",
    "i cannot find",
    "i can't find",
    "no results found",
    "page not found",
    "i failed to",
]

VERDICTS = {
    "STOP":        "🔴 STOP        — too many errors, something is broken",
    "INVESTIGATE": "🟠 INVESTIGATE — heuristic pass rate too low, check agent logs",
    "CAUTION":     "🟡 CAUTION     — LLM estimate below target, may need tuning",
    "CONTINUE":    "🟢 CONTINUE    — results look healthy",
}


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_tasks(run_dir: Path) -> list[dict]:
    """Read all result.json files from run_dir/tasks/."""
    tasks_dir = run_dir / "tasks"
    results = []
    if tasks_dir.exists():
        for result_file in sorted(tasks_dir.glob("*/result.json")):
            try:
                results.append(json.loads(result_file.read_text()))
            except Exception:
                continue
    return results


def load_config(run_dir: Path) -> dict:
    cfg_file = run_dir / "config.json"
    if cfg_file.exists():
        try:
            return json.loads(cfg_file.read_text())
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Heuristic scoring (free, instant)
# ---------------------------------------------------------------------------

def heuristic_check(result: dict, max_steps: int) -> tuple[bool, str]:
    """Return (pass, reason). Reason is non-empty only on fail."""
    status = result.get("status", "")
    answer = result.get("answer") or ""
    steps = result.get("steps_taken", 0)

    if status == "error":
        return False, f"error: {(result.get('error') or '')[:60]}"
    if status == "timeout":
        return False, f"timeout after {steps} steps"
    if not answer:
        return False, "empty answer"
    if len(answer) < 80:
        return False, f"answer too short ({len(answer)} chars)"

    low = answer.lower()
    for phrase in FAILURE_PHRASES:
        if low.startswith(phrase):
            return False, f"answer starts with failure phrase"

    if max_steps > 0 and steps >= int(max_steps * 0.88):
        return False, f"near step limit ({steps}/{max_steps})"

    return True, ""


# ---------------------------------------------------------------------------
# Hermes LLM scoring (local, best-effort)
# ---------------------------------------------------------------------------

HERMES_URL = "http://localhost:8082/v1/chat/completions"
LLM_TIMEOUT = 20  # seconds per call — short so we don't block on busy Hermes


def llm_judge(client: httpx.Client, instruction: str, answer: str) -> bool | None:
    """Ask Hermes PASS/FAIL. Returns None if Hermes is unavailable/busy."""
    prompt = (
        "You are evaluating a web automation result. "
        "Reply with exactly one word: PASS or FAIL.\n\n"
        f"Task: {instruction}\n\n"
        f"Agent answer: {answer[:600]}"
    )
    try:
        resp = client.post(
            HERMES_URL,
            json={
                "model": "Hermes",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 8,
            },
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip().upper()
        if "PASS" in text:
            return True
        if "FAIL" in text:
            return False
        return None
    except Exception:
        return None  # Hermes busy or down — caller handles


def llm_sweep(results: list[dict]) -> tuple[int, int, int]:
    """
    Run Hermes over all completed results with a non-empty answer.

    Returns (pass_count, fail_count, skipped_count).
    skipped = Hermes was unavailable for that task.
    """
    candidates = [
        r for r in results
        if r.get("status") == "completed" and r.get("answer")
    ]
    if not candidates:
        return 0, 0, 0

    passes = fails = skipped = 0
    hermes_dead = False  # Stop trying after first N consecutive failures

    with httpx.Client() as client:
        consecutive_failures = 0
        for r in candidates:
            if hermes_dead:
                skipped += len(candidates) - passes - fails - skipped
                break
            verdict = llm_judge(client, r.get("instruction", ""), r.get("answer", ""))
            if verdict is None:
                skipped += 1
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    hermes_dead = True
            else:
                consecutive_failures = 0
                if verdict:
                    passes += 1
                else:
                    fails += 1

    return passes, fails, skipped


# ---------------------------------------------------------------------------
# Verdict + report
# ---------------------------------------------------------------------------

def compute_verdict(
    total: int,
    errors: int,
    timeouts: int,
    heuristic_pass: int,
    heuristic_total: int,
    llm_pass: int,
    llm_total: int,
) -> str:
    bad_rate = (errors + timeouts) / total if total > 0 else 0
    h_rate = heuristic_pass / heuristic_total if heuristic_total > 0 else 1.0
    l_rate = llm_pass / llm_total if llm_total > 0 else None

    if bad_rate > 0.40:
        return "STOP"
    if h_rate < 0.50:
        return "INVESTIGATE"
    if l_rate is not None and l_rate < 0.60:
        return "CAUTION"
    return "CONTINUE"


def print_report(
    run_dir: Path,
    total_tasks: int,
    results: list[dict],
    sweep_n: int,
    max_steps: int,
    no_llm: bool,
):
    completed = [r for r in results if r.get("status") == "completed"]
    errors    = [r for r in results if r.get("status") == "error"]
    timeouts  = [r for r in results if r.get("status") == "timeout"]

    h_pass = [r for r in results if heuristic_check(r, max_steps)[0]]
    h_fail = [r for r in results if not heuristic_check(r, max_steps)[0]]

    pct = len(results) / total_tasks * 100 if total_tasks else 0
    h_rate = len(h_pass) / len(results) * 100 if results else 0

    # Per-site breakdown
    sites: dict[str, dict] = {}
    for r in results:
        site = r.get("web_name", "unknown")
        if site not in sites:
            sites[site] = {"total": 0, "h_pass": 0}
        sites[site]["total"] += 1
        if heuristic_check(r, max_steps)[0]:
            sites[site]["h_pass"] += 1

    # LLM sweep
    llm_pass = llm_fail = llm_skip = 0
    if not no_llm:
        llm_pass, llm_fail, llm_skip = llm_sweep(results)
    llm_total = llm_pass + llm_fail

    verdict = compute_verdict(
        len(results), len(errors), len(timeouts),
        len(h_pass), len(results),
        llm_pass, llm_total,
    )

    now = datetime.now().strftime("%H:%M:%S")
    width = 66

    print()
    print("━" * width)
    print(f"  SWEEP #{sweep_n}  {now}  —  {len(results)}/{total_tasks} tasks ({pct:.1f}%)")
    print("━" * width)
    print(f"  Agent runs:   {len(completed)} completed  |  {len(errors)} errors  |  {len(timeouts)} timeouts")
    print(f"  Avg steps:    {sum(r.get('steps_taken',0) for r in results)/len(results):.1f}" if results else "  Avg steps:    —")
    print(f"  Avg duration: {sum(r.get('duration_s',0) for r in results)/len(results):.0f}s" if results else "")
    print()
    print(f"  Heuristic:    {len(h_pass)}/{len(results)} look good ({h_rate:.1f}%)")
    if not no_llm:
        if llm_total > 0:
            llm_rate = llm_pass / llm_total * 100
            note = f"  ({llm_skip} skipped — Hermes busy)" if llm_skip else ""
            print(f"  Hermes est:   {llm_pass}/{llm_total} PASS ({llm_rate:.1f}%){note}")
        else:
            print("  Hermes est:   unavailable — heuristics only")
    else:
        print("  Hermes est:   skipped (--no-llm)")

    # Per-site
    if len(sites) > 1:
        print()
        print("  By site:")
        for site, s in sorted(sites.items()):
            rate = s["h_pass"] / s["total"] * 100
            bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
            print(f"    {site:<20} {bar}  {s['h_pass']}/{s['total']} ({rate:.0f}%)")

    # Failures
    if h_fail:
        print()
        print(f"  Failures ({len(h_fail)}):")
        for r in h_fail[:10]:
            _, reason = heuristic_check(r, max_steps)
            print(f"    {r.get('task_id','?'):<30} {reason}")
        if len(h_fail) > 10:
            print(f"    ... and {len(h_fail)-10} more")

    print()
    print(f"  VERDICT:  {VERDICTS[verdict]}")
    print("━" * width)
    print()

    return verdict, llm_pass, llm_fail, llm_skip


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state(run_dir: Path) -> dict:
    state_file = run_dir / ".sweep_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass
    return {"sweep_count": 0, "last_seen": 0, "history": []}


def save_state(run_dir: Path, state: dict):
    (run_dir / ".sweep_state.json").write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_sweep(run_dir: Path, no_llm: bool, sweep_n: int, total_tasks: int):
    cfg = load_config(run_dir)
    max_steps = cfg.get("max_steps", 30)
    results = scan_tasks(run_dir)
    if not results:
        print(f"  No results yet in {run_dir}/tasks/")
        return 0, results

    verdict, _, _, _ = print_report(run_dir, total_tasks, results, sweep_n, max_steps, no_llm)
    return len(results), results


def main():
    parser = argparse.ArgumentParser(
        prog="python3 -m benchmark.sweep",
        description="Mid-run sweep: heuristic + Hermes assessment of in-progress benchmark",
    )
    parser.add_argument("run_dir", help="Path to benchmark run directory")
    parser.add_argument("--interval", type=int, default=15,
                        help="Trigger sweep every N new completions (default: 15)")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip Hermes LLM scoring (heuristics only)")
    parser.add_argument("--watch", action="store_true",
                        help="Keep watching and auto-sweep every --interval completions")
    parser.add_argument("--total", type=int, default=590,
                        help="Expected total tasks (for progress %%)")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.exists():
        print(f"Error: {run_dir} does not exist")
        return

    if not args.watch:
        # One-shot
        state = load_state(run_dir)
        sweep_n = state["sweep_count"] + 1
        run_sweep(run_dir, args.no_llm, sweep_n, args.total)
        return

    # Watch mode
    print(f"Watching {run_dir} — sweep every {args.interval} new completions. Ctrl+C to stop.")
    state = load_state(run_dir)

    try:
        while True:
            results = scan_tasks(run_dir)
            current = len(results)
            new = current - state["last_seen"]

            if new >= args.interval or (new > 0 and current >= args.total):
                state["sweep_count"] += 1
                count, _ = run_sweep(run_dir, args.no_llm, state["sweep_count"], args.total)
                state["last_seen"] = current
                state["history"].append({
                    "time": datetime.now().isoformat(),
                    "completed": current,
                })
                save_state(run_dir, state)

                if current >= args.total:
                    print("All tasks completed. Final sweep done.")
                    break
            else:
                remaining = args.interval - new
                print(f"\r  {current}/{args.total} done — next sweep in {remaining} more... ", end="", flush=True)

            time.sleep(20)

    except KeyboardInterrupt:
        print("\nSweep stopped.")


if __name__ == "__main__":
    main()
