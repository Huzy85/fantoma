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

    skipped_ids = set()
    if skipped_path:
        skip_file = Path(skipped_path)
        if skip_file.exists():
            skipped_ids = set(json.loads(skip_file.read_text()).keys())

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
