"""Step executor — orchestrates browser actions, DOM extraction, and resilience."""
import logging
import re
from typing import Any

from fantoma.browser.engine import BrowserEngine
from fantoma.browser.actions import wait_for_navigation, wait_for_network_idle
from fantoma.browser.consent import dismiss_consent
from fantoma.browser.form_assist import after_type as form_after_type
from fantoma.action_parser import normalize_action, execute_action
from fantoma.dom.diff import PageDiff
from fantoma.llm.client import LLMClient
from fantoma.llm.prompts import ACTION_SELECTOR_SYSTEM, EXTRACTION_SYSTEM
from fantoma.llm.vision import VisionFallback
from fantoma.resilience.memory import ActionMemory
from fantoma.resilience.checkpoint import CheckpointManager
from fantoma.resilience.escalation import EscalationChain
from fantoma.captcha.orchestrator import CaptchaOrchestrator
from fantoma.config import FantomaConfig

log = logging.getLogger("fantoma.executor")


class Executor:
    """Orchestrates browser steps with DOM extraction, LLM action selection, and resilience."""

    def __init__(self, browser: BrowserEngine, llm: LLMClient,
                 config: FantomaConfig, escalation: EscalationChain = None):
        self.browser = browser
        self.llm = llm
        self.config = config
        self.escalation = escalation or EscalationChain()

        # Use accessibility tree by default (cleaner, legally protected)
        from fantoma.dom.accessibility import AccessibilityExtractor
        self.dom = AccessibilityExtractor(
            max_elements=config.extraction.max_elements,
            max_headings=config.extraction.max_headings,
        )
        self.diff = PageDiff()
        self.memory = ActionMemory()
        self.checkpoints = CheckpointManager()
        self.captcha = CaptchaOrchestrator(config)
        self.vision = VisionFallback(llm)

        self._total_actions = 0
        self._completed_steps: list[str] = []
        self._consecutive_failures = 0

    # ── Plan-based execution ──────────────────────────────────────

    def execute_plan(self, steps: list[str], task: str) -> 'AgentResult':
        """Execute a list of planned steps. Returns AgentResult."""
        from fantoma.agent import AgentResult

        steps_detail = []

        for i, step_desc in enumerate(steps):
            step_num = i + 1
            log.info("Step %d/%d: %s", step_num, len(steps), step_desc)

            if self._total_actions >= self.config.resilience.max_steps:
                log.warning("Max steps (%d) reached", self.config.resilience.max_steps)
                return AgentResult(
                    success=False,
                    steps_taken=self._total_actions,
                    steps_detail=steps_detail,
                    error=f"Max steps ({self.config.resilience.max_steps}) reached at step {step_num}",
                    escalations=self.escalation.total_escalations,
                )

            self.captcha.handle(self.browser.get_page(), self.browser.screenshot)

            # Checkpoint before executing
            page = self.browser.get_page()
            dom_text = self.dom.extract(page)
            self.checkpoints.save(
                step=step_num,
                url=self.browser.get_url(),
                dom_snapshot=dom_text,
                cookies=self.browser.get_cookies(),
                completed_steps=list(self._completed_steps),
                action_history=self.memory._history,
            )

            success = self._execute_step(step_desc, step_num)

            steps_detail.append({
                "step": step_num,
                "description": step_desc,
                "success": success,
                "url": self.browser.get_url(),
            })

            if success:
                self._completed_steps.append(step_desc)
                log.info("Step %d succeeded", step_num)
            else:
                log.warning("Step %d failed after retries", step_num)
                if not self._try_backtrack(step_desc, step_num, task):
                    return AgentResult(
                        success=False,
                        steps_taken=self._total_actions,
                        steps_detail=steps_detail,
                        error=f"Failed at step {step_num}: {step_desc}",
                        escalations=self.escalation.total_escalations,
                    )

        page = self.browser.get_page()
        data = self._extract_result(task, self.dom.extract(page))
        return AgentResult(
            success=True, data=data, steps_taken=self._total_actions,
            steps_detail=steps_detail, escalations=self.escalation.total_escalations,
        )

    # ── Single step execution (with retries) ─────────────────────

    def _execute_step(self, step_desc: str, step_num: int, max_retries: int = None) -> bool:
        """Execute a single step with retries. Returns True on success."""
        max_retries = max_retries or self.config.resilience.max_retries

        for attempt in range(max_retries):
            page = self.browser.get_page()
            dom_text = self.dom.extract(page)
            dom_hash = self.memory.hash_dom(dom_text)

            # Context for LLM: what already failed
            failed = self.memory.get_failed_actions(dom_hash)
            failed_str = f"\nAlready tried and FAILED (do NOT repeat): {', '.join(failed)}" if failed else ""

            # Ask LLM to pick an action
            action = self._select_action(step_desc, dom_text, failed_str)
            if not action:
                log.warning("LLM returned no action for step: %s", step_desc)
                continue

            if self.memory.is_blacklisted(action, dom_hash):
                log.warning("Action '%s' is blacklisted — skipping", action)
                continue

            before = self.diff.snapshot(page)

            # Execute
            self._total_actions += 1
            executed = execute_action(action, self.browser, self.dom)

            if not executed:
                self.memory.record(action, dom_hash, dom_hash, False, step_num)
                continue

            # Actions that always succeed (no page change check needed)
            action_verb = action.strip().split()[0].upper() if action.strip() else ""
            if action_verb in ("WAIT", "DONE", "SCROLL", "PRESS"):
                self.memory.record(action, dom_hash, "success", True, step_num)
                return True
            if action_verb == "TYPE":
                self.memory.record(action, dom_hash, "typed", True, step_num)
                return True
            if action_verb == "NAVIGATE":
                wait_for_network_idle(self.browser, timeout=self.config.timeouts.network_idle)
                self.memory.record(action, dom_hash, "navigated", True, step_num)
                return True

            # Wait and check page change
            wait_for_network_idle(self.browser, timeout=self.config.timeouts.network_idle)
            after = self.diff.snapshot(page)
            changed = self.diff.changed(before, after)
            after_hash = self.memory.hash_dom(self.dom.extract(page))
            self.memory.record(action, dom_hash, after_hash, changed, step_num)

            if changed:
                log.info("Page changed after action — step succeeded")
                return True

            # Last attempt: try vision fallback
            if attempt == max_retries - 1:
                log.info("DOM approach failed — trying vision fallback")
                if self._try_vision_fallback(step_desc, step_num):
                    return True

        return False

    # ── Reactive execution (main mode) ───────────────────────────

    def execute_reactive(self, task: str) -> 'AgentResult':
        """Reactive execution — see page, pick ONE action, execute, repeat.

        No upfront planning. The LLM sees the current page and task,
        picks one action, we execute it, repeat until DONE or max_steps.
        """
        from fantoma.agent import AgentResult
        from fantoma.llm.prompts import REACTIVE_SYSTEM

        steps_detail = []
        page = self.browser.get_page()

        for step_num in range(1, self.config.resilience.max_steps + 1):
            dismiss_consent(page)
            self.captcha.handle(page, self.browser.screenshot)

            dom_text = self.dom.extract(page)
            dom_hash = self.memory.hash_dom(dom_text)

            # Code-based answer detection (solves "small models don't say DONE")
            if step_num >= 2 and self._page_likely_has_answer(task, page):
                log.info("Step %d: answer detected on page — extracting", step_num)
                return AgentResult(
                    success=True, data=self._extract_result(task, dom_text),
                    steps_taken=step_num, steps_detail=steps_detail,
                    escalations=self.escalation.total_escalations,
                )

            # Detect action loops (5x same action)
            if self._is_action_loop():
                if self.escalation.can_escalate():
                    new_endpoint = self.escalation.escalate()
                    self.llm = LLMClient(base_url=new_endpoint, api_key=self.escalation.current_api_key())
                    log.info("Action loop detected — escalated to %s", new_endpoint)
                    self.memory._history.clear()  # clear loop history so new model starts fresh
                    self._consecutive_failures = 0
                    continue
                else:
                    log.info("Action loop detected, no escalation available — forcing DONE")
                    return AgentResult(
                        success=bool(self._extract_result(task, dom_text)),
                        data=self._extract_result(task, dom_text),
                        steps_taken=step_num, steps_detail=steps_detail,
                        escalations=self.escalation.total_escalations,
                    )

            # Ask LLM for next action
            failed = self.memory.get_failed_actions(dom_hash)
            user_msg = f"Task: {task}\n\n{dom_text}"
            if failed:
                user_msg += f"\n\nFailed (don't repeat): {', '.join(failed)}"

            raw = self.llm.chat(
                [{"role": "system", "content": REACTIVE_SYSTEM},
                 {"role": "user", "content": user_msg}],
                max_tokens=50,
            )
            raw = (raw or "").strip()
            if not raw:
                log.warning("Step %d: LLM returned empty action", step_num)
                continue

            action = normalize_action(raw, user_msg)
            log.info("Step %d: %s", step_num, action[:80])

            # DONE signal
            if action.upper().startswith("DONE"):
                log.info("LLM signalled DONE after %d steps", step_num)
                return AgentResult(
                    success=True, data=self._extract_result(task, dom_text),
                    steps_taken=step_num, steps_detail=steps_detail,
                    escalations=self.escalation.total_escalations,
                )

            # Execute the action
            before = self.diff.snapshot(page)
            self._total_actions += 1
            executed = execute_action(action, self.browser, self.dom)
            action_verb = action.strip().split()[0].upper() if action.strip() else ""

            # SCROLL/WAIT: auto-succeed, no page change check
            if action_verb in ("SCROLL", "WAIT"):
                self.memory.record(action, dom_hash, "success", True, step_num)
                steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                self._consecutive_failures = 0
                continue

            # TYPE: check if it actually worked, then handle form assist
            if action_verb == "TYPE":
                if executed:
                    # Extract typed text for autocomplete matching
                    type_re = re.match(r'TYPE\s*\[?\d+\]?\s*["\'](.+?)["\']', action, re.IGNORECASE)
                    typed_text = type_re.group(1) if type_re else ""
                    assist = form_after_type(page, typed_text, timeout=self.config.timeouts.autocomplete)
                    if assist:
                        log.info("Step %d: form assist — %s", step_num, assist)
                    self.memory.record(action, dom_hash, "typed", True, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                    self._consecutive_failures = 0
                else:
                    self.memory.record(action, dom_hash, dom_hash, False, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": False, "url": self.browser.get_url()})
                    log.warning("Step %d: TYPE failed — element not found or not typeable", step_num)
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= 3 and self.escalation.can_escalate():
                        new_endpoint = self.escalation.escalate()
                        self.llm = LLMClient(base_url=new_endpoint, api_key=self.escalation.current_api_key())
                        log.info("Escalated to %s after %d consecutive failures", new_endpoint, self._consecutive_failures)
                        self._consecutive_failures = 0
                continue

            # NAVIGATE: wait for load
            if action_verb == "NAVIGATE":
                wait_for_navigation(self.browser, timeout=10000)
                self.memory.record(action, dom_hash, "navigated", True, step_num)
                steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                self._consecutive_failures = 0
                continue

            if not executed:
                self.memory.record(action, dom_hash, dom_hash, False, step_num)
                steps_detail.append({"step": step_num, "action": action, "success": False, "url": self.browser.get_url()})
                log.warning("Step %d: action failed to execute", step_num)
                self._consecutive_failures += 1
                if self._consecutive_failures >= 3 and self.escalation.can_escalate():
                    new_endpoint = self.escalation.escalate()
                    self.llm = LLMClient(base_url=new_endpoint, api_key=self.escalation.current_api_key())
                    log.info("Escalated to %s after %d consecutive failures", new_endpoint, self._consecutive_failures)
                    self._consecutive_failures = 0
                continue

            # CLICK and others: check page change
            changed = self._check_page_change(page, before, dom_hash, action, step_num)
            steps_detail.append({"step": step_num, "action": action, "success": changed, "url": self.browser.get_url()})

            if changed:
                log.info("Step %d: page changed", step_num)
                self._consecutive_failures = 0
            else:
                log.info("Step %d: no visible change", step_num)
                self._consecutive_failures += 1
                if self._consecutive_failures >= 3 and self.escalation.can_escalate():
                    new_endpoint = self.escalation.escalate()
                    self.llm = LLMClient(base_url=new_endpoint, api_key=self.escalation.current_api_key())
                    log.info("Escalated to %s after %d consecutive failures", new_endpoint, self._consecutive_failures)
                    self._consecutive_failures = 0

        # Hit max steps
        dom_text = self.dom.extract(page)
        data = self._extract_result(task, dom_text)
        return AgentResult(
            success=bool(data), data=data, steps_taken=self._total_actions,
            steps_detail=steps_detail, error="Max steps reached" if not data else "",
            escalations=self.escalation.total_escalations,
        )

    # ── Session mode entry points ────────────────────────────────

    def execute_single_step(self, instruction: str) -> bool:
        """Execute a single instruction (for session mode)."""
        return self._execute_step(instruction, step_num=self._total_actions + 1)

    def extract_data(self, query: str) -> str:
        """Extract data from current page (for session mode)."""
        page = self.browser.get_page()
        dom_text = self.dom.extract(page)
        return self._extract_result(query, dom_text)

    # ── Private helpers ──────────────────────────────────────────

    def _select_action(self, step_desc: str, dom_text: str, failed_context: str) -> str:
        """Ask LLM to pick an action from the DOM elements."""
        user_msg = f"Current step: {step_desc}\n\n{dom_text}{failed_context}\n\nPrevious actions:\n{self.memory.get_history_summary(5)}"
        response = self.llm.chat(
            [{"role": "system", "content": ACTION_SELECTOR_SYSTEM},
             {"role": "user", "content": user_msg}],
        )
        return response.strip() if response else ""

    def _try_vision_fallback(self, step_desc: str, step_num: int) -> bool:
        """Try using vision (screenshot) when DOM approach fails."""
        try:
            screenshot = self.browser.screenshot()
            description = self.vision.describe_page(screenshot)
            if description:
                log.info("Vision sees: %s", description[:100])
                action = self._select_action(step_desc, description, "")
                if action:
                    return execute_action(action, self.browser, self.dom)
        except Exception as e:
            log.debug("Vision fallback failed: %s", e)
        return False

    def _try_backtrack(self, failed_step: str, step_num: int, task: str) -> bool:
        """Roll back to checkpoint and retry, possibly with escalation."""
        checkpoint = self.checkpoints.get_for_step(step_num)
        if not checkpoint:
            log.warning("No checkpoint available for backtracking")
            if self.escalation.can_escalate():
                new_endpoint = self.escalation.escalate()
                self.llm = LLMClient(base_url=new_endpoint, api_key=self.escalation.current_api_key())
                log.info("Escalated to %s — retrying step", new_endpoint)
                return self._execute_step(failed_step, step_num, max_retries=2)
            return False

        log.info("Backtracking to checkpoint at step %d", checkpoint.step)
        rollback_data = self.checkpoints.rollback_to(checkpoint)
        self.browser.navigate(rollback_data["url"])

        if self.escalation.can_escalate():
            new_endpoint = self.escalation.escalate()
            self.llm = LLMClient(base_url=new_endpoint, api_key=self.config.llm.api_key)

        return self._execute_step(failed_step, step_num, max_retries=2)

    def _page_likely_has_answer(self, task: str, page) -> bool:
        """Check if the page likely has the answer using keyword matching (no LLM)."""
        task_lower = task.lower()
        extraction_signals = [
            "tell me", "what is", "what are", "find", "extract",
            "get the", "show me", "list", "name of", "price of",
            "population", "title", "first result", "top",
        ]
        if not any(s in task_lower for s in extraction_signals):
            return False

        try:
            page_text = page.inner_text("body")[:3000].lower()
        except Exception:
            return False

        if page.url.lower() in ("about:blank", "", "data:,"):
            return False

        skip_words = {"tell", "what", "the", "and", "from", "that", "this",
                      "with", "for", "are", "was", "were", "been", "have",
                      "has", "had", "will", "would", "could", "should",
                      "first", "top", "best", "most", "show", "find",
                      "name", "list", "get", "how", "who", "when", "where"}
        words = re.findall(r'\b[a-z]{4,}\b', task_lower)
        keywords = [w for w in words if w not in skip_words]

        if not keywords:
            return False

        matches = sum(1 for kw in keywords if kw in page_text)
        match_ratio = matches / len(keywords)

        if match_ratio >= 0.3:
            log.debug("Answer detection: %.0f%% keywords matched (%s)", match_ratio * 100, keywords[:3])
            return True
        return False

    def _extract_result(self, task: str, dom_text: str) -> str:
        """Extract the requested data from the final page.
        Targets main content area first (skips nav/sidebar noise), falls back to body.
        """
        page = self.browser.get_page()
        max_text = self.config.extraction.max_page_text
        try:
            main = page.locator("main, [role=main]")
            if main.count() > 0:
                full_text = main.first.inner_text()[:max_text]
            else:
                full_text = page.inner_text("body")[:max_text]
        except Exception:
            full_text = dom_text[:max_text]

        response = self.llm.chat(
            [{"role": "system", "content": EXTRACTION_SYSTEM},
             {"role": "user", "content": f"Task: {task}\n\nPage:\n{full_text[:max_text]}"}],
            max_tokens=200,
        )
        return response.strip() if response else ""

    def _is_action_loop(self) -> bool:
        """Detect if the last 5 actions were identical or semantically identical.

        Catches both exact repeats (SCROLL SCROLL SCROLL) and same-intent repeats
        where only the element number differs (TYPE [1] "email", TYPE [4] "email").
        """
        recent = [r.action for r in self.memory._history[-5:]]
        if len(recent) < 5:
            return False
        # Exact match
        if len(set(recent)) == 1:
            return True
        # Semantic match — normalize by stripping element numbers
        normalized = [re.sub(r'\[\d+\]', '[N]', a) for a in recent]
        return len(set(normalized)) == 1

    # _handle_autocomplete removed — replaced by form_assist.after_type()

    def _check_page_change(self, page, before, dom_hash: str, action: str, step_num: int) -> bool:
        """Wait for page to settle and check if it changed after an action."""
        try:
            wait_for_navigation(self.browser, timeout=5000)
        except Exception:
            pass

        try:
            after = self.diff.snapshot(page)
            changed = self.diff.changed(before, after)
            after_hash = self.memory.hash_dom(self.dom.extract(page))
        except Exception as nav_err:
            if "context was destroyed" in str(nav_err) or "navigat" in str(nav_err).lower():
                log.info("Step %d: page navigated (context rebuilt)", step_num)
                self.memory.record(action, dom_hash, "navigated", True, step_num)
                return True
            raise

        self.memory.record(action, dom_hash, after_hash, changed, step_num)
        return changed
