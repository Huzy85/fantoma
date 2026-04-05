"""Benchmark runner — orchestrates parallel workers."""

import json
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from benchmark.config import BenchmarkConfig
from benchmark.evaluator import evaluate_results
from benchmark.results import aggregate_results, generate_summary_md
from benchmark.tasks import load_tasks
from benchmark.worker import TaskResult, run_single_task, serialize_result

log = logging.getLogger("benchmark")


def _init_logging():
    """Set up benchmark logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def run_benchmark(
    config: BenchmarkConfig,
    task_filter: str | None = None,
    site_filter: str | None = None,
) -> Path:
    """Run the full WebVoyager benchmark.

    Returns path to results directory.
    """
    _init_logging()

    # Create run directory
    run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = Path(config.results_dir) / run_id
    tasks_output_dir = run_dir / "tasks"
    tasks_output_dir.mkdir(parents=True)

    # Load tasks
    tasks = load_tasks(site_filter=site_filter, task_filter=task_filter)
    log.info("Loaded %d tasks", len(tasks))

    if not tasks:
        log.error("No tasks to run")
        return run_dir

    # Save run config
    (run_dir / "config.json").write_text(json.dumps(asdict(config), indent=2))

    # Run tasks in parallel
    config_dict = asdict(config)
    completed = 0
    total = len(tasks)
    start_time = time.monotonic()

    log.info("Starting benchmark: %d tasks, %d workers", total, config.workers)

    with ProcessPoolExecutor(max_workers=config.workers) as executor:
        futures = {
            executor.submit(run_single_task, task, config_dict): task
            for task in tasks
        }

        for future in as_completed(futures):
            task = futures[future]
            try:
                task_result, screenshot = future.result()
                serialize_result(task_result, screenshot, tasks_output_dir)
                completed += 1
                elapsed = time.monotonic() - start_time
                rate = completed / elapsed * 3600 if elapsed > 0 else 0
                log.info(
                    "Progress: %d/%d (%.0f tasks/hr) — %s: %s",
                    completed, total, rate, task_result.task_id, task_result.status,
                )
            except Exception as e:
                log.error("Worker crashed on task %s: %s", task["id"], e)
                error_result = TaskResult(
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
                    error=str(e),
                )
                serialize_result(error_result, None, tasks_output_dir)
                completed += 1

    total_time = time.monotonic() - start_time
    log.info("Agent runs complete: %d tasks in %.0f minutes", completed, total_time / 60)

    # Evaluate
    log.info("Starting GPT-4V evaluation...")
    evaluate_results(run_dir, config)

    # Aggregate
    log.info("Aggregating results...")
    summary = aggregate_results(run_dir)

    # Add metadata to summary
    summary["run_id"] = run_id
    summary["agent"] = "fantoma-0.7.0"
    summary["llm"] = config.llm_url
    summary["eval_model"] = config.eval_model
    summary["total_time_min"] = round(total_time / 60, 1)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    # Generate Markdown
    md = generate_summary_md(summary)
    (run_dir / "summary.md").write_text(md)

    log.info("Results saved to %s", run_dir)
    log.info("Score: %d/%d (%.1f%%)", summary["success"], summary["evaluated"], summary["score_pct"])

    return run_dir
