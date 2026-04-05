"""Single-task worker for benchmark runs."""

import json
import logging
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path

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
    agent = Agent(
        llm_url=config.llm_url,
        api_key=config.llm_api_key,
        model=config.llm_model,
        max_steps=config.max_steps,
        headless=config.headless,
        browser=config.browser,
        timeout=config.timeout,
    )

    screenshot = None
    start_time = time.monotonic()

    try:
        result = agent.run(task["ques"], start_url=task["web"])
        duration = time.monotonic() - start_time

        # Capture final screenshot
        try:
            screenshot = agent.fantoma.screenshot()
        except Exception:
            log.warning("Failed to capture screenshot for %s", task_id)

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
        try:
            agent.fantoma.stop()
        except Exception:
            pass

    log.info("Finished task %s: %s (%.1fs, %d steps)", task_id, task_result.status, duration, task_result.steps_taken)
    return task_result, screenshot
