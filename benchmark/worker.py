"""Single-task worker for benchmark runs."""

import json
import logging
import os
import signal
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path


class TaskTimeout(Exception):
    pass

log = logging.getLogger("benchmark.worker")


@dataclass
class TaskResult:
    """Result from running a single benchmark task."""

    task_id: str
    web_name: str
    instruction: str
    start_url: str
    status: str  # completed | timeout | error
    answer: str | None
    final_url: str | None
    steps_taken: int
    steps_detail: list
    duration_s: float
    tokens_used: int
    error: str | None


def serialize_result(result: TaskResult, screenshot: bytes | None, output_dir: str | Path) -> Path:
    """Save result.json and screenshot to output_dir/<task_id>/."""
    task_dir = Path(output_dir) / result.task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "result.json").write_text(json.dumps(asdict(result), indent=2))

    if screenshot:
        (task_dir / "screenshot_final.png").write_bytes(screenshot)

    return task_dir


def run_single_task(task: dict, config_dict: dict) -> tuple[TaskResult, bytes | None]:
    """Run one WebVoyager task. Called in a worker process.

    Args:
        task: Dict with keys: id, web_name, ques, web.
        config_dict: Serialised BenchmarkConfig (dataclass asdict).

    Returns:
        (TaskResult, screenshot_bytes or None)
    """
    from dataclasses import fields
    from benchmark.config import BenchmarkConfig

    config = BenchmarkConfig(**{f.name: config_dict[f.name] for f in fields(BenchmarkConfig)})

    task_id = task["id"]
    log.info("Starting task %s (%s)", task_id, task["web_name"])

    # Import here — each worker process needs its own imports
    from fantoma import Agent

    # Agent.__init__ takes **kwargs which are forwarded to Fantoma
    agent_kwargs = dict(
        llm_url=config.llm_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        max_steps=config.max_steps,
        headless=config.headless,
        browser=config.browser,
        timeout=config.timeout,
    )
    if config.captcha_api and config.captcha_key:
        agent_kwargs["captcha_api"] = config.captcha_api
        agent_kwargs["captcha_key"] = config.captcha_key

    # Wire escalation chain if configured (pipe-separated lists)
    if config.escalation_urls:
        urls = [u.strip() for u in config.escalation_urls.split("|") if u.strip()]
        keys = [k.strip() for k in config.escalation_keys.split("|")] if config.escalation_keys else []
        models = [m.strip() for m in config.escalation_models.split("|")] if config.escalation_models else []
        # Pad keys and models to match urls length
        while len(keys) < len(urls):
            keys.append("")
        while len(models) < len(urls):
            models.append("auto")
        agent_kwargs["escalation"] = urls
        agent_kwargs["escalation_keys"] = keys
        agent_kwargs["escalation_models"] = models
        log.info("Escalation chain wired: %d tiers, top=%s", len(urls), models[-1])

    agent = Agent(**agent_kwargs)

    # Intercept fantoma.stop() to capture screenshot before browser closes.
    # Agent.run() calls fantoma.stop() in its finally block, so we can't
    # capture after run() returns.
    screenshot = None
    original_stop = agent.fantoma.stop

    def _stop_with_screenshot():
        nonlocal screenshot
        if screenshot is None:
            try:
                screenshot = agent.fantoma.screenshot()
            except Exception:
                log.warning("Failed to capture screenshot for %s", task_id)
        original_stop()

    agent.fantoma.stop = _stop_with_screenshot

    start_time = time.monotonic()

    # Timeout via os._exit in a watchdog thread.
    # SIGALRM doesn't work in ProcessPoolExecutor child processes, and
    # threading can't interrupt blocked C calls (Playwright). Hard kill is
    # the only reliable option for hung browser calls.
    #
    # Use an Event so the watchdog can be cancelled when the task finishes.
    # Without cancellation, a completed task's watchdog stays alive in the
    # worker process and fires later during the next task, killing the pool.
    stop_event = threading.Event()

    def _watchdog():
        if not stop_event.wait(config.timeout):
            log.error("Task %s hard-killed after %ds timeout", task_id, config.timeout)
            os._exit(1)

    timer = threading.Thread(target=_watchdog, daemon=True)
    timer.start()

    try:
        result = agent.run(task["ques"], start_url=task["web"])
        duration = time.monotonic() - start_time

        task_result = TaskResult(
            task_id=task_id,
            web_name=task["web_name"],
            instruction=task["ques"],
            start_url=task["web"],
            status="completed",
            answer=str(result.data) if result.data else result.error or "",
            final_url=result.steps_detail[-1].get("url", "") if result.steps_detail else "",
            steps_taken=result.steps_taken,
            steps_detail=result.steps_detail or [],
            duration_s=round(duration, 1),
            tokens_used=result.tokens_used,
            error=result.error if result.error else None,
        )

    except TaskTimeout:
        duration = time.monotonic() - start_time
        log.error("Task %s timed out after %.0fs", task_id, duration)
        task_result = TaskResult(
            task_id=task_id,
            web_name=task["web_name"],
            instruction=task["ques"],
            start_url=task["web"],
            status="timeout",
            answer=None,
            final_url=None,
            steps_taken=0,
            steps_detail=[],
            duration_s=round(duration, 1),
            tokens_used=0,
            error=f"Task timed out after {config.timeout}s",
        )

    except Exception as e:
        duration = time.monotonic() - start_time
        log.error("Task %s failed: %s", task_id, e)
        task_result = TaskResult(
            task_id=task_id,
            web_name=task["web_name"],
            instruction=task["ques"],
            start_url=task["web"],
            status="error",
            answer=None,
            final_url=None,
            steps_taken=0,
            steps_detail=[],
            duration_s=round(duration, 1),
            tokens_used=0,
            error=traceback.format_exc(),
        )

    finally:
        # Cancel the watchdog first so it can't fire during cleanup or on a
        # subsequent task running in the same pool worker.
        stop_event.set()
        # Agent.run() already calls fantoma.stop() (which we wrapped above).
        # Only call it explicitly if the agent crashed before reaching its own stop.
        try:
            original_stop()
        except Exception:
            pass

    log.info("Finished task %s: %s (%.1fs, %d steps)", task_id, task_result.status, duration, task_result.steps_taken)
    return task_result, screenshot
