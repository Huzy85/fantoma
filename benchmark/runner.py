"""Benchmark runner — orchestrates parallel workers."""

import json
import logging
import os
import time
import urllib.request
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from benchmark.config import BenchmarkConfig
from benchmark.evaluator import evaluate_results
from benchmark.results import aggregate_results, generate_summary_md
from benchmark.tasks import load_tasks
from benchmark.worker import TaskResult, run_single_task, serialize_result

log = logging.getLogger("benchmark")

FAILURE_PHRASES = ["i could not", "i couldn't", "i was unable", "unable to find",
                   "could not find", "i cannot find", "i can't find", "no results found"]
SWEEP_INTERVAL = 25       # notify every N completions
NOTIFY_UNTIL   = 100      # stop Telegram notifications after this many tasks
STOP_FILE      = Path("/tmp/fantoma_benchmark_stop")


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _load_telegram_config() -> tuple[str, str] | None:
    """Read bot token + chat_id from env vars or nanobot config."""
    # Env vars take priority (set when running inside Docker)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        return token, chat
    # Fallback: read from nanobot config (host only)
    try:
        cfg_path = Path.home() / ".nanobot/config.json"
        d = json.loads(cfg_path.read_text())
        tg = d["channels"]["telegram"]
        if not tg.get("enabled"):
            return None
        return tg["token"], tg["allowFrom"][0]
    except Exception:
        return None


def _send_telegram(token: str, chat_id: str, text: str):
    try:
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------

def _quick_check(result: TaskResult) -> bool:
    """Heuristic pass/fail — no LLM, instant."""
    if result.status != "completed":
        return False
    answer = result.answer or ""
    if len(answer) < 80:
        return False
    low = answer.lower()
    if any(low.startswith(p) for p in FAILURE_PHRASES):
        return False
    return True


def _sweep(results: list[TaskResult], total: int, tg: tuple | None):
    n = len(results)
    likely_pass = sum(1 for r in results if _quick_check(r))
    likely_fail = n - likely_pass
    errors   = sum(1 for r in results if r.status == "error")
    timeouts = sum(1 for r in results if r.status == "timeout")
    flag = "  *** INVESTIGATE ***" if n > 0 and likely_pass / n < 0.5 else ""

    line = (f"SWEEP {n}/{total} — "
            f"{likely_pass} likely pass, {likely_fail} likely fail "
            f"({errors} errors, {timeouts} timeouts){flag}")
    print(f"\n  {line}\n", flush=True)

    if tg and n <= NOTIFY_UNTIL:
        warn = " Stopping recommended." if flag else ""
        msg = (f"*Fantoma benchmark — {n}/{total} done*\n"
               f"{likely_pass} likely pass, {likely_fail} likely fail\n"
               f"{errors} errors, {timeouts} timeouts{warn}\n\n"
               f"_Reply \"stop benchmark\" to halt._")
        _send_telegram(*tg, msg)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _init_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_benchmark(
    config: BenchmarkConfig,
    task_filter: str | None = None,
    site_filter: str | None = None,
    limit: int | None = None,
) -> Path:
    """Run the full WebVoyager benchmark. Returns path to results directory."""
    _init_logging()

    # Clear any leftover stop file from a previous run
    STOP_FILE.unlink(missing_ok=True)

    run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = Path(config.results_dir) / run_id
    tasks_output_dir = run_dir / "tasks"
    tasks_output_dir.mkdir(parents=True)

    tasks = load_tasks(site_filter=site_filter, task_filter=task_filter)
    if limit:
        tasks = tasks[:limit]
    log.info("Loaded %d tasks", len(tasks))

    if not tasks:
        log.error("No tasks to run")
        return run_dir

    (run_dir / "config.json").write_text(json.dumps(asdict(config), indent=2))

    tg = _load_telegram_config()
    if tg:
        _send_telegram(*tg,
            f"*Fantoma benchmark started*\n{len(tasks)} tasks, {config.workers} workers.\n"
            f"Updates every {SWEEP_INTERVAL} tasks (first {NOTIFY_UNTIL}).\n"
            f"_Reply \"stop benchmark\" to halt._")

    config_dict = asdict(config)
    completed  = 0
    total      = len(tasks)
    start_time = time.monotonic()
    all_results: list[TaskResult] = []
    last_sweep = 0
    stopped    = False

    log.info("Starting benchmark: %d tasks, %d workers", total, config.workers)

    def _make_crashed_result(task: dict, err: str) -> TaskResult:
        return TaskResult(
            task_id=task["id"],
            web_name=task["web_name"],
            instruction=task["ques"],
            start_url=task["web"],
            status="error",
            answer=None,
            final_url=None,
            steps_taken=0,
            steps_detail=[],
            duration_s=0,
            tokens_used=0,
            error=err,
        )

    # Tasks run in batches of `config.workers` so a single worker dying via
    # the watchdog's os._exit(1) cannot cascade-break the whole queue.
    # With workers=1 this means one task per pool (perfect isolation).
    # With workers>1 a pool break still loses at most `workers-1` siblings.
    batch_size = max(1, config.workers)
    batches = [tasks[i:i + batch_size] for i in range(0, len(tasks), batch_size)]

    for batch in batches:
        if STOP_FILE.exists():
            log.info("Stop file detected — cancelling remaining tasks")
            stopped = True
            break

        processed_in_batch: set[str] = set()
        try:
            with ProcessPoolExecutor(max_workers=config.workers) as executor:
                futures = {
                    executor.submit(run_single_task, task, config_dict): task
                    for task in batch
                }

                for future in as_completed(futures):
                    if STOP_FILE.exists():
                        log.info("Stop file detected — cancelling remaining tasks")
                        for f in futures:
                            f.cancel()
                        stopped = True
                        break

                    task = futures[future]
                    try:
                        task_result, screenshot = future.result(timeout=config.timeout)
                        serialize_result(task_result, screenshot, tasks_output_dir)
                        completed += 1
                        elapsed = time.monotonic() - start_time
                        rate = completed / elapsed * 3600 if elapsed > 0 else 0
                        log.info(
                            "Progress: %d/%d (%.0f tasks/hr) — %s: %s",
                            completed, total, rate, task_result.task_id, task_result.status,
                        )
                    except BrokenExecutor:
                        raise
                    except Exception as e:
                        log.error("Worker crashed on task %s: %s", task["id"], e)
                        task_result = _make_crashed_result(task, str(e))
                        serialize_result(task_result, None, tasks_output_dir)
                        completed += 1

                    processed_in_batch.add(task["id"])
                    all_results.append(task_result)
                    if completed - last_sweep >= SWEEP_INTERVAL:
                        _sweep(all_results, total, tg)
                        last_sweep = completed

        except BrokenExecutor as pool_err:
            # Some task's watchdog called os._exit(1) — pool is dead.
            # Record every un-processed task in this batch as crashed and
            # continue with the next batch in a fresh pool.
            log.error("Pool broken during batch: %s", pool_err)
            for task in batch:
                if task["id"] in processed_in_batch:
                    continue
                task_result = _make_crashed_result(task, f"pool_broken: {pool_err}")
                serialize_result(task_result, None, tasks_output_dir)
                completed += 1
                all_results.append(task_result)
                log.info(
                    "Progress: %d/%d — %s: error (pool broken)",
                    completed, total, task["id"],
                )

        if stopped:
            break

    total_time = time.monotonic() - start_time

    if stopped:
        log.info("Benchmark stopped early: %d tasks in %.0f minutes", completed, total_time / 60)
        if tg:
            n = len(all_results)
            p = sum(1 for r in all_results if _quick_check(r))
            _send_telegram(*tg, f"*Benchmark stopped.* {n} tasks done, {p} likely pass, {n-p} likely fail.")
        return run_dir

    log.info("Agent runs complete: %d tasks in %.0f minutes", completed, total_time / 60)

    log.info("Starting GPT-4V evaluation...")
    evaluate_results(run_dir, config)

    log.info("Aggregating results...")
    summary = aggregate_results(run_dir)

    summary["run_id"] = run_id
    summary["agent"] = "fantoma-0.7.0"
    summary["llm"] = config.llm_url
    summary["eval_model"] = config.eval_model
    summary["total_time_min"] = round(total_time / 60, 1)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    md = generate_summary_md(summary)
    (run_dir / "summary.md").write_text(md)

    log.info("Results saved to %s", run_dir)
    log.info("Score: %d/%d (%.1f%%)", summary["success"], summary["evaluated"], summary["score_pct"])

    if tg:
        _send_telegram(*tg,
            f"*Benchmark complete!*\n"
            f"{summary['success']}/{summary['evaluated']} passed "
            f"({summary['score_pct']:.1f}%) in {summary['total_time_min']:.0f} min.")

    return run_dir
