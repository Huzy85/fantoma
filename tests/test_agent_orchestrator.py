# tests/test_agent_orchestrator.py
"""Test Agent.run() orchestration of planner -> navigator -> summarise."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from fantoma.agent import Agent, AgentResult
from fantoma.planner import Subtask
from fantoma.navigator import NavigatorResult


class TestAgentRunOrchestration:
    def _mock_agent(self, planner_subtasks, navigator_results, summary="Final answer"):
        """Build an Agent with mocked planner, navigator, and browser."""
        agent = Agent.__new__(Agent)
        agent._max_steps = 25

        # Mock planner
        agent._planner = MagicMock()
        agent._planner.decompose.return_value = planner_subtasks
        agent._planner.summarise.return_value = summary
        agent._planner.replan.return_value = None

        # Mock navigator
        agent._navigator = MagicMock()
        agent._navigator.execute.side_effect = navigator_results

        # Mock fantoma (browser tool)
        agent.fantoma = MagicMock()
        agent.fantoma.start.return_value = {"url": "https://example.com", "aria_tree": ""}
        agent.fantoma._engine = MagicMock()
        page_mock = MagicMock()
        page_mock.url = "https://example.com"
        page_mock.title.return_value = "Example"
        agent.fantoma._engine.get_page.return_value = page_mock
        agent.fantoma._dom = MagicMock()
        agent.fantoma._dom.extract_content.return_value = "Page content here"
        agent.fantoma._dom.extract.return_value = "Page: Example\nURL: https://example.com"

        # Mock LLM
        agent._llm = MagicMock()
        agent._sensitive_data = {}
        agent.escalation = MagicMock()
        agent.escalation.total_escalations = 0

        return agent

    def test_simple_task_two_subtasks(self):
        subtasks = [
            Subtask("Search for AI", "interact", "Results visible"),
            Subtask("Read first result", "read", "Title extracted"),
        ]
        nav_results = [
            NavigatorResult("done", "Search done", 3, [], "https://example.com/search"),
            NavigatorResult("done", "Title is: AI Paper", 2, [], "https://example.com/result"),
        ]
        agent = self._mock_agent(subtasks, nav_results, "AI Paper found")
        result = agent.run("Find AI papers", start_url="https://example.com")

        assert result.success is True
        assert result.data == "AI Paper found"
        assert result.steps_taken == 5

    def test_stagnation_triggers_replan(self):
        subtasks = [Subtask("Click menu", "interact", "Menu opens")]
        nav_results = [
            NavigatorResult("stagnant", "Stopped: action_cycle", 8, [], "https://example.com"),
        ]
        agent = self._mock_agent(subtasks, nav_results)
        agent._planner.replan.return_value = None  # No more replans
        result = agent.run("Find something", start_url="https://example.com")

        agent._planner.replan.assert_called_once()

    def test_step_budget_rolls_over(self):
        subtasks = [
            Subtask("Quick step", "interact", "Done"),
            Subtask("Big step", "find", "Done"),
        ]
        nav_results = [
            NavigatorResult("done", "Quick done", 2, [], "https://a.com"),
            NavigatorResult("done", "Big done", 10, [], "https://a.com/deep"),
        ]
        agent = self._mock_agent(subtasks, nav_results)
        agent._max_steps = 25
        result = agent.run("Do things", start_url="https://a.com")

        # Second navigator.execute should get more steps because first only used 2
        calls = agent._navigator.execute.call_args_list
        first_budget = calls[0].kwargs.get("max_steps") or calls[0][1].get("max_steps", 0)
        second_budget = calls[1].kwargs.get("max_steps") or calls[1][1].get("max_steps", 0)
        assert second_budget >= first_budget  # Rolled-over budget

    def test_browser_start_failure(self):
        agent = Agent.__new__(Agent)
        agent.fantoma = MagicMock()
        agent.fantoma.start.side_effect = Exception("Connection refused")
        agent._sensitive_data = {}

        result = agent.run("Do something", start_url="https://example.com")
        assert result.success is False
        assert "Connection refused" in result.error

    def test_no_subtasks_returns_empty_success(self):
        """Planner returns empty list — agent finishes immediately."""
        agent = self._mock_agent([], [])
        result = agent.run("Empty task", start_url="https://example.com")
        # No subtasks completed means loop exits immediately
        agent._planner.summarise.assert_called_once()
        assert result.steps_taken == 0

    def test_sensitive_data_passed_to_navigator(self):
        subtasks = [Subtask("Login", "interact", "Logged in")]
        nav_results = [NavigatorResult("done", "Logged in", 3, [], "https://example.com/dash")]
        agent = self._mock_agent(subtasks, nav_results)
        agent._sensitive_data = {"password": "s3cr3t"}

        agent.run("Login to site", start_url="https://example.com")

        call_kwargs = agent._navigator.execute.call_args.kwargs
        assert call_kwargs.get("sensitive_data") == {"password": "s3cr3t"}

    def test_replan_replaces_remaining_subtasks(self):
        """After stagnation, replan returns new subtasks that get executed."""
        original = [
            Subtask("Step 1", "interact", "Done"),
            Subtask("Step 2 (will fail)", "interact", "Done"),
        ]
        replacement = [Subtask("Alternative step 2", "find", "Done")]
        nav_results = [
            NavigatorResult("done", "Step 1 done", 3, [], "https://example.com"),
            NavigatorResult("stagnant", "Stopped", 5, [], "https://example.com"),
            NavigatorResult("done", "Alt step done", 2, [], "https://example.com/alt"),
        ]
        agent = self._mock_agent(original, nav_results)
        agent._planner.replan.return_value = replacement

        result = agent.run("Do steps", start_url="https://example.com")

        assert agent._planner.replan.call_count == 1
        assert agent._navigator.execute.call_count == 3
