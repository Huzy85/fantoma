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
