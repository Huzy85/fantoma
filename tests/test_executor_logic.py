"""Tests for internal Executor methods with zero coverage."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


def _make_executor():
    """Create a bare Executor instance without calling __init__.

    Uses __new__ to bypass full initialisation (which requires live browser,
    LLM, config, etc.) and then wires up the minimal attributes each method
    under test actually touches.
    """
    from fantoma.executor import Executor

    ex = Executor.__new__(Executor)

    # Core dependencies — all mocked
    ex.browser = MagicMock()
    ex.llm = MagicMock()
    ex.config = MagicMock()
    ex.config.resilience.max_steps = 50
    ex.config.resilience.max_retries = 3
    ex.config.resilience.retry_levels = 3
    ex.config.extraction.max_elements = 100
    ex.config.extraction.max_headings = 20
    ex.config.extraction.max_page_text = 5000

    # Mocked sub-objects
    ex.dom = MagicMock()
    ex.dom._last_interactive = []
    ex.diff = MagicMock()
    ex.memory = MagicMock()
    ex.checkpoints = MagicMock()
    ex.cache = MagicMock()
    ex.captcha = MagicMock()
    ex.vision = MagicMock()
    ex.escalation = MagicMock()
    ex.escalation.can_escalate.return_value = False
    ex.escalation.total_escalations = 0

    # State attributes
    ex._total_actions = 0
    ex._completed_steps = []
    ex._consecutive_failures = 0
    ex._env_level = 1
    ex._step_history = []
    ex._action_outcomes = []
    ex._compacted_memory = ""
    ex._compact_threshold = 50
    ex._compact_keep_recent = 10
    ex._secrets = {}
    ex._stall_counter = 0

    return ex


# ─────────────────────────────────────────────────────────────
# _compact_history
# ─────────────────────────────────────────────────────────────

class TestCompactHistory:
    """_compact_history() compacts action_outcomes when they exceed the threshold."""

    def test_under_limit_no_compaction(self):
        """History well under the char threshold — LLM must NOT be called."""
        ex = _make_executor()
        ex._action_outcomes = ["Step 1: CLICK → ok", "Step 2: TYPE → ok"]

        ex._compact_history()

        ex.llm.chat.assert_not_called()
        # Outcomes unchanged
        assert len(ex._action_outcomes) == 2

    def test_over_limit_compacts(self):
        """History over the char threshold — LLM is called and history shrinks."""
        ex = _make_executor()
        # threshold ≈ 49000 * 2 * 0.4 = 39200 chars; fill well past it
        long_entry = "x" * 4000
        ex._action_outcomes = [long_entry] * 15  # 60 000 chars total

        ex.llm.chat.return_value = "Summarised progress"

        with patch("fantoma.executor.COMPACTION_SYSTEM", "sys", create=True):
            ex._compact_history()

        # LLM was called
        ex.llm.chat.assert_called_once()
        # Compacted memory updated
        assert ex._compacted_memory == "Summarised progress"
        # Only _compact_keep_recent entries remain
        assert len(ex._action_outcomes) == ex._compact_keep_recent

    def test_llm_exception_during_compaction_preserves_history(self):
        """If the LLM raises during compaction, history is left untouched."""
        ex = _make_executor()
        long_entry = "y" * 4000
        original_outcomes = [long_entry] * 15
        ex._action_outcomes = list(original_outcomes)

        ex.llm.chat.side_effect = RuntimeError("LLM unavailable")

        ex._compact_history()

        # History must NOT be truncated after an exception
        assert ex._action_outcomes == original_outcomes
        # compacted_memory must remain empty
        assert ex._compacted_memory == ""


# ─────────────────────────────────────────────────────────────
# _page_likely_has_answer
# ─────────────────────────────────────────────────────────────

class TestPageLikelyHasAnswer:
    """_page_likely_has_answer() uses keyword matching, no LLM."""

    def _make_page(self, body_text: str, url: str = "https://example.com") -> MagicMock:
        page = MagicMock()
        page.inner_text.return_value = body_text
        page.url = url
        return page

    def test_matching_content_returns_true(self):
        ex = _make_executor()
        task = "What is the population of France?"
        page = self._make_page("France has a population of about 68 million people.")
        assert ex._page_likely_has_answer(task, page) is True

    def test_no_relevant_content_returns_false(self):
        ex = _make_executor()
        task = "What is the capital of Germany?"
        # Page text shares no meaningful keywords with the task
        page = self._make_page("Buy shoes online. Free shipping on orders over £50.")
        assert ex._page_likely_has_answer(task, page) is False

    def test_extract_task_with_data_on_page_returns_true(self):
        ex = _make_executor()
        task = "Extract the product price from this page"
        # 'product' and 'price' appear in the body → ≥30% match
        page = self._make_page("Product: Widget. Price: £9.99. Available now.")
        assert ex._page_likely_has_answer(task, page) is True

    def test_get_task_with_data_on_page_returns_true(self):
        ex = _make_executor()
        task = "Get the title of the article"
        page = self._make_page("Title: How Python Became Popular. Author: Jane.")
        assert ex._page_likely_has_answer(task, page) is True

    def test_exception_during_page_access_returns_false(self):
        ex = _make_executor()
        task = "Find the company address"
        page = MagicMock()
        page.inner_text.side_effect = Exception("context destroyed")
        page.url = "https://example.com"
        assert ex._page_likely_has_answer(task, page) is False

    def test_blank_page_returns_false(self):
        ex = _make_executor()
        task = "Find the population of Spain"
        page = self._make_page("population of spain 47 million", url="about:blank")
        assert ex._page_likely_has_answer(task, page) is False

    def test_task_with_no_extraction_signal_returns_false(self):
        """Task without any extraction signal keyword returns False immediately."""
        ex = _make_executor()
        task = "Click the login button"
        page = self._make_page("login button available")
        assert ex._page_likely_has_answer(task, page) is False


# ─────────────────────────────────────────────────────────────
# _task_wants_login  (static method)
# ─────────────────────────────────────────────────────────────

class TestTaskWantsLogin:
    """_task_wants_login() checks auth intent + form presence."""

    def _call(self, task: str, dom_text: str) -> bool:
        from fantoma.executor import Executor
        return Executor._task_wants_login(task, dom_text)

    def test_login_keyword_with_form_true(self):
        assert self._call(
            "Login to the website",
            "Enter your email and password to sign in"
        ) is True

    def test_sign_in_keyword_with_form_true(self):
        assert self._call(
            "Sign in using your credentials",
            "Username: [textbox]  Password: [textbox]"
        ) is True

    def test_log_in_keyword_with_form_true(self):
        assert self._call(
            "Log in to your account",
            "Log in with email and password"
        ) is True

    def test_unrelated_task_returns_false(self):
        assert self._call(
            "Search for the best Python books",
            "Search results for Python books"
        ) is False

    def test_auth_intent_but_no_form_returns_false(self):
        """Login intent present but page has no form fields."""
        assert self._call(
            "Login to the dashboard",
            "Welcome to the main page. No fields here."
        ) is False

    def test_case_insensitive_matching(self):
        """Keywords must match regardless of case in the task string."""
        assert self._call(
            "SIGN UP for an account",
            "Create account — enter your email and password"
        ) is True

    def test_signup_single_word(self):
        assert self._call(
            "signup with email",
            "Register — email and password required"
        ) is True

    def test_create_account_keyword(self):
        assert self._call(
            "Create account on the platform",
            "Sign up: email address, username, password"
        ) is True


# ─────────────────────────────────────────────────────────────
# _check_page_change
# ─────────────────────────────────────────────────────────────

class TestCheckPageChange:
    """_check_page_change() waits, diffs, records, and returns changed bool."""

    def test_dom_changed_returns_true(self):
        ex = _make_executor()
        page = MagicMock()
        before = MagicMock()
        after = MagicMock()

        ex.diff.snapshot.return_value = after
        ex.diff.changed.return_value = True
        ex.memory.hash_dom.return_value = "hash_after"
        ex.dom.extract.return_value = "dom text"

        with patch("fantoma.executor.wait_for_dom_stable"), \
             patch("fantoma.executor.wait_for_navigation"):
            result = ex._check_page_change(page, before, "hash_before", "CLICK [0]", 1)

        assert result is True
        ex.memory.record.assert_called_once_with("CLICK [0]", "hash_before", "hash_after", True, 1)

    def test_dom_unchanged_returns_false(self):
        ex = _make_executor()
        page = MagicMock()
        before = MagicMock()
        after = MagicMock()

        ex.diff.snapshot.return_value = after
        ex.diff.changed.return_value = False
        ex.memory.hash_dom.return_value = "hash_same"
        ex.dom.extract.return_value = "dom text"

        with patch("fantoma.executor.wait_for_dom_stable"), \
             patch("fantoma.executor.wait_for_navigation"):
            result = ex._check_page_change(page, before, "hash_same", "CLICK [1]", 2)

        assert result is False

    def test_navigation_exception_returns_true(self):
        """If snapshot raises a navigation/context-destroyed error, return True."""
        ex = _make_executor()
        page = MagicMock()
        before = MagicMock()

        ex.diff.snapshot.side_effect = Exception("context was destroyed")

        with patch("fantoma.executor.wait_for_dom_stable"), \
             patch("fantoma.executor.wait_for_navigation"):
            result = ex._check_page_change(page, before, "hash_x", "CLICK [0]", 1)

        assert result is True

    def test_unexpected_exception_propagates(self):
        """Non-navigation exceptions must propagate."""
        ex = _make_executor()
        page = MagicMock()
        before = MagicMock()

        ex.diff.snapshot.side_effect = RuntimeError("something else entirely")

        with patch("fantoma.executor.wait_for_dom_stable"), \
             patch("fantoma.executor.wait_for_navigation"):
            with pytest.raises(RuntimeError, match="something else entirely"):
                ex._check_page_change(page, before, "hash_x", "CLICK [0]", 1)

    def test_wait_for_navigation_exception_is_swallowed(self):
        """wait_for_navigation timeout must not crash _check_page_change."""
        ex = _make_executor()
        page = MagicMock()
        before = MagicMock()
        after = MagicMock()

        ex.diff.snapshot.return_value = after
        ex.diff.changed.return_value = False
        ex.memory.hash_dom.return_value = "h"
        ex.dom.extract.return_value = ""

        with patch("fantoma.executor.wait_for_dom_stable"), \
             patch("fantoma.executor.wait_for_navigation", side_effect=Exception("timeout")):
            # Should not raise
            result = ex._check_page_change(page, before, "h", "CLICK [0]", 1)

        assert result is False


# ─────────────────────────────────────────────────────────────
# _maybe_escalate
# ─────────────────────────────────────────────────────────────

class TestMaybeEscalate:
    """_maybe_escalate() fires only after 3+ consecutive failures."""

    def test_below_threshold_no_escalation(self):
        ex = _make_executor()
        ex._consecutive_failures = 2

        ex._maybe_escalate()

        ex.escalation.can_escalate.assert_not_called()

    def test_at_threshold_model_escalation(self):
        """At 3 consecutive failures with escalation available → escalates model."""
        ex = _make_executor()
        ex._consecutive_failures = 3
        ex.escalation.can_escalate.return_value = True
        ex.escalation.escalate.return_value = "http://new-endpoint"
        ex.escalation.current_api_key.return_value = "apikey123"

        with patch("fantoma.executor.LLMClient") as MockLLM:
            ex._maybe_escalate()

        ex.escalation.escalate.assert_called_once()
        MockLLM.assert_called_once_with(base_url="http://new-endpoint", api_key="apikey123")
        assert ex._consecutive_failures == 0

    def test_above_threshold_no_escalation_chain(self):
        """At 3+ failures but no escalation available and env escalation fails → no-op."""
        ex = _make_executor()
        ex._consecutive_failures = 5
        ex.escalation.can_escalate.return_value = False
        # _try_env_escalation will check _env_level >= retry_levels
        ex._env_level = 3
        ex.config.resilience.retry_levels = 3

        ex._maybe_escalate()

        # _consecutive_failures unchanged since neither path reset it
        assert ex._consecutive_failures == 5

    def test_at_threshold_env_escalation_when_model_exhausted(self):
        """Model escalation exhausted → env escalation is tried."""
        ex = _make_executor()
        ex._consecutive_failures = 3
        ex.escalation.can_escalate.return_value = False
        # env level 1 → can escalate to level 2 (clear cookies)
        ex._env_level = 1
        ex.config.resilience.retry_levels = 3
        ex.browser.clear_cookies = MagicMock()

        ex._maybe_escalate()

        ex.browser.clear_cookies.assert_called_once()
        assert ex._consecutive_failures == 0

    def test_zero_failures_no_op(self):
        ex = _make_executor()
        ex._consecutive_failures = 0

        ex._maybe_escalate()

        ex.escalation.can_escalate.assert_not_called()
