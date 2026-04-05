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
