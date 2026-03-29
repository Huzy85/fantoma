"""Step executor — orchestrates browser actions, DOM extraction, and resilience."""
import logging
import re
from typing import Any

from fantoma.browser.engine import BrowserEngine
from fantoma.browser.actions import wait_for_navigation, wait_for_network_idle
from fantoma.browser.consent import dismiss_consent
from fantoma.browser.form_assist import after_type as form_after_type
from fantoma.action_parser import normalize_action, execute_action, parse_actions
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
                 config: FantomaConfig, escalation: EscalationChain = None,
                 sensitive_data: dict = None):
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
        self._env_level = 1
        self._step_history: list[str] = []
        self._compacted_memory: str = ""
        self._compact_threshold = 30
        self._compact_keep_recent = 6
        self._secrets = sensitive_data or {}

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

        _login_attempted = False

        for step_num in range(1, self.config.resilience.max_steps + 1):
            dismiss_consent(page, timeout=self.config.timeouts.consent_dismiss)
            self.captcha.handle(page, self.browser.screenshot)

            dom_text = self.dom.extract(page)
            dom_hash = self.memory.hash_dom(dom_text)

            # Code-assisted form fill: if task involves login/signup and page
            # has a form, use the code path first (faster, more reliable)
            if not _login_attempted and step_num <= 3:
                if self._task_wants_login(task, dom_text):
                    log.info("Step %d: detected login/signup form — using code path", step_num)
                    filled = self._try_code_form_fill(task, page)
                    if filled:
                        _login_attempted = True
                        steps_detail.append({"step": step_num, "action": "CODE_FORM_FILL", "success": True, "url": self.browser.get_url()})
                        # Re-read page after filling
                        dom_text = self.dom.extract(page)
                        dom_hash = self.memory.hash_dom(dom_text)
                        continue

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
                elif self._try_env_escalation():
                    # Don't clear history — same LLM will repeat the same loop.
                    # Next detection (5 more identical actions) will force DONE.
                    self._consecutive_failures = 0
                    continue
                else:
                    log.info("Action loop detected, no escalation available — forcing DONE")
                    loop_data = self._extract_result(task, dom_text)
                    return AgentResult(
                        success=bool(loop_data),
                        data=loop_data,
                        steps_taken=step_num, steps_detail=steps_detail,
                        escalations=self.escalation.total_escalations,
                    )

            # Ask LLM for next action(s)
            failed = self.memory.get_failed_actions(dom_hash)
            user_msg = f"Task: {task}\n\n{dom_text}"
            if self._compacted_memory:
                user_msg += f"\n\n[Previous progress (unverified summary):\n{self._compacted_memory}]"
            if self._step_history:
                recent = "\n".join(f"  {s}" for s in self._step_history[-self._compact_keep_recent:])
                user_msg += f"\n\nRecent steps:\n{recent}"
            if self._secrets:
                secret_list = ", ".join(f"<secret:{k}>" for k in self._secrets.keys())
                user_msg += f"\n\nAvailable secrets: {secret_list}"
            if failed:
                user_msg += f"\n\nFailed (don't repeat): {', '.join(failed)}"

            raw = self.llm.chat(
                [{"role": "system", "content": REACTIVE_SYSTEM},
                 {"role": "user", "content": user_msg}],
                max_tokens=300,
            )
            raw = (raw or "").strip()
            if not raw:
                log.warning("Step %d: LLM returned empty action", step_num)
                continue

            actions_batch = parse_actions(raw, max_actions=5)
            if not actions_batch:
                log.warning("Step %d: no valid actions parsed from LLM response", step_num)
                continue

            # Inject secrets into actions before execution
            if self._secrets:
                actions_batch = [self._inject_secrets(a, self._secrets) for a in actions_batch]

            log.info("Step %d: %d action(s) — %s", step_num, len(actions_batch),
                     ", ".join(self._filter_secrets(a, self._secrets)[:40] if self._secrets else a[:40] for a in actions_batch))

            pre_batch_url = self.browser.get_url()
            done_signalled = False

            for action in actions_batch:
                action_verb = action.strip().split()[0].upper() if action.strip() else ""

                # DONE signal
                if action_verb == "DONE":
                    log.info("LLM signalled DONE after %d steps", step_num)
                    done_signalled = True
                    break

                # Execute the action
                before = self.diff.snapshot(page)
                self._total_actions += 1
                executed = execute_action(action, self.browser, self.dom)

                # SCROLL/WAIT: auto-succeed, no page change check, continue batch
                if action_verb in ("SCROLL", "WAIT"):
                    self.memory.record(action, dom_hash, "success", True, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                    self._consecutive_failures = 0
                    continue

                # TYPE: check if it actually worked, then handle form assist, continue batch
                if action_verb == "TYPE":
                    if executed:
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
                        self._maybe_escalate()
                    continue

                # NAVIGATE: wait for load, then terminate batch (page changed)
                if action_verb == "NAVIGATE":
                    wait_for_navigation(self.browser, timeout=10000)
                    self.memory.record(action, dom_hash, "navigated", True, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                    self._consecutive_failures = 0
                    break

                if not executed:
                    self.memory.record(action, dom_hash, dom_hash, False, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": False, "url": self.browser.get_url()})
                    log.warning("Step %d: action failed to execute", step_num)
                    self._consecutive_failures += 1
                    self._maybe_escalate()
                    continue

                # CLICK and others: check page change
                changed = self._check_page_change(page, before, dom_hash, action, step_num)
                steps_detail.append({"step": step_num, "action": action, "success": changed, "url": self.browser.get_url()})

                if changed:
                    log.info("Step %d: page changed after %s", step_num, action_verb)
                    self._consecutive_failures = 0
                    # If URL changed, stop executing further batch actions
                    post_action_url = self.browser.get_url()
                    if action_verb == "CLICK" and post_action_url != pre_batch_url:
                        log.info("Step %d: URL changed — stopping batch", step_num)
                        break
                else:
                    log.info("Step %d: no visible change", step_num)
                    self._consecutive_failures += 1
                    self._maybe_escalate()

            # Record step for history tracking
            if actions_batch:
                step_summary = actions_batch[0][:60]
                if self._secrets:
                    step_summary = self._filter_secrets(step_summary, self._secrets)
                self._step_history.append(step_summary)
                self._compact_history()

            if done_signalled:
                return AgentResult(
                    success=True, data=self._extract_result(task, dom_text),
                    steps_taken=step_num, steps_detail=steps_detail,
                    escalations=self.escalation.total_escalations,
                )

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
            self.llm = LLMClient(base_url=new_endpoint, api_key=self.escalation.current_api_key())

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
             {"role": "user", "content": f"Task: {task}\n\nPage:\n{full_text}"}],
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

    def _try_env_escalation(self):
        """Try environment-level escalation when model escalation didn't help.

        Level 1: Normal (current behaviour)
        Level 2: Clear cookies + rotate proxy
        Level 3: Fresh browser fingerprint
        """
        max_levels = self.config.resilience.retry_levels
        if self._env_level >= max_levels:
            log.info("All %d environment escalation levels exhausted", max_levels)
            return False

        self._env_level += 1
        log.info("Environment escalation → level %d", self._env_level)

        if self._env_level == 2:
            self.browser.clear_cookies()
            log.info("Level 2: Cleared cookies")
            return True

        if self._env_level == 3:
            current_url = self.browser.get_url()
            self.browser.restart_with_new_fingerprint()
            if current_url:
                self.browser.navigate(current_url)
            log.info("Level 3: Fresh fingerprint, re-navigated to %s", current_url)
            return True

        return False

    def _compact_history(self):
        """Summarize old step history when it gets too long.
        Keeps the last N steps verbatim, summarizes the rest via one LLM call.
        Prevents context window overflow on long tasks (50+ steps).
        """
        if len(self._step_history) < self._compact_threshold:
            return

        from fantoma.llm.prompts import COMPACTION_SYSTEM

        old_steps = self._step_history[:-self._compact_keep_recent]
        recent_steps = self._step_history[-self._compact_keep_recent:]

        history_text = "\n".join(f"Step {i+1}: {s}" for i, s in enumerate(old_steps))

        try:
            summary = self.llm.chat(
                [{"role": "system", "content": COMPACTION_SYSTEM},
                 {"role": "user", "content": f"Steps completed so far:\n{history_text}"}],
                max_tokens=300,
            )
            if summary:
                self._compacted_memory = summary.strip()
                self._step_history = recent_steps
                log.info("History compacted: %d steps → summary + %d recent",
                         len(old_steps), len(recent_steps))
        except Exception as e:
            log.warning("History compaction failed: %s", e)

    @staticmethod
    def _inject_secrets(action: str, secrets: dict) -> str:
        """Replace <secret:key> placeholders with real values in an action string."""
        for key, value in secrets.items():
            action = action.replace(f"<secret:{key}>", value)
        return action

    @staticmethod
    def _filter_secrets(text: str, secrets: dict) -> str:
        """Replace real secret values with placeholders in text (for logging/history)."""
        for key, value in secrets.items():
            if value and value in text:
                text = text.replace(value, f"<secret:{key}>")
        return text

    @staticmethod
    def _task_wants_login(task, dom_text):
        """Check if task involves login/signup AND page has a form."""
        task_lower = task.lower()
        has_auth_intent = any(w in task_lower for w in [
            "sign up", "signup", "create account", "register",
            "log in", "login", "sign in", "signin",
        ])
        if not has_auth_intent:
            return False
        # Check if page has form-like fields
        dom_lower = dom_text.lower()
        has_form = any(w in dom_lower for w in [
            "password", "email", "username", "sign in", "log in",
            "create account", "register", "sign up",
        ])
        return has_form

    def _try_code_form_fill(self, task, page):
        """Use form_login code path to fill a login/signup form detected by LLM task."""
        from fantoma.browser.form_login import login as form_login

        # Extract credentials from the task description
        email = ""
        username = ""
        password = ""
        first_name = ""

        # Parse common patterns from task text
        task_text = task

        # Email: look for email-like strings
        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', task_text)
        if email_match:
            email = email_match.group(0)

        # Password: look for "password: X" or "Password: X"
        pw_match = re.search(r'[Pp]assword[:\s]+(\S+)', task_text)
        if pw_match:
            password = pw_match.group(1).rstrip('.,;')

        # Username: look for "username: X" or "Username: X"
        user_match = re.search(r'[Uu]sername[:\s]+(\S+)', task_text)
        if user_match:
            username = user_match.group(1).rstrip('.,;')

        # Name: look for "name: X" or first_name
        name_match = re.search(r'[Nn]ame[:\s]+(\S+)', task_text)
        if name_match:
            first_name = name_match.group(1).rstrip('.,;')

        if not any([email, username, password]):
            return False

        log.info("Code form fill: email=%s, user=%s, pass=%s",
                 email[:3] + "***" if email else "", username, "***" if password else "")

        result = form_login(
            browser=self.browser,
            dom_extractor=self.dom,
            email=email,
            username=username,
            password=password,
            first_name=first_name,
            config=self.config,
        )
        return bool(result.get("fields_filled"))

    def _maybe_escalate(self):
        """Escalate after 3+ consecutive failures — model first, then environment."""
        if self._consecutive_failures < 3:
            return
        if self.escalation.can_escalate():
            new_endpoint = self.escalation.escalate()
            self.llm = LLMClient(base_url=new_endpoint, api_key=self.escalation.current_api_key())
            log.info("Escalated to %s after %d consecutive failures", new_endpoint, self._consecutive_failures)
            self._consecutive_failures = 0
        elif self._try_env_escalation():
            self._consecutive_failures = 0

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
