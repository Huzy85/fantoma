"""Fantoma Agent — convenience wrapper for vibe coders.

Provides run() — describe a task in English, the agent does it.
Delegates all browser operations to the Fantoma tool class.
"""
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, urlparse

from fantoma.action_cache import ActionCache
from fantoma.browser_tool import Fantoma
from fantoma.llm.client import LLMClient
from fantoma.resilience.escalation import EscalationChain
from fantoma.planner import Planner, Subtask, Checkpoint
from fantoma.navigator import Navigator, NavigatorResult
from fantoma.state_tracker import StateTracker
from fantoma.validator import validate_answer

log = logging.getLogger("fantoma")


def _dedup_urls(urls: list[str]) -> list[str]:
    """Return the URL trail with consecutive duplicates collapsed.

    Preserves order so the planner can see the real navigation path. Empty
    strings are dropped.
    """
    result = []
    prev = None
    for url in urls:
        if not url:
            continue
        if url != prev:
            result.append(url)
            prev = url
    return result


def _instruction_similar(a: str, b: str) -> bool:
    """Two subtask instructions are similar if their token sets share >60%.

    Used to detect when the planner keeps emitting the same broken subtask
    after replan. The planner cannot see its own history, so the agent loop
    has to enforce "do not repeat the broken approach" itself.
    """
    if not a or not b:
        return False
    norm = lambda s: set(re.findall(r"[a-z0-9]+", s.lower()))
    aw, bw = norm(a), norm(b)
    if not aw or not bw:
        return False
    return len(aw & bw) / max(len(aw), len(bw)) > 0.6


def _google_fallback_subtasks(task: str) -> list[Subtask]:
    """Force a search-and-click-first-result plan when the planner is stuck.

    Used when subtask-cycle detection trips. Side-steps the LLM's apparent
    inability to escape a broken approach by hard-coding the most reliable
    web-task plan: search Google for the task, click the first result, read.
    """
    query = quote_plus(task[:200])
    return [
        Subtask(
            instruction=(
                f"Navigate directly to https://www.google.com/search?q={query} "
                f"and CLICK the first organic search result whose title matches "
                f"the task keywords."
            ),
            mode="find",
            done_when="Landed on a page that is relevant to the task (not a search results page).",
            allow_cross_domain=True,
        ),
        Subtask(
            instruction="Read the page content and extract every value the user asked for.",
            mode="read",
            done_when="The asked-for values are visible in the gathered data.",
            allow_cross_domain=True,
        ),
    ]


_PLACEHOLDER_PREFIXES = (
    "Stopped:",
    "Domain drift",
    "Blocked:",
    "LLM produced no parseable actions",
    "Step budget exhausted",
)


def _has_real_data(data: str | None) -> bool:
    """True if data contains a real answer, not a placeholder status line."""
    return bool(data and not data.startswith(_PLACEHOLDER_PREFIXES))


def _result_has_real_data(result: "NavigatorResult") -> bool:
    """True if a navigator result carries real extracted content.

    Prefers the structured is_placeholder flag over string sniffing; falls
    back to the prefix check for safety. Without this, placeholder status
    lines ("Step budget exhausted", "LLM produced no parseable actions")
    leaked into `completed` and were fed to the summariser as if they were
    real gathered facts.
    """
    if getattr(result, "is_placeholder", False):
        return False
    return _has_real_data(result.data)


def _build_phase1_context(result: NavigatorResult) -> dict:
    """Extract context from Phase 1's NavigatorResult for Phase 2 handover."""
    return {
        "visited_urls": [s.get("url", "") for s in result.steps_detail if s.get("url")],
        "steps_taken": result.steps_taken,
        "partial_data": result.data if _result_has_real_data(result) else None,
        "final_url": result.final_url,
        "failure_reason": result.failure_reason,
        "last_actions": result.last_actions,
    }


@dataclass
class AgentResult:
    """Result of an agent.run() call."""
    success: bool
    data: Any = None
    steps_taken: int = 0
    steps_detail: list = None
    error: str = ""
    tokens_used: int = 0
    escalations: int = 0
    validated: bool = None   # None = not validated, True = passed, False = failed


class Agent:
    """Convenience wrapper — describe a task, the agent does it.

    Usage:
        agent = Agent(llm_url="http://localhost:8080/v1")
        result = agent.run("Go to HN and find the top post about AI")
    """

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
        action_cache: bool = True,
        validate: bool = None,
        **kwargs,
    ):
        self.fantoma = Fantoma(llm_url=llm_url, api_key=api_key, model=model, **kwargs)
        self._max_steps = max_steps
        self._flat_budget = min(flat_budget, max_steps)
        self._sensitive_data = sensitive_data or {}

        endpoints = escalation or [llm_url]
        keys = escalation_keys or [api_key] + [""] * (len(endpoints) - 1)
        models = escalation_models or [model] + ["auto"] * (len(endpoints) - 1)
        self.escalation = EscalationChain(endpoints, keys, models)
        # Tier-0 params, rebuilt fresh each run() so no escalated tier or
        # 400-pinned temperature leaks across runs.
        self._base_llm = {"base_url": llm_url, "api_key": api_key, "model": model}
        self._llm = LLMClient(**self._base_llm)
        self._planner = Planner(self._llm)
        self._navigator = Navigator()
        # Action-trace cache — replay known task plans with zero navigation LLM
        # calls. Env override: FANTOMA_ACTION_CACHE=0 disables globally.
        cache_on = action_cache and os.environ.get("FANTOMA_ACTION_CACHE", "1").lower() not in ("0", "false", "no")
        self._action_cache = ActionCache(enabled=cache_on)
        # Answer validator — opt-in. Env: FANTOMA_VALIDATE=1 enables globally.
        env_validate = os.environ.get("FANTOMA_VALIDATE", "0").lower() in ("1", "true", "yes")
        self._validate = validate if validate is not None else env_validate

    def _apply_validator(self, task: str, result: "AgentResult") -> "AgentResult":
        """If validation is on and the run succeeded, run one LLM check.

        Mutates result.validated in-place and returns it. Fails open.
        """
        if not getattr(self, "_validate", False) or not result.success:
            return result
        passed, reason = validate_answer(task, result.data or "", self._llm)
        result.validated = passed
        if not passed:
            log.warning("Validator: answer did not satisfy task — %s", reason)
        return result

    def _set_llm(self, llm: "LLMClient") -> None:
        """Point the agent, planner, and browser tool at one LLM client.

        The browser tool (extract, form-login labeller) must follow escalation
        too — otherwise it keeps using the weak model that was just deemed
        insufficient.
        """
        self._llm = llm
        self._planner._llm = llm
        try:
            self.fantoma._llm = llm
        except Exception:
            pass

    def _sub_secrets(self, text: str) -> str:
        """Substitute <secret:name> placeholders with real sensitive values."""
        for name, value in self._sensitive_data.items():
            text = text.replace(f"<secret:{name}>", value)
        return text

    def _replay_steps(self, steps: list, start_url: str) -> bool:
        """Replay a cached plan with no navigation LLM calls.

        Returns False on the first unresolved element or failed action, so the
        caller can invalidate the cache and fall back to a full run. A stale
        cache therefore costs one normal run, never a wrong action.
        """
        try:
            if start_url:
                self.fantoma.navigate(start_url)
            for st in steps:
                act = st.get("action")
                if act == "navigate":
                    r = self.fantoma.navigate(st.get("value"))
                elif act == "go_back":
                    r = self.fantoma.go_back()
                elif act == "scroll":
                    r = self.fantoma.scroll(st.get("value") or "down")
                elif act == "press_key":
                    r = self.fantoma.press_key(st.get("value") or "Enter")
                elif act in ("click", "type_text", "select"):
                    self.fantoma.get_state()  # refresh the interactive element list
                    idx = self.fantoma._dom.find_by_signature(st.get("role", ""), st.get("name", ""))
                    if idx is None:
                        return False
                    if act == "click":
                        r = self.fantoma.click(idx)
                    elif act == "type_text":
                        r = self.fantoma.type_text(idx, self._sub_secrets(st.get("value") or ""))
                    else:
                        r = self.fantoma.select(idx, st.get("value") or "")
                else:
                    return False
                if not r.get("success", False):
                    return False
            return True
        except Exception as e:
            log.warning("Replay error: %s", e)
            return False

    def _escalate_llm(self) -> bool:
        """Try escalating to the next tier in the chain.

        Swaps the Agent's LLMClient (and the Planner's reference) to the next
        endpoint/key/model. Returns True if escalation succeeded, False if the
        chain is exhausted.
        """
        if not self.escalation.can_escalate():
            return False
        new_endpoint = self.escalation.escalate()
        if not new_endpoint:
            return False
        new_key = self.escalation.current_api_key()
        new_model = self.escalation.current_model()
        log.info("Escalating LLM to %s (model=%s)", new_endpoint, new_model)
        self._set_llm(LLMClient(base_url=new_endpoint, api_key=new_key, model=new_model))
        # Fresh replan budget on the stronger model
        self._planner.reset()
        return True

    def run(self, task: str, start_url: str = None, deadline_s: float = None) -> AgentResult:
        """Run a browser task described in English.

        Two-phase execution:
        - Phase 1: flat reactive loop with a single catch-all subtask
        - Phase 2: hierarchical planner (only if Phase 1 stalls)

        deadline_s: optional wall-clock budget. The navigator stops and returns
        a "timeout" status once it is exceeded, so a slow LLM can't run for hours
        even within the step budget.
        """
        log.info("Task: %s", task)
        self.fantoma._task = task

        # Reset escalation to tier 0 and rebuild a fresh LLM client for this run,
        # so no escalated (expensive) tier or 400-pinned temperature carries over.
        # (getattr guard tolerates partially-constructed test agents.)
        base = getattr(self, "_base_llm", None)
        if base:
            self.escalation.reset()
            self._set_llm(LLMClient(**base))

        deadline = (time.monotonic() + deadline_s) if deadline_s else None

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

        # Tolerate a partially-constructed Agent (tests build via __new__).
        cache = getattr(self, "_action_cache", None)

        try:
            # ── Action cache: replay a known plan with zero navigation LLM ──
            cached = cache.lookup(start_domain, task) if (cache and start_domain) else None
            if cached:
                log.info("Action cache hit (%d steps) — replaying without the navigation LLM", len(cached))
                if self._replay_steps(cached, start_url):
                    answer = self._navigator._extract_answer(
                        Subtask(task, "read", "Task complete"), self.fantoma, self._llm
                    )
                    cache.mark_used(start_domain, task)
                    return self._apply_validator(task, AgentResult(
                        success=True, data=answer, steps_taken=len(cached),
                        steps_detail=all_steps, escalations=0,
                    ))
                log.info("Replay failed (page changed) — invalidating cache, full run")
                cache.invalidate(start_domain, task)

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
                deadline=deadline,
            )

            all_steps.extend(phase1_result.steps_detail)
            total_steps += phase1_result.steps_taken

            # Phase 1 success: skip Phase 2
            if phase1_result.status == "done" and _result_has_real_data(phase1_result):
                # Cache the successful plan for zero-LLM replay next time.
                if cache and start_domain:
                    rs = phase1_result.replay_steps
                    if rs:
                        cache.record(start_domain, task, rs)
                        log.info("Action cache: recorded %d-step plan for %s", len(rs), start_domain)
                    else:
                        log.info("Action cache: nothing to record (no replayable steps / not cacheable)")
                answer = self._planner.summarise(
                    task, [(flat_subtask, phase1_result)]
                )
                return self._apply_validator(task, AgentResult(
                    success=True,
                    data=answer,
                    steps_taken=total_steps,
                    steps_detail=all_steps,
                    escalations=self.escalation.total_escalations,
                ))

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
                # Cap at the remaining budget so subtasks can't overshoot max_steps.
                step_budget = min(remaining_budget, max(5, remaining_budget // max(1, n_remaining)))
                tracker = StateTracker()

                result = self._navigator.execute(
                    subtask=subtask,
                    fantoma=self.fantoma,
                    llm=self._llm,
                    tracker=tracker,
                    max_steps=step_budget,
                    start_domain=start_domain,
                    sensitive_data=self._sensitive_data,
                    deadline=deadline,
                )

                all_steps.extend(result.steps_detail)
                total_steps += result.steps_taken
                remaining_budget -= result.steps_taken

                has_real_data = _result_has_real_data(result)

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
            return self._apply_validator(task, AgentResult(
                success=bool(completed),
                data=answer,
                steps_taken=total_steps,
                steps_detail=all_steps,
                escalations=self.escalation.total_escalations,
            ))
        except Exception as e:
            return AgentResult(success=False, error=str(e),
                               steps_taken=total_steps,
                               steps_detail=all_steps)
        finally:
            self.fantoma.stop()

    def _get_page_summary(self) -> str:
        """Get a brief page summary for the planner (URL + title + headings + content)."""
        try:
            page = self.fantoma._engine.get_page()
            url = page.url
            title = page.title()
            # Get headings from navigate-mode ARIA (they appear as "(hN) ..." lines)
            aria = self.fantoma._dom.extract(page, mode="navigate")
            headings = [
                line.strip() for line in aria.split("\n")
                if line.strip().startswith("(h")
            ]
            content = self.fantoma._dom.extract_content(page)[:500]
            parts = [f"URL: {url}", f"Title: {title}"]
            if headings:
                parts.append(f"Headings: {'; '.join(headings[:10])}")
            parts.append(f"Content: {content}")
            return "\n".join(parts)
        except Exception:
            return "Page not loaded"

    def login(self, url: str, **creds) -> AgentResult:
        """Log into a site. Delegates to Fantoma."""
        try:
            self.fantoma.start()
            result = self.fantoma.login(url, **creds)
            return AgentResult(
                success=result.get("success", False),
                data=result,
                steps_taken=result.get("steps", 0),
            )
        except Exception as e:
            return AgentResult(success=False, error=str(e))
        finally:
            self.fantoma.stop()

    def extract(self, url: str, query: str, schema: dict = None):
        """Navigate to a URL and extract data. Delegates to Fantoma."""
        try:
            self.fantoma.start(url)
            return self.fantoma.extract(query, schema)
        except Exception as e:
            log.error("Extract failed: %s", e)
            return [] if schema else ""
        finally:
            self.fantoma.stop()

    def session(self, start_url: str):
        """Create a step-by-step session."""
        return _Session(self, start_url)


class _Session:
    """Step-by-step session using Fantoma tool directly."""

    def __init__(self, agent: Agent, start_url: str):
        self.agent = agent
        self.start_url = start_url

    def __enter__(self):
        self.agent.fantoma.start(self.start_url)
        return self

    def __exit__(self, *args):
        self.agent.fantoma.stop()

    def act(self, instruction: str) -> dict:
        """Execute one instruction. Sends to LLM, executes result via Fantoma."""
        from fantoma.navigator import _parse_actions, NAVIGATOR_SYSTEM
        state = self.agent.fantoma.get_state()
        messages = [
            {"role": "system", "content": NAVIGATOR_SYSTEM.format(
                instruction=instruction, done_when="Task complete")},
            {"role": "user", "content": f"Page ({state['url']}):\n{state['aria_tree']}"},
        ]
        raw = self.agent._llm.chat(messages, max_tokens=200)
        actions = _parse_actions(raw or "")
        result = state
        for action_type, params in actions:
            if action_type == "done":
                break
            method = getattr(self.agent.fantoma, action_type)
            r = method(**params)
            result = r.get("state", result)
        return result

    def extract(self, query: str) -> str:
        """Extract info from current page."""
        return self.agent.fantoma.extract(query)

    def new_tab(self, url: str, name: str = None) -> dict:
        return self.agent.fantoma.new_tab(url)

    def switch_tab(self, tab: int | str) -> dict:
        return self.agent.fantoma.switch_tab(tab)

    def close_tab(self, tab: int | str = None) -> dict:
        return self.agent.fantoma.close_tab(tab)
