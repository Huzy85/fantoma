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
