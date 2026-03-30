"""Tests for cache replay wiring in executor."""
import pytest
from unittest.mock import MagicMock, patch


class TestCacheReplayMethodExists:
    """Verify that ScriptCache is wired into Executor."""

    def test_executor_has_cache_attribute(self):
        """Executor.__init__ must create self.cache = ScriptCache()."""
        from fantoma.executor import Executor
        from fantoma.resilience.script_cache import ScriptCache

        with patch.object(ScriptCache, '__init__', return_value=None):
            browser = MagicMock()
            llm = MagicMock()
            config = MagicMock()
            config.extraction.max_elements = 100
            config.extraction.max_headings = 20
            config.resilience.max_steps = 50
            config.resilience.max_retries = 3
            config.resilience.retry_levels = 3

            with patch("fantoma.dom.accessibility.AccessibilityExtractor"):
                executor = Executor(browser, llm, config)

            assert hasattr(executor, 'cache')

    def test_executor_has_replay_cached_method(self):
        """Executor must have _replay_cached method."""
        from fantoma.executor import Executor
        assert hasattr(Executor, '_replay_cached')


class TestReplayCachedLogic:
    """Test _replay_cached with mocked browser/DOM."""

    @pytest.fixture
    def executor(self):
        from fantoma.executor import Executor
        from fantoma.resilience.script_cache import ScriptCache

        with patch.object(ScriptCache, '__init__', return_value=None), \
             patch("fantoma.dom.accessibility.AccessibilityExtractor"):
            browser = MagicMock()
            llm = MagicMock()
            config = MagicMock()
            config.extraction.max_elements = 100
            config.extraction.max_headings = 20
            config.resilience.max_steps = 50
            config.resilience.max_retries = 3
            config.resilience.retry_levels = 3

            executor = Executor(browser, llm, config)
            # Set up dom mock
            executor.dom = MagicMock()
            executor.dom._last_interactive = [
                {"role": "button", "name": "Login"},
                {"role": "textbox", "name": "Email"},
            ]
            return executor

    def test_replay_returns_true_on_all_actions_succeed(self, executor):
        page = MagicMock()

        cached_actions = [
            {"action": "CLICK [0]", "target_role": "button", "target_name": "Login"},
        ]

        with patch("fantoma.executor.execute_action", return_value=True), \
             patch("fantoma.executor.assess_progress", return_value={"action_ok": True, "progress_ok": True, "reason": ""}), \
             patch("fantoma.executor.wait_for_dom_stable"):
            result = executor._replay_cached(page, cached_actions, "login task")

        assert result is True

    def test_replay_returns_false_on_heal_failure(self, executor):
        page = MagicMock()

        # Element at index 0 has wrong role/name — heal_action will fail
        executor.dom._last_interactive = [
            {"role": "link", "name": "Something else"},
        ]

        cached_actions = [
            {"action": "CLICK [0]", "target_role": "button", "target_name": "Login"},
        ]

        with patch("fantoma.executor.execute_action", return_value=True), \
             patch("fantoma.executor.assess_progress", return_value={"action_ok": True, "progress_ok": True, "reason": ""}), \
             patch("fantoma.executor.wait_for_dom_stable"):
            result = executor._replay_cached(page, cached_actions, "login task")

        assert result is False

    def test_replay_returns_false_on_action_not_ok(self, executor):
        page = MagicMock()

        cached_actions = [
            {"action": "CLICK [0]", "target_role": "button", "target_name": "Login"},
        ]

        with patch("fantoma.executor.execute_action", return_value=True), \
             patch("fantoma.executor.assess_progress", return_value={"action_ok": False, "progress_ok": False, "reason": "error"}), \
             patch("fantoma.executor.wait_for_dom_stable"):
            result = executor._replay_cached(page, cached_actions, "login task")

        assert result is False

    def test_replay_injects_secrets(self, executor):
        page = MagicMock()
        executor._secrets = {"email": "test@test.com"}

        cached_actions = [
            {"action": 'TYPE [1] "<secret:email>"', "target_role": "textbox", "target_name": "Email"},
        ]

        executed_actions = []

        def capture_action(action, *args, **kwargs):
            executed_actions.append(action)
            return True

        with patch("fantoma.executor.execute_action", side_effect=capture_action), \
             patch("fantoma.executor.assess_progress", return_value={"action_ok": True, "progress_ok": True, "reason": ""}), \
             patch("fantoma.executor.wait_for_dom_stable"):
            result = executor._replay_cached(page, cached_actions, "login task")

        assert result is True
        assert "test@test.com" in executed_actions[0]

    def test_replay_heals_shifted_element(self, executor):
        page = MagicMock()

        # Login button moved from index 0 to index 1
        executor.dom._last_interactive = [
            {"role": "textbox", "name": "Search"},
            {"role": "button", "name": "Login"},
        ]

        cached_actions = [
            {"action": "CLICK [0]", "target_role": "button", "target_name": "Login"},
        ]

        executed_actions = []

        def capture_action(action, *args, **kwargs):
            executed_actions.append(action)
            return True

        with patch("fantoma.executor.execute_action", side_effect=capture_action), \
             patch("fantoma.executor.assess_progress", return_value={"action_ok": True, "progress_ok": True, "reason": ""}), \
             patch("fantoma.executor.wait_for_dom_stable"):
            result = executor._replay_cached(page, cached_actions, "login task")

        assert result is True
        # Action should have been rewritten with new index [1]
        assert "CLICK [1]" in executed_actions[0]

    def test_replay_skips_dom_wait_for_scroll(self, executor):
        page = MagicMock()

        cached_actions = [
            {"action": "SCROLL down", "target_role": "", "target_name": ""},
        ]

        with patch("fantoma.executor.execute_action", return_value=True), \
             patch("fantoma.executor.assess_progress", return_value={"action_ok": True, "progress_ok": True, "reason": ""}), \
             patch("fantoma.executor.wait_for_dom_stable") as mock_wait:
            result = executor._replay_cached(page, cached_actions, "task")

        assert result is True
        mock_wait.assert_not_called()
