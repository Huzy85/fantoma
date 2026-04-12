# Flat-First Agent with Hierarchical Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure Agent.run() into a two-phase execution: Phase 1 runs a flat reactive loop (20 steps), Phase 2 activates the existing hierarchical planner only if Phase 1 stalls.

**Architecture:** Phase 1 creates a single catch-all Subtask and hands it to the existing Navigator. If it returns "done" with real data, skip Phase 2 and summarise. If it stalls, hand Phase 1's context (visited URLs, partial data, failure reason) to the Planner for decomposition and run the existing v0.8 orchestration loop with the remaining step budget.

**Tech Stack:** Python 3.12, pytest, unittest.mock

**Spec:** `docs/superpowers/specs/2026-04-10-fantoma-flat-first-agent-design.md`

---

### Task 1: Extract `_has_real_data()` helper

The inline `has_real_data` check (agent.py:201-206) is used in Phase 1 and Phase 2. Extract it as a module-level function.

**Files:**
- Modify: `fantoma/agent.py:200-206`
- Test: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add this test class at the bottom of `tests/test_agent_orchestrator.py`:

```python
class TestHasRealData:
    """Unit tests for the _has_real_data helper."""

    def test_real_data_returns_true(self):
        from fantoma.agent import _has_real_data
        assert _has_real_data("Price is $999") is True

    def test_stopped_prefix_returns_false(self):
        from fantoma.agent import _has_real_data
        assert _has_real_data("Stopped: action_cycle") is False

    def test_domain_drift_returns_false(self):
        from fantoma.agent import _has_real_data
        assert _has_real_data("Domain drift to other.com") is False

    def test_blocked_prefix_returns_false(self):
        from fantoma.agent import _has_real_data
        assert _has_real_data("Blocked: login wall") is False

    def test_empty_string_returns_false(self):
        from fantoma.agent import _has_real_data
        assert _has_real_data("") is False

    def test_none_returns_false(self):
        from fantoma.agent import _has_real_data
        assert _has_real_data(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py::TestHasRealData -v`

Expected: FAIL with `ImportError: cannot import name '_has_real_data'`

- [ ] **Step 3: Write the helper function**

Add this function in `fantoma/agent.py` after the `_google_fallback_subtasks` function (after line 79), before the `AgentResult` dataclass:

```python
def _has_real_data(data: str | None) -> bool:
    """True if data contains a real answer, not a placeholder status line."""
    return bool(
        data
        and not data.startswith("Stopped:")
        and not data.startswith("Domain drift")
        and not data.startswith("Blocked:")
    )
```

- [ ] **Step 4: Replace the inline check in `run()`**

In `fantoma/agent.py`, replace the inline `has_real_data` block (lines 200-206):

```python
                # Real data means not a placeholder status line from the navigator.
                has_real_data = bool(
                    result.data
                    and not result.data.startswith("Stopped:")
                    and not result.data.startswith("Domain drift")
                    and not result.data.startswith("Blocked:")
                )
```

With:

```python
                has_real_data = _has_real_data(result.data)
```

- [ ] **Step 5: Run all tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py -v`

Expected: ALL PASS (new tests + existing tests unchanged)

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py tests/test_agent_orchestrator.py
git commit -m "refactor: extract _has_real_data() helper from inline check"
```

---

### Task 2: Add `flat_budget` parameter to Agent.__init__

**Files:**
- Modify: `fantoma/agent.py:102-116`
- Test: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add to `TestAgentInitWithModels` class in `tests/test_agent_orchestrator.py`:

```python
    def test_flat_budget_default(self):
        agent = Agent(
            llm_url="http://localhost:8081/v1",
            headless="virtual",
        )
        assert agent._flat_budget == 20

    def test_flat_budget_custom(self):
        agent = Agent(
            llm_url="http://localhost:8081/v1",
            flat_budget=10,
            headless="virtual",
        )
        assert agent._flat_budget == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py::TestAgentInitWithModels::test_flat_budget_default -v`

Expected: FAIL with `AttributeError: 'Agent' object has no attribute '_flat_budget'`

- [ ] **Step 3: Add the parameter**

In `fantoma/agent.py`, modify `Agent.__init__` signature. Add `flat_budget: int = 20,` after the `max_steps` parameter:

```python
    def __init__(
        self,
        llm_url: str = "http://localhost:8080/v1",
        api_key: str = "",
        model: str = "auto",
        escalation: list[str] = None,
        escalation_keys: list[str] = None,
        escalation_models: list[str] = None,
        max_steps: int = 50,
        flat_budget: int = 20,
        sensitive_data: dict = None,
        **kwargs,
    ):
```

And add this line after `self._max_steps = max_steps`:

```python
        self._flat_budget = min(flat_budget, max_steps)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py -v`

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py tests/test_agent_orchestrator.py
git commit -m "feat: add flat_budget parameter to Agent.__init__"
```

---

### Task 3: Add `_build_phase1_context()` helper

Extracts context from Phase 1's NavigatorResult for handover to Phase 2.

**Files:**
- Modify: `fantoma/agent.py`
- Test: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add this test class in `tests/test_agent_orchestrator.py`:

```python
class TestBuildPhase1Context:
    """Unit tests for _build_phase1_context helper."""

    def test_extracts_visited_urls(self):
        from fantoma.agent import _build_phase1_context
        result = NavigatorResult(
            status="stagnant", data="Some data", steps_taken=5,
            steps_detail=[
                {"url": "https://a.com"},
                {"url": "https://b.com"},
                {"url": "https://a.com"},
            ],
            final_url="https://a.com",
            failure_reason="action_cycle",
            last_actions=["click(1)", "click(2)"],
        )
        ctx = _build_phase1_context(result)
        assert ctx["visited_urls"] == ["https://a.com", "https://b.com", "https://a.com"]
        assert ctx["steps_taken"] == 5
        assert ctx["final_url"] == "https://a.com"
        assert ctx["failure_reason"] == "action_cycle"
        assert ctx["last_actions"] == ["click(1)", "click(2)"]

    def test_real_data_preserved(self):
        from fantoma.agent import _build_phase1_context
        result = NavigatorResult(
            status="stagnant", data="Price is $42", steps_taken=3,
            steps_detail=[], final_url="https://x.com",
        )
        ctx = _build_phase1_context(result)
        assert ctx["partial_data"] == "Price is $42"

    def test_placeholder_data_excluded(self):
        from fantoma.agent import _build_phase1_context
        result = NavigatorResult(
            status="stagnant", data="Stopped: action_cycle", steps_taken=3,
            steps_detail=[], final_url="https://x.com",
        )
        ctx = _build_phase1_context(result)
        assert ctx["partial_data"] is None

    def test_empty_steps_detail(self):
        from fantoma.agent import _build_phase1_context
        result = NavigatorResult(
            status="failed", data="", steps_taken=0,
            steps_detail=[], final_url="",
        )
        ctx = _build_phase1_context(result)
        assert ctx["visited_urls"] == []
        assert ctx["steps_taken"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py::TestBuildPhase1Context -v`

Expected: FAIL with `ImportError: cannot import name '_build_phase1_context'`

- [ ] **Step 3: Write the helper**

Add this function in `fantoma/agent.py` after `_has_real_data()`:

```python
def _build_phase1_context(result: NavigatorResult) -> dict:
    """Extract context from Phase 1's NavigatorResult for Phase 2 handover."""
    return {
        "visited_urls": [s.get("url", "") for s in result.steps_detail if s.get("url")],
        "steps_taken": result.steps_taken,
        "partial_data": result.data if _has_real_data(result.data) else None,
        "final_url": result.final_url,
        "failure_reason": result.failure_reason,
        "last_actions": result.last_actions,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py -v`

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py tests/test_agent_orchestrator.py
git commit -m "feat: add _build_phase1_context() helper for phase handover"
```

---

### Task 4: Restructure `run()` into Phase 1 + Phase 2

This is the main change. Replace the current `run()` method body with the two-phase loop.

**Files:**
- Modify: `fantoma/agent.py:147-316`
- Test: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Write Phase 1 success test**

Add this class in `tests/test_agent_orchestrator.py`:

```python
class TestFlatFirstAgent:
    """Tests for the two-phase flat-first agent architecture."""

    def _mock_agent(self, navigator_results, planner_subtasks=None, summary="Final answer"):
        """Build an Agent with mocked components for flat-first testing."""
        agent = Agent.__new__(Agent)
        agent._max_steps = 30
        agent._flat_budget = 20

        agent._planner = MagicMock()
        if planner_subtasks:
            agent._planner.decompose.return_value = planner_subtasks
        agent._planner.summarise.return_value = summary
        agent._planner.replan.return_value = None

        agent._navigator = MagicMock()
        agent._navigator.execute.side_effect = navigator_results

        agent.fantoma = MagicMock()
        agent.fantoma.start.return_value = {"url": "https://example.com", "aria_tree": ""}
        agent.fantoma._engine = MagicMock()
        page_mock = MagicMock()
        page_mock.url = "https://example.com"
        page_mock.title.return_value = "Example"
        agent.fantoma._engine.get_page.return_value = page_mock
        agent.fantoma._dom = MagicMock()
        agent.fantoma._dom.extract_content.return_value = "Page content"
        agent.fantoma._dom.extract.return_value = "Page: Example"

        agent._llm = MagicMock()
        agent._sensitive_data = {}
        agent.escalation = MagicMock()
        agent.escalation.total_escalations = 0
        agent.escalation.can_escalate.return_value = False

        return agent

    def test_phase1_success_skips_planner(self):
        """Phase 1 completes with data. Planner.decompose() never called."""
        nav_results = [
            NavigatorResult("done", "Recipe: Chocolate Cake", 5, [
                {"url": "https://allrecipes.com/search?q=cake"},
                {"url": "https://allrecipes.com/recipe/123"},
            ], "https://allrecipes.com/recipe/123"),
        ]
        agent = self._mock_agent(nav_results, summary="Chocolate Cake recipe found")
        result = agent.run("Find a chocolate cake recipe", start_url="https://allrecipes.com")

        assert result.success is True
        assert result.data == "Chocolate Cake recipe found"
        assert result.steps_taken == 5
        agent._planner.decompose.assert_not_called()
        agent._planner.summarise.assert_called_once()

    def test_phase1_stall_triggers_phase2(self):
        """Phase 1 stalls. Phase 2 decomposes with Phase 1 context."""
        nav_results = [
            # Phase 1: stalls after 8 steps
            NavigatorResult("stagnant", "Stopped: action_cycle", 8, [
                {"url": "https://example.com"},
                {"url": "https://example.com/page2"},
            ], "https://example.com/page2",
               failure_reason="action_cycle", last_actions=["click(1)", "click(1)"]),
            # Phase 2: subtask 1 succeeds
            NavigatorResult("done", "Found the answer: 42", 3, [],
                            "https://example.com/answer"),
        ]
        phase2_subtasks = [Subtask("Find answer via search", "find", "Answer visible")]
        agent = self._mock_agent(nav_results, planner_subtasks=phase2_subtasks,
                                  summary="The answer is 42")

        result = agent.run("Find the answer", start_url="https://example.com")

        assert result.success is True
        assert result.steps_taken == 11  # 8 + 3
        agent._planner.decompose.assert_called_once()
        # Verify Phase 1 context was passed in the decompose prompt
        decompose_args = agent._planner.decompose.call_args
        page_summary_arg = decompose_args[0][1]  # second positional arg
        assert "action_cycle" in page_summary_arg or "Previous attempt" in page_summary_arg

    def test_phase1_partial_data_preserved_in_phase2(self):
        """Phase 1 stalls with real data. That data appears in Phase 2 completed list."""
        nav_results = [
            # Phase 1: stalls but has partial data
            NavigatorResult("stagnant", "Price: $99", 6, [],
                            "https://example.com/product",
                            failure_reason="scroll_limit", last_actions=[]),
            # Phase 2: completes
            NavigatorResult("done", "Color: Red", 2, [],
                            "https://example.com/product/details"),
        ]
        phase2_subtasks = [Subtask("Get color", "read", "Color found")]
        agent = self._mock_agent(nav_results, planner_subtasks=phase2_subtasks,
                                  summary="Price $99, Color Red")

        result = agent.run("Get price and color", start_url="https://example.com")

        assert result.success is True
        # summarise should include Phase 1 partial data
        summarise_args = agent._planner.summarise.call_args[0]
        completed_list = summarise_args[1]
        # Phase 1 partial data should be in the completed list
        phase1_data = [r.data for _, r in completed_list if r.data == "Price: $99"]
        assert len(phase1_data) == 1

    def test_step_budget_accounting(self):
        """Phase 1 uses 12 steps. Phase 2 gets max_steps - 12 = 18."""
        nav_results = [
            NavigatorResult("stagnant", "Stopped: dom_stagnant", 12, [],
                            "https://example.com",
                            failure_reason="dom_stagnant", last_actions=[]),
            NavigatorResult("done", "Result found", 5, [],
                            "https://example.com/result"),
        ]
        phase2_subtasks = [Subtask("Try search", "find", "Found")]
        agent = self._mock_agent(nav_results, planner_subtasks=phase2_subtasks)
        agent._max_steps = 30
        agent._flat_budget = 20

        agent.run("Find thing", start_url="https://example.com")

        # Phase 1 call should have max_steps=20 (flat_budget)
        phase1_call = agent._navigator.execute.call_args_list[0]
        assert phase1_call.kwargs.get("max_steps") == 20

    def test_phase1_zero_steps_phase2_gets_full_budget(self):
        """Navigator crashes immediately. Phase 2 gets full budget."""
        nav_results = [
            NavigatorResult("failed", "", 0, [],
                            "", failure_reason="browser_error", last_actions=[]),
            NavigatorResult("done", "Found it", 8, [],
                            "https://example.com/result"),
        ]
        phase2_subtasks = [Subtask("Navigate directly", "find", "Found")]
        agent = self._mock_agent(nav_results, planner_subtasks=phase2_subtasks)

        result = agent.run("Find thing", start_url="https://example.com")

        assert result.success is True
        assert result.steps_taken == 8

    def test_phase2_escalation_works(self):
        """Phase 2 replans exhausted, escalation fires as before."""
        from fantoma.resilience.escalation import EscalationChain

        nav_results = [
            # Phase 1 stalls
            NavigatorResult("stagnant", "Stopped: action_cycle", 10, [],
                            "https://example.com",
                            failure_reason="action_cycle", last_actions=[]),
            # Phase 2 subtask 1 also stalls
            NavigatorResult("stagnant", "Stopped: dom_stagnant", 5, [],
                            "https://example.com",
                            failure_reason="dom_stagnant", last_actions=[]),
            # After escalation, new subtask succeeds
            NavigatorResult("done", "Found via escalated model", 3, [],
                            "https://example.com/answer"),
        ]
        phase2_subtasks = [Subtask("Search for info", "find", "Info found")]
        escalated_subtasks = [Subtask("Direct URL approach", "find", "Done")]

        agent = self._mock_agent(nav_results, planner_subtasks=phase2_subtasks)
        agent.escalation = EscalationChain(
            endpoints=["http://localhost:8081/v1", "https://openrouter.ai/api/v1"],
            api_keys=["", "sk-or-test"],
            models=["auto", "qwen/qwen3.6-plus"],
        )
        agent._planner._llm = MagicMock()
        agent._planner.decompose.side_effect = [phase2_subtasks, escalated_subtasks]

        result = agent.run("Find info", start_url="https://example.com")

        assert result.success is True
        assert agent.escalation.total_escalations == 1

    def test_phase1_uses_flat_budget_not_max_steps(self):
        """Phase 1 Navigator gets flat_budget, not max_steps."""
        nav_results = [
            NavigatorResult("done", "Found it", 3, [],
                            "https://example.com/result"),
        ]
        agent = self._mock_agent(nav_results)
        agent._max_steps = 50
        agent._flat_budget = 15

        agent.run("Find thing", start_url="https://example.com")

        call_kwargs = agent._navigator.execute.call_args.kwargs
        assert call_kwargs["max_steps"] == 15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py::TestFlatFirstAgent -v`

Expected: FAIL because `run()` still uses the old hierarchical-only flow (Phase 1 tests will fail because `decompose` IS called).

- [ ] **Step 3: Rewrite the `run()` method**

Replace the entire `run()` method body in `fantoma/agent.py` (lines 147-316) with:

```python
    def run(self, task: str, start_url: str = None) -> AgentResult:
        """Run a browser task described in English.

        Two-phase execution:
        - Phase 1: flat reactive loop with a single catch-all subtask
        - Phase 2: hierarchical planner (only if Phase 1 stalls)
        """
        log.info("Task: %s", task)
        self.fantoma._task = task

        start_domain = ""
        if start_url:
            try:
                start_domain = urlparse(start_url).netloc
            except Exception:
                pass

        try:
            state = self.fantoma.start(start_url)
        except Exception as e:
            return AgentResult(success=False, error=f"Browser start failed: {e}")

        total_steps = 0
        all_steps = []

        try:
            # ── Phase 1: Flat reactive loop ──────────────────────────
            flat_subtask = Subtask(
                instruction=task,
                mode="find",
                done_when="Task is complete",
            )
            tracker = StateTracker()
            phase1_result = self._navigator.execute(
                subtask=flat_subtask,
                fantoma=self.fantoma,
                llm=self._llm,
                tracker=tracker,
                max_steps=self._flat_budget,
                start_domain=start_domain,
                sensitive_data=self._sensitive_data,
            )

            all_steps.extend(phase1_result.steps_detail)
            total_steps += phase1_result.steps_taken

            # Phase 1 success: skip Phase 2
            if phase1_result.status == "done" and _has_real_data(phase1_result.data):
                answer = self._planner.summarise(
                    task, [(flat_subtask, phase1_result)]
                )
                return AgentResult(
                    success=True,
                    data=answer,
                    steps_taken=total_steps,
                    steps_detail=all_steps,
                    escalations=self.escalation.total_escalations,
                )

            # ── Phase 2: Hierarchical fallback ───────────────────────
            log.info(
                "Phase 1 ended (%s, %d steps). Entering Phase 2.",
                phase1_result.status, phase1_result.steps_taken,
            )
            phase1_ctx = _build_phase1_context(phase1_result)

            self._planner.reset()
            remaining_budget = self._max_steps - total_steps

            # Build Phase 1 context for the decompose prompt
            ctx_lines = [
                f"Previous attempt (flat loop) tried {phase1_ctx['steps_taken']} steps.",
                f"Visited: {'; '.join(phase1_ctx['visited_urls'][-12:]) or 'none'}",
                f"Stopped because: {phase1_ctx['failure_reason'] or 'unknown'}",
                f"Last actions: {'; '.join(phase1_ctx['last_actions'] or []) or 'none'}",
            ]
            summary = self._get_page_summary()
            enriched_summary = "\n".join(ctx_lines) + "\n\n" + summary

            subtasks = self._planner.decompose(task, enriched_summary)

            # Seed completed list with Phase 1 partial data if any
            completed = []
            if phase1_ctx["partial_data"]:
                completed.append((flat_subtask, phase1_result))

            # Checkpoint from Phase 1
            checkpoints = []
            if phase1_ctx["final_url"]:
                checkpoints.append(Checkpoint(
                    url=phase1_ctx["final_url"],
                    subtask=flat_subtask,
                    result_summary=(phase1_ctx["partial_data"] or "")[:200],
                ))

            recent_failed_instructions: deque[str] = deque(maxlen=4)
            google_fallback_used = False

            i = 0
            while i < len(subtasks) and remaining_budget > 0:
                subtask = subtasks[i]
                n_remaining = len(subtasks) - i
                step_budget = max(5, remaining_budget // max(1, n_remaining))
                tracker = StateTracker()

                result = self._navigator.execute(
                    subtask=subtask,
                    fantoma=self.fantoma,
                    llm=self._llm,
                    tracker=tracker,
                    max_steps=step_budget,
                    start_domain=start_domain,
                    sensitive_data=self._sensitive_data,
                )

                all_steps.extend(result.steps_detail)
                total_steps += result.steps_taken
                remaining_budget -= result.steps_taken

                has_real_data = _has_real_data(result.data)

                if result.status == "done":
                    completed.append((subtask, result))
                    checkpoints.append(Checkpoint(
                        url=result.final_url,
                        subtask=subtask,
                        result_summary=result.data[:200],
                    ))
                    i += 1
                    continue

                if has_real_data:
                    completed.append((subtask, result))
                    if result.final_url:
                        checkpoints.append(Checkpoint(
                            url=result.final_url,
                            subtask=subtask,
                            result_summary=result.data[:200],
                        ))

                visited_urls = _dedup_urls([s.get("url", "") for s in all_steps])
                summary = self._get_page_summary()

                recent_failed_instructions.append(subtask.instruction)
                repeat_count = sum(
                    1 for prev in recent_failed_instructions
                    if _instruction_similar(prev, subtask.instruction)
                )
                if repeat_count >= 2 and not google_fallback_used:
                    log.info(
                        "Subtask-cycle detected (%d similar failures). Forcing google search fallback.",
                        repeat_count,
                    )
                    new_subtasks = _google_fallback_subtasks(task)
                    google_fallback_used = True
                    subtasks = subtasks[:i] + new_subtasks
                    continue

                new_subtasks = self._planner.replan(
                    task, completed, subtask, summary,
                    failure_reason=result.failure_reason,
                    last_actions=result.last_actions,
                    visited_urls=visited_urls,
                )

                if (new_subtasks
                        and not google_fallback_used
                        and _instruction_similar(new_subtasks[0].instruction, subtask.instruction)):
                    log.info(
                        "Replan first step similar to failed subtask (%r ~ %r). Forcing google fallback.",
                        new_subtasks[0].instruction[:80], subtask.instruction[:80],
                    )
                    new_subtasks = _google_fallback_subtasks(task)
                    google_fallback_used = True

                if new_subtasks is None:
                    if self._escalate_llm():
                        log.info("Replans exhausted, re-decomposing with escalated model")
                        summary = self._get_page_summary()
                        new_subtasks = self._planner.decompose(task, summary)
                        subtasks = subtasks[:i] + new_subtasks
                        if checkpoints:
                            try:
                                self.fantoma.navigate(checkpoints[-1].url)
                            except Exception:
                                pass
                        continue
                    break
                subtasks = subtasks[:i] + new_subtasks
                if checkpoints:
                    try:
                        self.fantoma.navigate(checkpoints[-1].url)
                    except Exception:
                        pass
                continue

            answer = self._planner.summarise(task, completed)
            return AgentResult(
                success=bool(completed),
                data=answer,
                steps_taken=total_steps,
                steps_detail=all_steps,
                escalations=self.escalation.total_escalations,
            )
        except Exception as e:
            return AgentResult(success=False, error=str(e),
                               steps_taken=total_steps,
                               steps_detail=all_steps)
        finally:
            self.fantoma.stop()
```

- [ ] **Step 4: Run all new tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py::TestFlatFirstAgent -v`

Expected: ALL PASS

- [ ] **Step 5: Run all existing tests (expect failures)**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py -v`

Expected: `TestFlatFirstAgent` tests PASS. `TestAgentRunOrchestration` and `TestAgentEscalation` tests FAIL (missing `_flat_budget` attribute, wrong call counts). These are fixed in Task 5.

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py tests/test_agent_orchestrator.py
git commit -m "feat: restructure Agent.run() into flat-first + hierarchical fallback"
```

---

### Task 5: Fix existing orchestration tests for two-phase flow

The existing tests assumed the old hierarchical-only flow. Phase 1 now runs before Phase 2, adding one extra Navigator.execute call. Strategy: set `flat_budget=0` on mock agents and auto-prepend a Phase 1 stall result (`NavigatorResult("max_steps", "", 0, [], ...)`) to every navigator side_effect list. This makes Phase 1 a no-op (0 steps, status="max_steps") so Phase 2 runs with the full budget, preserving existing test semantics. Navigator.execute uses `for step_num in range(1, max_steps + 1)`, so max_steps=0 produces `range(1, 1)` which is empty.

**Files:**
- Modify: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Update `_mock_agent` in TestAgentRunOrchestration**

Replace the full `_mock_agent` method in `TestAgentRunOrchestration` with:

```python
    def _mock_agent(self, planner_subtasks, navigator_results, summary="Final answer"):
        """Build an Agent with mocked planner, navigator, and browser.

        Phase 1 is disabled (flat_budget=0) so existing tests exercise Phase 2 only.
        A Phase 1 stall result is prepended to navigator_results automatically.
        """
        agent = Agent.__new__(Agent)
        agent._max_steps = 25
        agent._flat_budget = 0

        # Mock planner
        agent._planner = MagicMock()
        agent._planner.decompose.return_value = planner_subtasks
        agent._planner.summarise.return_value = summary
        agent._planner.replan.return_value = None

        # Mock navigator — prepend Phase 1 stall result
        phase1_stall = NavigatorResult("max_steps", "", 0, [], "https://example.com")
        agent._navigator = MagicMock()
        agent._navigator.execute.side_effect = [phase1_stall] + list(navigator_results)

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
```

- [ ] **Step 2: Fix assertion counts affected by Phase 1 extra call**

Three tests have hardcoded counts or indices that need +1 for the Phase 1 call:

**`test_step_budget_rolls_over`** — call_args_list indices shift from [0],[1] to [1],[2]:

```python
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

        # [0] is Phase 1 stall, [1] and [2] are Phase 2 subtasks
        calls = agent._navigator.execute.call_args_list
        first_budget = calls[1].kwargs.get("max_steps") or calls[1][1].get("max_steps", 0)
        second_budget = calls[2].kwargs.get("max_steps") or calls[2][1].get("max_steps", 0)
        assert second_budget >= first_budget  # Rolled-over budget
```

**`test_replan_replaces_remaining_subtasks`** — execute call_count from 3 to 4:

```python
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
        assert agent._navigator.execute.call_count == 4  # 1 Phase 1 + 3 Phase 2
```

- [ ] **Step 3: Update `_mock_agent_with_escalation` in TestAgentEscalation**

Same pattern. Add `agent._flat_budget = 0` and prepend Phase 1 stall to nav_results. Replace the method:

```python
    def _mock_agent_with_escalation(self, subtasks, nav_results, replan_results=None):
        """Build an Agent with a real EscalationChain plus mocked planner/navigator."""
        from fantoma.resilience.escalation import EscalationChain

        agent = Agent.__new__(Agent)
        agent._max_steps = 25
        agent._flat_budget = 0
        agent._sensitive_data = {}

        agent._planner = MagicMock()
        agent._planner.decompose.return_value = subtasks
        agent._planner.summarise.return_value = "Final answer"
        if replan_results is None:
            agent._planner.replan.return_value = None
        else:
            agent._planner.replan.side_effect = replan_results
        agent._planner._llm = MagicMock()

        # Prepend Phase 1 stall result
        phase1_stall = NavigatorResult("max_steps", "", 0, [], "https://example.com")
        agent._navigator = MagicMock()
        agent._navigator.execute.side_effect = [phase1_stall] + list(nav_results)

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
        agent.escalation = EscalationChain(
            endpoints=["http://localhost:8081/v1", "https://openrouter.ai/api/v1"],
            api_keys=["", "sk-or-test"],
            models=["auto", "qwen/qwen3.6-plus"],
        )
        return agent
```

- [ ] **Step 4: Fix escalation test assertion counts**

**`test_escalation_fires_when_replans_exhausted`** — execute call_count from 2 to 3:

```python
    def test_escalation_fires_when_replans_exhausted(self):
        """When planner.replan returns None, agent escalates and re-decomposes."""
        original = [Subtask("Search broken thing", "interact", "Results visible")]
        nav_results = [
            NavigatorResult("stagnant", "Stopped: action_cycle", 5, [], "https://example.com",
                            failure_reason="action_cycle", last_actions=[]),
            NavigatorResult("done", "Found via direct URL", 3, [], "https://example.com/results"),
        ]
        agent = self._mock_agent_with_escalation(original, nav_results)
        agent._planner.decompose.side_effect = [
            original,
            [Subtask("Navigate to direct results URL", "find", "Page loaded")],
        ]

        result = agent.run("Find results", start_url="https://example.com")

        assert agent.escalation.total_escalations == 1
        assert agent.escalation.current_endpoint() == "https://openrouter.ai/api/v1"
        assert agent._planner.decompose.call_count == 2
        assert agent._navigator.execute.call_count == 3  # 1 Phase 1 + 2 Phase 2
        assert result.success is True
```

- [ ] **Step 5: Run all tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py -v`

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add tests/test_agent_orchestrator.py
git commit -m "test: update existing orchestration tests for two-phase agent flow"
```

---

### Task 6: Add `flat_budget` to BenchmarkConfig

The benchmark worker passes `max_steps` to Agent. It should also pass `flat_budget` so benchmark runs can configure it.

**Files:**
- Modify: `benchmark/config.py`
- Modify: `benchmark/worker.py`

- [ ] **Step 1: Read current config and worker**

Run: `cd /home/workspace/workbench/fantoma && cat benchmark/config.py`

- [ ] **Step 2: Add `flat_budget` field to BenchmarkConfig**

In `benchmark/config.py`, add to the BenchmarkConfig dataclass:

```python
    flat_budget: int = 20
```

- [ ] **Step 3: Pass `flat_budget` to Agent in worker.py**

In `benchmark/worker.py`, find the `agent_kwargs` dict (around line 81) and add:

```python
        "flat_budget": config.flat_budget,
```

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add benchmark/config.py benchmark/worker.py
git commit -m "feat: add flat_budget to BenchmarkConfig and worker"
```

---

### Task 7: Smoke test and verify

- [ ] **Step 1: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --tb=short`

Expected: ALL PASS

- [ ] **Step 2: Copy code to Docker container**

```bash
docker cp /home/workspace/workbench/fantoma/fantoma/. fantoma-browser:/app/fantoma/
docker cp /home/workspace/workbench/fantoma/benchmark/. fantoma-browser:/app/benchmark/
docker exec fantoma-browser find /app -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
```

- [ ] **Step 3: Run a single benchmark task to verify**

```bash
cd /home/workspace/workbench/fantoma
BENCHMARK_LLM_API_KEY="$KIMI_API_KEY" BENCHMARK_LLM_URL="https://api.moonshot.ai/v1" BENCHMARK_LLM_MODEL="kimi-k2-turbo-preview" ./benchmark/run_docker.sh --task "Allrecipes--0" --workers 1
```

Expected: Task runs with Phase 1 flat loop visible in logs ("Phase 1 ended" message if it stalls, or direct completion if Phase 1 succeeds).

- [ ] **Step 4: Commit final state**

```bash
cd /home/workspace/workbench/fantoma
git add -A
git commit -m "feat: flat-first agent with hierarchical fallback (v0.9)"
```
