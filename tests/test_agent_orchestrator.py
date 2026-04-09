# tests/test_agent_orchestrator.py
"""Test Agent.run() orchestration of planner -> navigator -> summarise."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from fantoma.agent import Agent, AgentResult, _dedup_urls
from fantoma.planner import Subtask
from fantoma.navigator import NavigatorResult


class TestDedupUrls:
    def test_collapses_consecutive_duplicates(self):
        assert _dedup_urls(["a", "a", "b", "b", "c", "a"]) == ["a", "b", "c", "a"]

    def test_drops_empty_strings(self):
        assert _dedup_urls(["", "a", "", "b"]) == ["a", "b"]

    def test_empty_input(self):
        assert _dedup_urls([]) == []


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
        # Default: no escalation available — tests that need it must opt in
        agent.escalation.can_escalate.return_value = False

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

    def test_stagnant_with_real_data_checkpoints_final_url(self):
        """Stagnant subtask that extracted real data must checkpoint final_url.

        Regression: Apple--0 reached the answer page in subtask 2 but ended
        stagnant. Without this behaviour, backtrack would jump to subtask 1's
        overview URL and throw away the answer page.
        """
        original = [
            Subtask("Land on category", "interact", "Category page"),
            Subtask("Find product details", "find", "Details visible"),
        ]
        replacement = [Subtask("Re-read details", "read", "Done")]
        nav_results = [
            # Subtask 1 completes cleanly -> checkpoint #1
            NavigatorResult("done", "Category page loaded", 2, [],
                            "https://example.com/category"),
            # Subtask 2 reaches the answer page but ends stagnant with real data
            NavigatorResult("stagnant", "Price is $999", 4, [],
                            "https://example.com/category/product-42",
                            failure_reason="scroll_limit", last_actions=[]),
            # Replacement runs cleanly
            NavigatorResult("done", "Confirmed $999", 1, [],
                            "https://example.com/category/product-42"),
        ]
        agent = self._mock_agent(original, nav_results)
        agent._planner.replan.return_value = replacement

        agent.run("Get price", start_url="https://example.com")

        # Backtrack after replan must land on the answer URL from subtask 2,
        # not the category URL from subtask 1.
        navigate_calls = [c for c in agent.fantoma.navigate.call_args_list]
        assert navigate_calls, "Agent should have called fantoma.navigate for backtrack"
        last_navigate_url = navigate_calls[-1].args[0] if navigate_calls[-1].args else navigate_calls[-1].kwargs.get("url")
        assert last_navigate_url == "https://example.com/category/product-42"

    def test_visited_urls_passed_to_replan(self):
        """Agent must collect the URL trail from steps_detail and pass it to replan."""
        original = [Subtask("Step 1", "interact", "Done")]
        nav_results = [
            NavigatorResult(
                "stagnant", "Stopped: loop", 3,
                steps_detail=[
                    {"step": 1, "action": "navigate({'url': 'x'})", "success": True,
                     "url": "https://example.com/a"},
                    {"step": 2, "action": "click({'element_id': 1})", "success": True,
                     "url": "https://example.com/b"},
                    {"step": 3, "action": "navigate({'url': 'y'})", "success": True,
                     "url": "https://example.com/a"},
                ],
                final_url="https://example.com/a",
                failure_reason="action_cycle", last_actions=[],
            ),
        ]
        agent = self._mock_agent(original, nav_results)
        agent._planner.replan.return_value = None  # force exhaustion after one replan

        agent.run("Find thing", start_url="https://example.com")

        replan_kwargs = agent._planner.replan.call_args.kwargs
        visited = replan_kwargs.get("visited_urls")
        assert visited == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/a",
        ]

    def test_stagnant_with_placeholder_data_does_not_checkpoint(self):
        """Stagnant subtask with only 'Stopped:' placeholder must NOT checkpoint.

        Placeholder data means nothing useful was gathered, so the final_url
        is probably a loop trap and should not become a backtrack target.
        """
        original = [
            Subtask("Land on category", "interact", "Category page"),
            Subtask("Find product details", "find", "Details visible"),
        ]
        replacement = [Subtask("Try search instead", "find", "Done")]
        nav_results = [
            NavigatorResult("done", "Category page loaded", 2, [],
                            "https://example.com/category"),
            NavigatorResult("stagnant", "Stopped: action_cycle", 4, [],
                            "https://example.com/loop-trap",
                            failure_reason="action_cycle", last_actions=[]),
            NavigatorResult("done", "Found via search", 2, [],
                            "https://example.com/search?q=product"),
        ]
        agent = self._mock_agent(original, nav_results)
        agent._planner.replan.return_value = replacement

        agent.run("Get price", start_url="https://example.com")

        # Backtrack must use subtask 1's URL, not the loop-trap URL.
        navigate_calls = list(agent.fantoma.navigate.call_args_list)
        assert navigate_calls
        last_navigate_url = navigate_calls[-1].args[0] if navigate_calls[-1].args else navigate_calls[-1].kwargs.get("url")
        assert last_navigate_url == "https://example.com/category"


class TestAgentEscalation:
    """Escalation triggers when planner runs out of replans."""

    def _mock_agent_with_escalation(self, subtasks, nav_results, replan_results=None):
        """Build an Agent with a real EscalationChain plus mocked planner/navigator."""
        from fantoma.resilience.escalation import EscalationChain

        agent = Agent.__new__(Agent)
        agent._max_steps = 25
        agent._sensitive_data = {}

        agent._planner = MagicMock()
        agent._planner.decompose.return_value = subtasks
        agent._planner.summarise.return_value = "Final answer"
        if replan_results is None:
            agent._planner.replan.return_value = None
        else:
            agent._planner.replan.side_effect = replan_results
        # Stub _llm reassignment target so _escalate_llm doesn't crash on real LLMClient
        agent._planner._llm = MagicMock()

        agent._navigator = MagicMock()
        agent._navigator.execute.side_effect = nav_results

        agent.fantoma = MagicMock()
        agent.fantoma.start.return_value = {"url": "https://example.com", "aria_tree": ""}
        agent.fantoma._engine = MagicMock()
        page_mock = MagicMock()
        page_mock.url = "https://example.com"
        page_mock.title.return_value = "Example"
        agent.fantoma._engine.get_page.return_value = page_mock
        agent.fantoma._dom = MagicMock()
        agent.fantoma._dom.extract_content.return_value = "Page content"
        agent.fantoma._dom.extract.return_value = "Page: Example\nURL: https://example.com"

        agent._llm = MagicMock()
        # Real EscalationChain so the flag flips correctly
        agent.escalation = EscalationChain(
            endpoints=["http://localhost:8081/v1", "https://openrouter.ai/api/v1"],
            api_keys=["", "sk-or-test"],
            models=["auto", "qwen/qwen3.6-plus"],
        )
        return agent

    def test_escalation_fires_when_replans_exhausted(self):
        """When planner.replan returns None, agent escalates and re-decomposes."""
        original = [Subtask("Search broken thing", "interact", "Results visible")]
        nav_results = [
            NavigatorResult("stagnant", "Stopped: action_cycle", 5, [], "https://example.com",
                            failure_reason="action_cycle", last_actions=[]),
            NavigatorResult("done", "Found via direct URL", 3, [], "https://example.com/results"),
        ]
        agent = self._mock_agent_with_escalation(original, nav_results)
        # planner.replan returns None on first call -> escalation triggers
        # Then decompose is called again with the escalated LLM
        agent._planner.decompose.side_effect = [
            original,
            [Subtask("Navigate to direct results URL", "find", "Page loaded")],
        ]

        result = agent.run("Find results", start_url="https://example.com")

        # Escalation fired exactly once
        assert agent.escalation.total_escalations == 1
        assert agent.escalation.current_endpoint() == "https://openrouter.ai/api/v1"
        # Decompose was called twice: once initially, once after escalation
        assert agent._planner.decompose.call_count == 2
        # Navigator ran the original subtask, then the escalated subtask
        assert agent._navigator.execute.call_count == 2
        assert result.success is True

    def test_no_escalation_when_chain_exhausted(self):
        """If can_escalate() is False, agent gives up after replans run out."""
        from fantoma.resilience.escalation import EscalationChain

        original = [Subtask("Try thing", "interact", "Done")]
        nav_results = [
            NavigatorResult("stagnant", "Stopped", 5, [], "https://example.com",
                            failure_reason="action_cycle", last_actions=[]),
        ]
        agent = self._mock_agent_with_escalation(original, nav_results)
        # Single-tier chain, no escalation possible
        agent.escalation = EscalationChain(
            endpoints=["http://localhost:8081/v1"], api_keys=[""], models=["auto"],
        )

        result = agent.run("Find thing", start_url="https://example.com")

        assert agent.escalation.total_escalations == 0
        # Decompose called once (initial), no re-decomposition
        assert agent._planner.decompose.call_count == 1


class TestAgentInitWithModels:
    """Agent.__init__ accepts escalation_models and propagates to EscalationChain."""

    def test_escalation_models_propagated(self):
        agent = Agent(
            llm_url="http://localhost:8081/v1",
            api_key="",
            model="auto",
            escalation=["http://localhost:8081/v1", "https://openrouter.ai/api/v1"],
            escalation_keys=["", "sk-or-test"],
            escalation_models=["auto", "qwen/qwen3.6-plus"],
            headless="virtual",
        )
        assert agent.escalation.current_model() == "auto"
        agent.escalation.escalate()
        assert agent.escalation.current_model() == "qwen/qwen3.6-plus"
        assert agent.escalation.current_api_key() == "sk-or-test"

    def test_escalation_models_default_to_auto(self):
        agent = Agent(
            llm_url="http://localhost:8081/v1",
            escalation=["http://localhost:8081/v1", "https://openrouter.ai/api/v1"],
            escalation_keys=["", "sk-or-test"],
            headless="virtual",
        )
        assert agent.escalation.current_model() == "auto"
        agent.escalation.escalate()
        assert agent.escalation.current_model() == "auto"
