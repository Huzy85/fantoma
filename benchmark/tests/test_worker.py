"""Tests for worker output structure."""

import json
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestWatchdogCancellation:
    """The per-task watchdog must stop firing once the task finishes so it
    cannot kill subsequent tasks in the same pool worker."""

    def _config_dict(self, timeout):
        from benchmark.config import BenchmarkConfig
        return asdict(BenchmarkConfig(timeout=timeout, max_steps=5, headless=True))

    def _fake_agent_module(self, run_side_effect):
        """Install a stub ``fantoma`` module so run_single_task imports it."""
        fake_agent = MagicMock()
        fake_result = MagicMock()
        fake_result.data = "ok"
        fake_result.steps_taken = 1
        fake_result.steps_detail = [{"url": "https://example.com"}]
        fake_result.error = None
        fake_result.tokens_used = 0
        fake_agent.run.side_effect = run_side_effect
        fake_agent.run.return_value = fake_result
        fake_agent.fantoma.stop = MagicMock()
        fake_agent.fantoma.screenshot = MagicMock(return_value=b"PNG")

        fake_module = MagicMock()
        fake_module.Agent.return_value = fake_agent
        return fake_module

    def test_completed_task_does_not_call_os_exit(self):
        """Task that finishes in well under timeout must not trigger os._exit."""
        from benchmark import worker

        def quick_run(*args, **kwargs):
            time.sleep(0.05)
            r = MagicMock()
            r.data = "ok"
            r.steps_taken = 1
            r.steps_detail = [{"url": "https://example.com"}]
            r.error = None
            r.tokens_used = 0
            return r

        fake_module = self._fake_agent_module(quick_run)
        task = {"id": "T--0", "web_name": "T", "ques": "q", "web": "https://example.com"}

        with patch.dict(sys.modules, {"fantoma": fake_module}):
            with patch.object(worker.os, "_exit") as mock_exit:
                worker.run_single_task(task, self._config_dict(timeout=2))
                # Wait past the timeout window; if the watchdog wasn't cancelled
                # it would fire here.
                time.sleep(2.2)
                assert mock_exit.call_count == 0

    def test_stop_event_cancels_watchdog_before_timeout(self):
        """The Event.wait + set pattern must return True when set, False otherwise."""
        ev = threading.Event()

        def cancelled():
            return not ev.wait(0.5)  # returns False because set below

        def not_cancelled():
            return not ev.wait(0.1)  # returns True because timeout elapses

        # Scenario 1: event set -> wait returns True -> "fired" is False
        ev.set()
        assert cancelled() is False

        # Scenario 2: event not set -> wait times out -> "fired" is True
        ev2 = threading.Event()
        assert (not ev2.wait(0.1)) is True
