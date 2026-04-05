# WebVoyager Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a native Python benchmark harness that runs the 643-task WebVoyager suite against Fantoma with 4 parallel workers, evaluates via GPT-4V, and publishes results on the GitHub README.

**Architecture:** A `benchmark/` package in the Fantoma repo. Runner distributes tasks across 4 worker processes (ProcessPoolExecutor). Each worker creates its own Fantoma + Agent instance, runs tasks sequentially, captures screenshots. After all workers finish, evaluator sends final screenshots + answers to GPT-4V. Results aggregator produces JSON + Markdown.

**Tech Stack:** Python 3.10+, concurrent.futures, httpx (for OpenAI eval calls), Fantoma Agent API, GPT-4o for evaluation.

**Spec:** `docs/superpowers/specs/2026-04-05-webvoyager-benchmark-design.md`

---

### Task 1: Scaffold benchmark package and config

**Files:**
- Create: `benchmark/__init__.py`
- Create: `benchmark/__main__.py`
- Create: `benchmark/config.py`
- Modify: `.gitignore` (add `benchmark/results/`)

- [ ] **Step 1: Create benchmark directory**

```bash
mkdir -p benchmark/data benchmark/results
```

- [ ] **Step 2: Write `benchmark/__init__.py`**

```python
"""Fantoma WebVoyager benchmark harness."""
```

- [ ] **Step 3: Write `benchmark/config.py`**

```python
"""Benchmark configuration."""

import os
from dataclasses import dataclass, field


@dataclass
class BenchmarkConfig:
    """Configuration for a benchmark run."""

    llm_url: str = "http://localhost:8080/v1"
    llm_api_key: str = ""
    llm_model: str = "auto"
    eval_model: str = "gpt-4o"
    openai_api_key: str = ""
    workers: int = 4
    max_steps: int = 30
    timeout: int = 180
    browser: str = "camoufox"
    headless: bool = True
    capture_step_screenshots: bool = False
    results_dir: str = "benchmark/results"

    @classmethod
    def from_env(cls, **overrides) -> "BenchmarkConfig":
        """Build config from environment variables, with overrides from CLI."""
        env_map = {
            "llm_url": "BENCHMARK_LLM_URL",
            "llm_api_key": "BENCHMARK_LLM_API_KEY",
            "llm_model": "BENCHMARK_LLM_MODEL",
            "eval_model": "BENCHMARK_EVAL_MODEL",
            "openai_api_key": "OPENAI_API_KEY",
            "workers": "BENCHMARK_WORKERS",
            "max_steps": "BENCHMARK_MAX_STEPS",
            "timeout": "BENCHMARK_TIMEOUT",
            "browser": "BENCHMARK_BROWSER",
        }
        kwargs = {}
        for attr, env_var in env_map.items():
            val = os.environ.get(env_var)
            if val is not None:
                # Cast int fields
                if attr in ("workers", "max_steps", "timeout"):
                    val = int(val)
                kwargs[attr] = val
        kwargs.update(overrides)
        return cls(**kwargs)
```

- [ ] **Step 4: Write `benchmark/__main__.py`**

```python
"""CLI entry point: python -m benchmark"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Run WebVoyager benchmark against Fantoma",
    )
    parser.add_argument("--llm", default=None, help="LLM endpoint URL")
    parser.add_argument("--llm-api-key", default=None, help="LLM API key")
    parser.add_argument("--llm-model", default=None, help="LLM model name")
    parser.add_argument("--eval-model", default=None, help="Evaluator model (default: gpt-4o)")
    parser.add_argument("--workers", type=int, default=None, help="Parallel workers (default: 4)")
    parser.add_argument("--max-steps", type=int, default=None, help="Max agent steps per task")
    parser.add_argument("--timeout", type=int, default=None, help="Seconds per task")
    parser.add_argument("--browser", default=None, help="Browser engine")
    parser.add_argument("--task", default=None, help="Run single task by ID")
    parser.add_argument("--site", default=None, help="Run tasks for one website only")
    parser.add_argument("--eval-only", default=None, metavar="DIR", help="Re-evaluate existing results")
    parser.add_argument("--update-readme", default=None, metavar="DIR", help="Update README from results")
    parser.add_argument("--step-screenshots", action="store_true", help="Capture per-step screenshots")
    args = parser.parse_args()

    from benchmark.config import BenchmarkConfig

    overrides = {}
    if args.llm:
        overrides["llm_url"] = args.llm
    if args.llm_api_key:
        overrides["llm_api_key"] = args.llm_api_key
    if args.llm_model:
        overrides["llm_model"] = args.llm_model
    if args.eval_model:
        overrides["eval_model"] = args.eval_model
    if args.workers is not None:
        overrides["workers"] = args.workers
    if args.max_steps is not None:
        overrides["max_steps"] = args.max_steps
    if args.timeout is not None:
        overrides["timeout"] = args.timeout
    if args.browser:
        overrides["browser"] = args.browser
    if args.step_screenshots:
        overrides["capture_step_screenshots"] = True

    config = BenchmarkConfig.from_env(**overrides)

    if args.eval_only:
        from benchmark.evaluator import evaluate_results
        evaluate_results(args.eval_only, config)
        sys.exit(0)

    if args.update_readme:
        from benchmark.results import update_readme
        update_readme(args.update_readme)
        sys.exit(0)

    from benchmark.runner import run_benchmark
    run_benchmark(config, task_filter=args.task, site_filter=args.site)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Add `benchmark/results/` to `.gitignore`**

Append to `.gitignore`:
```
benchmark/results/
```

- [ ] **Step 6: Commit**

```bash
git add benchmark/__init__.py benchmark/__main__.py benchmark/config.py .gitignore
git commit -m "feat(benchmark): scaffold package with config and CLI"
```

---

### Task 2: Task loader and data files

**Files:**
- Create: `benchmark/tasks.py`
- Create: `benchmark/data/webvoyager.jsonl` (copy from Magnitude's patched version)
- Create: `benchmark/data/skipped.json`

- [ ] **Step 1: Write failing test for task loader**

Create `benchmark/tests/__init__.py` (empty) and `benchmark/tests/test_tasks.py`:

```python
"""Tests for task loader."""

import json
import tempfile
from pathlib import Path

from benchmark.tasks import load_tasks


def test_load_tasks_returns_all():
    """Load all tasks from a small test JSONL."""
    data = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for i in range(5):
        json.dump({"web_name": "Test", "id": f"Test--{i}", "ques": f"Task {i}", "web": "https://test.com"}, data)
        data.write("\n")
    data.close()

    tasks = load_tasks(data.name, skipped_path=None)
    assert len(tasks) == 5
    assert tasks[0]["id"] == "Test--0"
    Path(data.name).unlink()


def test_load_tasks_filters_skipped():
    """Skipped task IDs are excluded."""
    data = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    skip = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    for i in range(5):
        json.dump({"web_name": "Test", "id": f"Test--{i}", "ques": f"Task {i}", "web": "https://test.com"}, data)
        data.write("\n")
    data.close()
    json.dump({"Test--1": "stale date", "Test--3": "impossible"}, skip)
    skip.close()

    tasks = load_tasks(data.name, skipped_path=skip.name)
    assert len(tasks) == 3
    ids = [t["id"] for t in tasks]
    assert "Test--1" not in ids
    assert "Test--3" not in ids
    Path(data.name).unlink()
    Path(skip.name).unlink()


def test_load_tasks_filter_by_site():
    """Filter tasks by web_name."""
    data = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for name in ["Alpha", "Alpha", "Beta", "Beta", "Beta"]:
        json.dump({"web_name": name, "id": f"{name}--0", "ques": "Q", "web": "https://test.com"}, data)
        data.write("\n")
    data.close()

    tasks = load_tasks(data.name, skipped_path=None, site_filter="Alpha")
    assert len(tasks) == 2
    assert all(t["web_name"] == "Alpha" for t in tasks)
    Path(data.name).unlink()


def test_load_tasks_filter_by_task_id():
    """Filter tasks by specific task ID."""
    data = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for i in range(5):
        json.dump({"web_name": "Test", "id": f"Test--{i}", "ques": f"Task {i}", "web": "https://test.com"}, data)
        data.write("\n")
    data.close()

    tasks = load_tasks(data.name, skipped_path=None, task_filter="Test--2")
    assert len(tasks) == 1
    assert tasks[0]["id"] == "Test--2"
    Path(data.name).unlink()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_tasks.py -v
```

Expected: ImportError — `benchmark.tasks` does not exist yet.

- [ ] **Step 3: Write `benchmark/tasks.py`**

```python
"""WebVoyager task loader."""

import json
from pathlib import Path


DATA_DIR = Path(__file__).parent / "data"
DEFAULT_TASKS = DATA_DIR / "webvoyager.jsonl"
DEFAULT_SKIPPED = DATA_DIR / "skipped.json"


def load_tasks(
    tasks_path: str | Path = None,
    skipped_path: str | Path | None = "default",
    site_filter: str | None = None,
    task_filter: str | None = None,
) -> list[dict]:
    """Load WebVoyager tasks, filtering skipped/site/task as requested."""
    tasks_path = Path(tasks_path) if tasks_path else DEFAULT_TASKS
    if skipped_path == "default":
        skipped_path = DEFAULT_SKIPPED

    # Load skipped IDs
    skipped_ids = set()
    if skipped_path:
        skip_file = Path(skipped_path)
        if skip_file.exists():
            skipped_ids = set(json.loads(skip_file.read_text()).keys())

    # Load and filter tasks
    tasks = []
    with open(tasks_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            task = json.loads(line)
            if task["id"] in skipped_ids:
                continue
            if site_filter and task["web_name"] != site_filter:
                continue
            if task_filter and task["id"] != task_filter:
                continue
            tasks.append(task)

    return tasks


def list_sites(tasks_path: str | Path = None) -> list[str]:
    """List all unique website names in the task file."""
    tasks = load_tasks(tasks_path=tasks_path, skipped_path=None)
    return sorted(set(t["web_name"] for t in tasks))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_tasks.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Copy Magnitude's patched tasks to `benchmark/data/webvoyager.jsonl`**

```bash
cp /tmp/webvoyager_patched.jsonl /home/workspace/workbench/fantoma/benchmark/data/webvoyager.jsonl
```

If `/tmp/webvoyager_patched.jsonl` is gone, re-download:
```bash
curl -sL https://raw.githubusercontent.com/magnitudedev/webvoyager/main/data/patchedTasks.jsonl \
  -o /home/workspace/workbench/fantoma/benchmark/data/webvoyager.jsonl
```

- [ ] **Step 6: Create initial `benchmark/data/skipped.json`**

Start with an empty skip list. We'll populate it during initial test runs.

```json
{}
```

- [ ] **Step 7: Commit**

```bash
git add benchmark/tasks.py benchmark/tests/ benchmark/data/
git commit -m "feat(benchmark): task loader with WebVoyager data"
```

---

### Task 3: Worker — single task executor

**Files:**
- Create: `benchmark/worker.py`
- Create: `benchmark/tests/test_worker.py`

- [ ] **Step 1: Write failing test for worker**

The worker calls `Agent.run()` which needs a browser. We'll test the result-serialisation logic and task-output structure without launching a real browser. Create `benchmark/tests/test_worker.py`:

```python
"""Tests for worker output structure."""

import json
import tempfile
from pathlib import Path

from benchmark.worker import TaskResult, serialize_result


def test_task_result_fields():
    """TaskResult has all required fields."""
    r = TaskResult(
        task_id="Test--0",
        web_name="Test",
        instruction="Do something",
        start_url="https://test.com",
        status="completed",
        answer="The answer is 42",
        final_url="https://test.com/result",
        steps_taken=5,
        steps_detail=[{"action": "click", "element": 1}],
        duration_s=12.3,
        tokens_used=500,
        error=None,
    )
    assert r.task_id == "Test--0"
    assert r.status == "completed"


def test_serialize_result_creates_files():
    """serialize_result writes result.json and placeholder for screenshot."""
    r = TaskResult(
        task_id="Test--0",
        web_name="Test",
        instruction="Do something",
        start_url="https://test.com",
        status="completed",
        answer="The answer is 42",
        final_url="https://test.com/result",
        steps_taken=5,
        steps_detail=[],
        duration_s=12.3,
        tokens_used=500,
        error=None,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        serialize_result(r, screenshot=b"\x89PNG fake", output_dir=tmpdir)
        task_dir = Path(tmpdir) / "Test--0"
        assert (task_dir / "result.json").exists()
        assert (task_dir / "screenshot_final.png").exists()

        data = json.loads((task_dir / "result.json").read_text())
        assert data["task_id"] == "Test--0"
        assert data["answer"] == "The answer is 42"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_worker.py -v
```

Expected: ImportError — `benchmark.worker` does not exist.

- [ ] **Step 3: Write `benchmark/worker.py`**

```python
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
    from fantoma import Fantoma, Agent

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

        status = "completed" if result.success or result.data else "completed"
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_worker.py -v
```

Expected: Both tests PASS (they only test TaskResult and serialize_result, not the actual browser run).

- [ ] **Step 5: Commit**

```bash
git add benchmark/worker.py benchmark/tests/test_worker.py
git commit -m "feat(benchmark): worker with TaskResult and serialisation"
```

---

### Task 4: Evaluator — GPT-4V judge

**Files:**
- Create: `benchmark/evaluator.py`
- Create: `benchmark/tests/test_evaluator.py`

- [ ] **Step 1: Write failing test for evaluator**

Create `benchmark/tests/test_evaluator.py`:

```python
"""Tests for evaluator verdict parsing."""

from benchmark.evaluator import parse_verdict, build_eval_messages


def test_parse_verdict_success():
    assert parse_verdict("The task was completed. SUCCESS") == "SUCCESS"


def test_parse_verdict_not_success():
    assert parse_verdict("The agent failed to find the recipe. NOT SUCCESS") == "NOT SUCCESS"


def test_parse_verdict_not_success_takes_priority():
    """NOT SUCCESS should match even if SUCCESS appears as substring."""
    assert parse_verdict("NOT SUCCESS despite some progress") == "NOT SUCCESS"


def test_parse_verdict_indeterminate():
    assert parse_verdict("I cannot determine the outcome.") is None


def test_build_eval_messages_structure():
    """Eval messages have system + user with text and image."""
    msgs = build_eval_messages(
        instruction="Find a recipe",
        answer="Vegetarian lasagna",
        screenshot_b64="aW1hZ2VkYXRh",
    )
    assert len(msgs) == 2
    assert msgs[0]["role"] == "system"
    assert "evaluator" in msgs[0]["content"].lower()
    assert msgs[1]["role"] == "user"
    # User message content is a list (multimodal)
    content = msgs[1]["content"]
    assert isinstance(content, list)
    # Should have text, image, text
    types = [c["type"] for c in content]
    assert types == ["text", "image_url", "text"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_evaluator.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `benchmark/evaluator.py`**

```python
"""GPT-4V evaluator using the standard WebVoyager auto-eval prompt."""

import base64
import json
import logging
from pathlib import Path

import httpx

log = logging.getLogger("benchmark.evaluator")

EVAL_SYSTEM_PROMPT = """As an evaluator, you will be presented with three primary components to assist you in your role:

1. Web Task Instruction: This is a clear and specific directive provided in natural language, detailing the online activity to be carried out. These requirements may include conducting searches, verifying information, comparing prices, checking availability, or any other action relevant to the specified web service (such as Amazon, Apple, ArXiv, BBC News, Booking etc).

2. Result Screenshots: This is a visual representation of the screen showing the result or intermediate state of performing a web task. It serves as visual proof of the actions taken in response to the instruction.

3. Result Response: This is a textual response obtained after the execution of the web task. It serves as textual result in response to the instruction.

-- You DO NOT NEED to interact with web pages or perform actions such as booking flights or conducting searches on websites.
-- You SHOULD NOT make assumptions based on information not presented in the screenshot when comparing it to the instructions.
-- Your primary responsibility is to conduct a thorough assessment of the web task instruction against the outcome depicted in the screenshot and in the response, evaluating whether the actions taken align with the given instructions.
-- NOTE that the instruction may involve more than one task, for example, locating the garage and summarizing the review. Failing to complete either task, such as not providing a summary, should be considered unsuccessful.
-- NOTE that the screenshot is authentic, but the response provided by LLM is generated at the end of web browsing, and there may be discrepancies between the text and the screenshots.
-- Note the difference: 1) Result response may contradict the screenshot, then the content of the screenshot prevails, 2) The content in the Result response is not mentioned on the screenshot, choose to believe the content.

You should elaborate on how you arrived at your final evaluation and then provide a definitive verdict on whether the task has been successfully accomplished, either as 'SUCCESS' or 'NOT SUCCESS'."""


def build_eval_messages(instruction: str, answer: str, screenshot_b64: str) -> list[dict]:
    """Build the OpenAI chat messages for evaluation."""
    user_content = [
        {
            "type": "text",
            "text": f"TASK: {instruction}\nResult Response: {answer}\n1 screenshots at the end:",
        },
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
        },
        {
            "type": "text",
            "text": "Your verdict:\n",
        },
    ]
    return [
        {"role": "system", "content": EVAL_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_verdict(response_text: str) -> str | None:
    """Parse SUCCESS or NOT SUCCESS from evaluator response."""
    if "NOT SUCCESS" in response_text.upper():
        return "NOT SUCCESS"
    if "SUCCESS" in response_text.upper():
        return "SUCCESS"
    return None


def evaluate_single(
    instruction: str,
    answer: str,
    screenshot_path: Path,
    openai_api_key: str,
    model: str = "gpt-4o",
) -> dict:
    """Evaluate a single task result via GPT-4V.

    Returns dict with keys: verdict, eval_model, eval_response.
    """
    screenshot_b64 = base64.b64encode(screenshot_path.read_bytes()).decode()
    messages = build_eval_messages(instruction, answer or "", screenshot_b64)

    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0,
            "seed": 42,
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    eval_text = data["choices"][0]["message"]["content"]
    verdict = parse_verdict(eval_text)

    return {
        "verdict": verdict or "INDETERMINATE",
        "eval_model": model,
        "eval_response": eval_text,
    }


def evaluate_results(results_dir: str | Path, config) -> None:
    """Evaluate all task results in a run directory.

    Reads result.json + screenshot_final.png from each task subdir.
    Writes result.eval.json next to each result.json.
    Skips tasks already evaluated or missing screenshots.
    """
    results_dir = Path(results_dir)
    tasks_dir = results_dir / "tasks"
    if not tasks_dir.exists():
        log.error("No tasks directory found at %s", tasks_dir)
        return

    task_dirs = sorted(d for d in tasks_dir.iterdir() if d.is_dir())
    evaluated = 0
    skipped = 0

    for task_dir in task_dirs:
        eval_file = task_dir / "result.eval.json"
        if eval_file.exists():
            skipped += 1
            continue

        result_file = task_dir / "result.json"
        screenshot_file = task_dir / "screenshot_final.png"

        if not result_file.exists():
            continue

        result = json.loads(result_file.read_text())

        # Auto-fail tasks that errored or timed out
        if result.get("status") in ("error", "timeout"):
            eval_data = {
                "task_id": result["task_id"],
                "verdict": "NOT SUCCESS",
                "eval_model": "auto",
                "eval_response": f"Auto-failed: task status was {result['status']}",
            }
            eval_file.write_text(json.dumps(eval_data, indent=2))
            evaluated += 1
            continue

        if not screenshot_file.exists():
            log.warning("No screenshot for %s, auto-failing", task_dir.name)
            eval_data = {
                "task_id": result["task_id"],
                "verdict": "NOT SUCCESS",
                "eval_model": "auto",
                "eval_response": "Auto-failed: no screenshot captured",
            }
            eval_file.write_text(json.dumps(eval_data, indent=2))
            evaluated += 1
            continue

        try:
            eval_data = evaluate_single(
                instruction=result["instruction"],
                answer=result.get("answer", ""),
                screenshot_path=screenshot_file,
                openai_api_key=config.openai_api_key,
                model=config.eval_model,
            )
            eval_data["task_id"] = result["task_id"]
            eval_file.write_text(json.dumps(eval_data, indent=2))
            evaluated += 1
            log.info("Evaluated %s: %s", task_dir.name, eval_data["verdict"])
        except Exception as e:
            log.error("Evaluation failed for %s: %s", task_dir.name, e)

    log.info("Evaluation complete: %d evaluated, %d skipped (already done)", evaluated, skipped)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_evaluator.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmark/evaluator.py benchmark/tests/test_evaluator.py
git commit -m "feat(benchmark): GPT-4V evaluator with standard WebVoyager prompt"
```

---

### Task 5: Results aggregator and Markdown generator

**Files:**
- Create: `benchmark/results.py`
- Create: `benchmark/tests/test_results.py`

- [ ] **Step 1: Write failing test for results aggregation**

Create `benchmark/tests/test_results.py`:

```python
"""Tests for results aggregation."""

import json
import tempfile
from pathlib import Path

from benchmark.results import aggregate_results, generate_comparison_table


def _make_task_result(task_id, web_name, verdict, steps=5, duration=30.0):
    """Helper to create a task dir with result.json and result.eval.json."""
    return {
        "result": {
            "task_id": task_id,
            "web_name": web_name,
            "instruction": "Do something",
            "start_url": "https://test.com",
            "status": "completed",
            "answer": "Answer",
            "final_url": "https://test.com/done",
            "steps_taken": steps,
            "steps_detail": [],
            "duration_s": duration,
            "tokens_used": 100,
            "error": None,
        },
        "eval": {
            "task_id": task_id,
            "verdict": verdict,
            "eval_model": "gpt-4o",
            "eval_response": f"The result is {verdict}",
        },
    }


def test_aggregate_results():
    """Aggregate results from task directories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir) / "tasks"

        for i, verdict in enumerate(["SUCCESS", "SUCCESS", "NOT SUCCESS"]):
            data = _make_task_result(f"Site--{i}", "Site", verdict, steps=i + 3, duration=20.0 + i * 10)
            task_dir = tasks_dir / f"Site--{i}"
            task_dir.mkdir(parents=True)
            (task_dir / "result.json").write_text(json.dumps(data["result"]))
            (task_dir / "result.eval.json").write_text(json.dumps(data["eval"]))

        summary = aggregate_results(tmpdir)
        assert summary["evaluated"] == 3
        assert summary["success"] == 2
        assert summary["score_pct"] == round(2 / 3 * 100, 1)
        assert "Site" in summary["per_site"]
        assert summary["per_site"]["Site"]["success"] == 2


def test_generate_comparison_table():
    """Comparison table is valid Markdown."""
    summary = {
        "agent": "fantoma-0.7.0",
        "llm": "hercules",
        "evaluated": 588,
        "success": 470,
        "score_pct": 79.9,
        "avg_steps": 8.3,
        "avg_duration_s": 94,
    }
    table = generate_comparison_table(summary)
    assert "Fantoma" in table
    assert "79.9%" in table
    assert "browser-use" in table
    assert "|" in table
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_results.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write `benchmark/results.py`**

```python
"""Results aggregation and Markdown generation."""

import json
import logging
import re
from pathlib import Path

log = logging.getLogger("benchmark.results")

# Competitor scores for the comparison table (from Steel.dev leaderboard)
COMPETITORS = [
    {"agent": "Surfer 2", "llm": "--", "score": "97.1%", "steps": "--", "time": "--"},
    {"agent": "Magnitude", "llm": "Claude Sonnet", "score": "93.9%", "steps": "--", "time": "--"},
    {"agent": "browser-use", "llm": "GPT-4o", "score": "89.1%", "steps": "--", "time": "--"},
    {"agent": "Skyvern 2.0", "llm": "--", "score": "85.9%", "steps": "--", "time": "--"},
]


def aggregate_results(results_dir: str | Path) -> dict:
    """Aggregate all task results into a summary dict."""
    results_dir = Path(results_dir)
    tasks_dir = results_dir / "tasks"

    per_site: dict[str, dict] = {}
    total_steps = 0
    total_duration = 0.0
    evaluated = 0
    success_count = 0

    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue

        result_file = task_dir / "result.json"
        eval_file = task_dir / "result.eval.json"

        if not result_file.exists() or not eval_file.exists():
            continue

        result = json.loads(result_file.read_text())
        eval_data = json.loads(eval_file.read_text())

        web_name = result["web_name"]
        verdict = eval_data.get("verdict", "NOT SUCCESS")
        is_success = verdict == "SUCCESS"

        if web_name not in per_site:
            per_site[web_name] = {"total": 0, "success": 0, "steps": 0, "duration": 0.0}

        per_site[web_name]["total"] += 1
        per_site[web_name]["steps"] += result.get("steps_taken", 0)
        per_site[web_name]["duration"] += result.get("duration_s", 0)
        if is_success:
            per_site[web_name]["success"] += 1
            success_count += 1

        total_steps += result.get("steps_taken", 0)
        total_duration += result.get("duration_s", 0)
        evaluated += 1

    # Compute per-site scores
    for site, data in per_site.items():
        data["score_pct"] = round(data["success"] / data["total"] * 100, 1) if data["total"] > 0 else 0.0
        data["avg_steps"] = round(data["steps"] / data["total"], 1) if data["total"] > 0 else 0
        data["avg_duration_s"] = round(data["duration"] / data["total"], 1) if data["total"] > 0 else 0

    summary = {
        "evaluated": evaluated,
        "success": success_count,
        "score_pct": round(success_count / evaluated * 100, 1) if evaluated > 0 else 0.0,
        "avg_steps": round(total_steps / evaluated, 1) if evaluated > 0 else 0,
        "avg_duration_s": round(total_duration / evaluated, 1) if evaluated > 0 else 0,
        "per_site": per_site,
    }

    # Save summary.json
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    log.info("Summary: %d/%d (%.1f%%)", success_count, evaluated, summary["score_pct"])

    return summary


def generate_comparison_table(summary: dict) -> str:
    """Generate Markdown comparison table for the README."""
    lines = [
        "| Agent | LLM | Score | Avg Steps | Avg Time |",
        "|-------|-----|-------|-----------|----------|",
    ]

    # Fantoma row (bold)
    agent_name = summary.get("agent", "Fantoma")
    llm_name = summary.get("llm", "unknown")
    lines.append(
        f"| **{agent_name}** | **{llm_name}** "
        f"| **{summary['score_pct']}%** "
        f"| **{summary['avg_steps']}** "
        f"| **{summary['avg_duration_s']}s** |"
    )

    # Competitor rows
    for comp in COMPETITORS:
        lines.append(
            f"| {comp['agent']} | {comp['llm']} "
            f"| {comp['score']} | {comp['steps']} | {comp['time']} |"
        )

    return "\n".join(lines)


def generate_site_breakdown(summary: dict) -> str:
    """Generate per-site Markdown table for docs/benchmark.md."""
    lines = [
        "| Site | Tasks | Passed | Score | Avg Steps | Avg Time |",
        "|------|-------|--------|-------|-----------|----------|",
    ]
    for site in sorted(summary["per_site"].keys()):
        data = summary["per_site"][site]
        lines.append(
            f"| {site} | {data['total']} | {data['success']} "
            f"| {data['score_pct']}% | {data['avg_steps']} "
            f"| {data['avg_duration_s']}s |"
        )
    return "\n".join(lines)


def generate_summary_md(summary: dict) -> str:
    """Generate the full summary Markdown (for docs/benchmark.md)."""
    parts = [
        f"# WebVoyager Benchmark Results\n",
        f"**Agent:** {summary.get('agent', 'Fantoma')}",
        f"**LLM:** {summary.get('llm', 'unknown')}",
        f"**Evaluated:** {summary['evaluated']} tasks",
        f"**Score:** {summary['success']}/{summary['evaluated']} ({summary['score_pct']}%)\n",
        "## Comparison\n",
        generate_comparison_table(summary),
        "\n## Per-Site Breakdown\n",
        generate_site_breakdown(summary),
    ]
    return "\n".join(parts)


def update_readme(results_dir: str | Path) -> None:
    """Update the README.md benchmark section with latest results."""
    results_dir = Path(results_dir)
    summary_file = results_dir / "summary.json"
    if not summary_file.exists():
        log.error("No summary.json in %s", results_dir)
        return

    summary = json.loads(summary_file.read_text())
    table = generate_comparison_table(summary)

    readme_path = Path("README.md")
    if not readme_path.exists():
        log.error("README.md not found in current directory")
        return

    readme = readme_path.read_text()

    # Replace between benchmark markers
    marker_start = "<!-- BENCHMARK:START -->"
    marker_end = "<!-- BENCHMARK:END -->"

    section = f"""{marker_start}
## Benchmark -- WebVoyager

Tested on [WebVoyager](https://github.com/MinorJerry/WebVoyager) (643 tasks, 15 live websites). Evaluated by GPT-4V using the standard auto-eval prompt. {summary['evaluated']} tasks evaluated.

{table}

Per-site breakdown and methodology: [docs/benchmark.md](docs/benchmark.md)
{marker_end}"""

    if marker_start in readme:
        readme = re.sub(
            f"{re.escape(marker_start)}.*?{re.escape(marker_end)}",
            section,
            readme,
            flags=re.DOTALL,
        )
    else:
        # Insert before ## Test Results if it exists, otherwise append
        if "## Test Results" in readme:
            readme = readme.replace("## Test Results", f"{section}\n\n## Test Results")
        else:
            readme = readme.rstrip() + f"\n\n{section}\n"

    readme_path.write_text(readme)
    log.info("README.md updated with benchmark results")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_results.py -v
```

Expected: Both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmark/results.py benchmark/tests/test_results.py
git commit -m "feat(benchmark): results aggregator and Markdown generator"
```

---

### Task 6: Runner — orchestrator with parallel workers

**Files:**
- Create: `benchmark/runner.py`

- [ ] **Step 1: Write `benchmark/runner.py`**

```python
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
from benchmark.results import aggregate_results, generate_summary_md, update_readme
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
                # Write error result
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
```

- [ ] **Step 2: Verify the full CLI works end-to-end (dry run)**

```bash
cd /home/workspace/workbench/fantoma && python -m benchmark --help
```

Expected: Help text with all options displayed, no import errors.

- [ ] **Step 3: Commit**

```bash
git add benchmark/runner.py
git commit -m "feat(benchmark): parallel runner with 4 workers"
```

---

### Task 7: Integration test with a single task

**Files:**
- Create: `benchmark/tests/test_integration.py`

- [ ] **Step 1: Write integration test**

This test runs a single real task against the Docker Fantoma API to verify the full pipeline works. Marked with `pytest.mark.slow` so it's skipped in normal test runs.

Create `benchmark/tests/test_integration.py`:

```python
"""Integration test — runs one real benchmark task."""

import json
from pathlib import Path

import pytest

from benchmark.config import BenchmarkConfig
from benchmark.runner import run_benchmark


@pytest.mark.slow
def test_single_task_end_to_end(tmp_path):
    """Run one easy task and verify result structure."""
    config = BenchmarkConfig(
        llm_url="http://localhost:8080/v1",
        workers=1,
        max_steps=15,
        timeout=120,
        results_dir=str(tmp_path),
    )

    run_dir = run_benchmark(config, task_filter="GitHub--0")

    # Check result files exist
    tasks_dir = run_dir / "tasks"
    assert tasks_dir.exists()
    task_dirs = list(tasks_dir.iterdir())
    assert len(task_dirs) == 1

    task_dir = task_dirs[0]
    assert (task_dir / "result.json").exists()

    result = json.loads((task_dir / "result.json").read_text())
    assert result["task_id"] == "GitHub--0"
    assert result["status"] in ("completed", "timeout", "error")
    assert result["steps_taken"] >= 0

    # Check summary
    assert (run_dir / "summary.json").exists()
```

- [ ] **Step 2: Run integration test**

```bash
cd /home/workspace/workbench/fantoma && python -m pytest benchmark/tests/test_integration.py -v -m slow --timeout=300
```

Expected: One task runs, result files are created. This validates the full worker + serialisation pipeline.

- [ ] **Step 3: Fix any issues found during integration test**

Adjust worker.py, runner.py, or Agent interaction based on what fails. Common issues:
- Agent constructor kwargs mismatch (check if `fantoma` can be passed directly)
- Screenshot capture timing (agent may have already stopped the browser)
- Process serialisation of config (dataclass must be picklable)

- [ ] **Step 4: Commit**

```bash
git add benchmark/tests/test_integration.py
git commit -m "test(benchmark): integration test for single task"
```

---

### Task 8: Benchmark README and docs page

**Files:**
- Create: `benchmark/README.md`
- Create: `docs/benchmark.md` (placeholder, populated after first run)
- Modify: `README.md` (add benchmark markers)

- [ ] **Step 1: Write `benchmark/README.md`**

```markdown
# WebVoyager Benchmark

Run the [WebVoyager](https://github.com/MinorJerry/WebVoyager) benchmark (643 tasks, 15 websites) against Fantoma.

## Quick Start

```bash
# Local LLM (Hercules)
python -m benchmark --llm http://localhost:8080/v1

# Claude Sonnet
python -m benchmark --llm https://api.anthropic.com/v1 --llm-api-key $ANTHROPIC_API_KEY

# Single task (for debugging)
python -m benchmark --task "GitHub--0" --llm http://localhost:8080/v1

# Single website
python -m benchmark --site GitHub --llm http://localhost:8080/v1
```

## Requirements

- `OPENAI_API_KEY` env var (for GPT-4V evaluation)
- Fantoma installed (`pip install fantoma` or running from source)
- An LLM endpoint (local or cloud)

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--llm` | localhost:8080 | LLM endpoint URL |
| `--llm-api-key` | none | LLM API key (for cloud) |
| `--workers` | 4 | Parallel browser processes |
| `--max-steps` | 30 | Max agent steps per task |
| `--timeout` | 180 | Seconds per task |
| `--task` | none | Run single task by ID |
| `--site` | none | Run one website only |
| `--eval-only DIR` | none | Re-evaluate existing results |
| `--update-readme DIR` | none | Update README from results |
| `--step-screenshots` | off | Capture per-step screenshots |

## Re-evaluate or Update README

```bash
# Re-run GPT-4V evaluation on existing results
python -m benchmark --eval-only benchmark/results/2026-04-05_143000/

# Update README.md with latest scores
python -m benchmark --update-readme benchmark/results/2026-04-05_143000/
```

## Task Data

Tasks are in `data/webvoyager.jsonl` (Magnitude's patched version with updated dates). Skipped tasks are listed in `data/skipped.json`.
```

- [ ] **Step 2: Add benchmark markers to main `README.md`**

Insert before the `## Test Results` section in `README.md`:

```markdown
<!-- BENCHMARK:START -->
<!-- BENCHMARK:END -->
```

This is where `--update-readme` will inject results after the first run.

- [ ] **Step 3: Create placeholder `docs/benchmark.md`**

```markdown
# WebVoyager Benchmark Results

Results will be generated after the first benchmark run.

Run: `python -m benchmark --llm http://localhost:8080/v1`
```

- [ ] **Step 4: Commit**

```bash
git add benchmark/README.md docs/benchmark.md README.md
git commit -m "docs(benchmark): add benchmark README, docs page, and README markers"
```

---

### Task 9: First real run and results

This task is manual. Run after all code is committed and tests pass.

- [ ] **Step 1: Run benchmark with Hercules (local)**

```bash
cd /home/workspace/workbench/fantoma
python -m benchmark --llm http://localhost:8080/v1 --workers 4
```

Expected runtime: ~2.5 hours. Monitor with:
```bash
tail -f benchmark/results/*/benchmark.log
ls benchmark/results/*/tasks/ | wc -l  # count completed
```

- [ ] **Step 2: Review results**

```bash
cat benchmark/results/*/summary.json | python3 -m json.tool
```

Check: score percentage, per-site breakdown, any sites with 0% (might indicate broken tasks to add to skipped.json).

- [ ] **Step 3: Update skipped.json if needed**

Add any tasks that are genuinely impossible (stale dates passed, sites changed) to `data/skipped.json` and re-evaluate:

```bash
python -m benchmark --eval-only benchmark/results/<run-id>/
```

- [ ] **Step 4: Update README**

```bash
cd /home/workspace/workbench/fantoma
python -m benchmark --update-readme benchmark/results/<run-id>/
```

- [ ] **Step 5: Copy full results to `docs/benchmark.md`**

Copy `benchmark/results/<run-id>/summary.md` content into `docs/benchmark.md`, adding methodology notes.

- [ ] **Step 6: Commit and push**

```bash
git add README.md docs/benchmark.md benchmark/data/skipped.json
git commit -m "feat(benchmark): WebVoyager results — Hercules local XX.X%"
git push
```

- [ ] **Step 7: Run benchmark with Claude Sonnet**

```bash
python -m benchmark --llm https://api.anthropic.com/v1 --llm-api-key $ANTHROPIC_API_KEY --llm-model claude-sonnet-4-20250514 --workers 4
```

- [ ] **Step 8: Update README with both scores and push**

```bash
python -m benchmark --update-readme benchmark/results/<sonnet-run-id>/
git add README.md docs/benchmark.md
git commit -m "feat(benchmark): add Claude Sonnet WebVoyager score"
git push
```
