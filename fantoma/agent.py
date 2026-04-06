"""Fantoma Agent — convenience wrapper for vibe coders.

Provides run() — describe a task in English, the agent does it.
Delegates all browser operations to the Fantoma tool class.
"""
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from fantoma.browser_tool import Fantoma
from fantoma.llm.client import LLMClient
from fantoma.resilience.escalation import EscalationChain

log = logging.getLogger("fantoma")


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


# ── LLM prompts (orchestrator concerns) ─────────────────────

REACTIVE_PROMPT = """\
You control a browser. Your job is to COMPLETE the task, not just observe the page.

Pick 1-5 actions from this list (one per line):
CLICK [number]
TYPE [number] "text"
SELECT [number] "option"
SCROLL down
SCROLL up
NAVIGATE https://example.com
PRESS Enter
DONE

Rules:
- Match [number] to the element list shown after the task.
- Elements marked with * are NEW (just appeared from your last action) — focus on these.
- You may return multiple actions (one per line) to execute in sequence.
- To fill a form: TYPE each field, then CLICK submit — all in one response.
- After typing in a search field, add PRESS Enter.
- NAVIGATE and DONE end the sequence — any actions after them are ignored.
- Only say DONE when the task is fully COMPLETED.
- Do NOT say DONE just because you can see a form or page — you must interact with it first.
- If secrets are available, use them with <secret:name> syntax.
- After each action you'll see an outcome. Use this feedback.
- Reply with ONLY action lines, nothing else.\
"""

EXTRACTION_PROMPT = """\
Extract ONLY the answer from the page content below. No code. No explanation. Just the data.\
"""

COMPACTION_PROMPT = """\
Summarize what has been accomplished so far in this browser automation task.
Include: pages visited, forms filled, buttons clicked, data found, errors encountered.
Be specific. Keep it under 200 words.\
"""

# ── Action parsing ───────────────────────────────────────────


def _parse_actions(raw: str) -> list[tuple[str, dict]]:
    """Parse LLM response into (action_type, params) tuples."""
    results = []
    for line in (raw or "").strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # CLICK [N]
        m = re.match(r'CLICK\s*\[?(\d+)\]?', line, re.IGNORECASE)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))
            continue
        # TYPE [N] "text"
        m = re.match(r'TYPE\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("type_text", {"element_id": int(m.group(1)), "text": m.group(2)}))
            continue
        # SELECT [N] "value"
        m = re.match(r'SELECT\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("select", {"element_id": int(m.group(1)), "value": m.group(2)}))
            continue
        # SCROLL
        m = re.match(r'SCROLL\s*(UP|DOWN)', line, re.IGNORECASE)
        if m:
            results.append(("scroll", {"direction": m.group(1).lower()}))
            continue
        # NAVIGATE
        m = re.match(r'NAVIGATE\s+["\']?(https?://\S+?)["\']?\s*$', line, re.IGNORECASE)
        if m:
            results.append(("navigate", {"url": m.group(1)}))
            break  # Terminator
        # PRESS
        m = re.match(r'PRESS\s+(\w+)', line, re.IGNORECASE)
        if m:
            results.append(("press_key", {"key": m.group(1)}))
            continue
        # DONE
        if re.match(r'DONE', line, re.IGNORECASE):
            results.append(("done", {}))
            break  # Terminator

        # Fallback: bare [N] → click
        m = re.search(r'\[(\d+)\]', line)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))

        if len(results) >= 5:
            break

    return results


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
        max_steps: int = 50,
        sensitive_data: dict = None,
        **kwargs,
    ):
        self.fantoma = Fantoma(llm_url=llm_url, api_key=api_key, model=model, **kwargs)
        self._max_steps = max_steps
        self._sensitive_data = sensitive_data or {}

        endpoints = escalation or [llm_url]
        keys = escalation_keys or [api_key] + [""] * (len(endpoints) - 1)
        self.escalation = EscalationChain(endpoints, keys)
        self._llm = LLMClient(base_url=llm_url, api_key=api_key, model=model)

    def run(self, task: str, start_url: str = None) -> AgentResult:
        """Run a browser task described in English."""
        log.info("Task: %s", task)
        history = []
        steps_detail = []
        self.fantoma._task = task

        try:
            state = self.fantoma.start(start_url)
        except Exception as e:
            return AgentResult(success=False, error=f"Browser start failed: {e}")

        # Timeout via threading.Event (safe with Playwright greenlets)
        timeout_event = threading.Event()
        timer = threading.Timer(self.fantoma.config.browser.timeout, timeout_event.set)
        timer.daemon = True
        timer.start()

        try:
            for step_num in range(1, self._max_steps + 1):
                if timeout_event.is_set():
                    return AgentResult(success=bool(steps_detail), data=state.get("aria_tree", ""),
                                       steps_taken=step_num - 1, steps_detail=steps_detail,
                                       error=f"Timeout after {step_num - 1} steps",
                                       escalations=self.escalation.total_escalations)

                # Mask secrets in the ARIA tree
                aria = state["aria_tree"]
                for name, value in self._sensitive_data.items():
                    aria = aria.replace(value, f"<secret:{name}>")

                # Build LLM messages
                messages = [{"role": "system", "content": REACTIVE_PROMPT}]
                if history:
                    messages.append({"role": "assistant", "content": "\n".join(history[-10:])})
                messages.append({"role": "user", "content": f"Task: {task}\n\nPage ({state['url']}):\n{aria}"})

                # Ask LLM
                raw = self._llm.chat(messages, max_tokens=500)
                if not raw:
                    continue

                actions = _parse_actions(raw)
                if not actions:
                    continue

                for action_type, params in actions:
                    if action_type == "done":
                        data = self._extract_answer(task, state)
                        return AgentResult(success=True, data=data, steps_taken=step_num,
                                           steps_detail=steps_detail,
                                           escalations=self.escalation.total_escalations)

                    # Unmask secrets before executing
                    if "text" in params:
                        for name, value in self._sensitive_data.items():
                            params["text"] = params["text"].replace(f"<secret:{name}>", value)

                    # Call the Fantoma tool method
                    method = getattr(self.fantoma, action_type)
                    action_desc = f"{action_type}({params})"
                    try:
                        result = method(**params)
                        state = result.get("state", state)
                        outcome = "OK" if result["success"] else "FAILED"
                    except Exception as action_err:
                        log.warning("Action %s failed: %s", action_desc, action_err)
                        result = {"success": False}
                        outcome = "ERROR"

                    history.append(f"Step {step_num}: {action_desc} → {outcome}")
                    steps_detail.append({"step": step_num, "action": action_desc,
                                         "success": result["success"], "url": state.get("url", "")})

                    if not result["success"]:
                        break  # Let LLM re-evaluate on next iteration

                # Loop detection: last 5 actions identical
                if len(history) >= 5 and len(set(history[-5:])) == 1:
                    if self.escalation.can_escalate():
                        new_ep = self.escalation.escalate()
                        self._llm = LLMClient(base_url=new_ep,
                                               api_key=self.escalation.current_api_key())
                        history.clear()
                    else:
                        return AgentResult(success=False, error="Action loop detected",
                                           steps_taken=step_num, steps_detail=steps_detail,
                                           escalations=self.escalation.total_escalations)

            return AgentResult(success=False, error="Max steps reached",
                               steps_taken=self._max_steps, steps_detail=steps_detail,
                               escalations=self.escalation.total_escalations)
        except Exception as e:
            return AgentResult(success=False, error=str(e),
                               steps_taken=len(steps_detail), steps_detail=steps_detail)
        finally:
            timer.cancel()
            self.fantoma.stop()

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

    def _extract_answer(self, task: str, state: dict) -> str:
        """Try to extract a concise answer from the current page."""
        try:
            messages = [
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": f"Task: {task}\n\nPage content:\n{state['aria_tree'][:4000]}"},
            ]
            return self._llm.chat(messages, max_tokens=1000) or ""
        except Exception:
            return state.get("aria_tree", "")[:2000]


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
        state = self.agent.fantoma.get_state()
        messages = [
            {"role": "system", "content": REACTIVE_PROMPT},
            {"role": "user", "content": f"Task: {instruction}\n\nPage ({state['url']}):\n{state['aria_tree']}"},
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
