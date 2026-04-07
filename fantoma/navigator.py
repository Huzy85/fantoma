# fantoma/navigator.py
"""Single-subtask execution loop for the hierarchical agent."""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from fantoma.browser.observer import collect_mutations, format_mutations
from fantoma.planner import Subtask
from fantoma.state_tracker import StateTracker

log = logging.getLogger("fantoma.navigator")

MODE_MAP = {"find": "navigate", "interact": "form", "read": "content"}


@dataclass
class NavigatorResult:
    status: str         # "done" | "stagnant" | "failed" | "max_steps"
    data: str
    steps_taken: int
    steps_detail: list
    final_url: str


NAVIGATOR_SYSTEM = """\
You control a browser to complete one specific task.

Subtask: {instruction}
Done when: {done_when}

Pick 1-5 actions (one per line):
CLICK [number]
TYPE [number] "text"
SELECT [number] "option"
SCROLL down|up
NAVIGATE https://...
PRESS Enter
DONE

Rules:
- Match [number] to the element list shown below.
- To fill a form: TYPE each field, then CLICK submit, all in one response.
- After typing in a search field, add PRESS Enter.
- NAVIGATE and DONE end the sequence.
- Say DONE only when the done_when condition is met.
- Read the Content section first -- if it contains the answer, say DONE immediately.
- Reply with ONLY action lines, nothing else.\
"""

EXTRACT_ON_DONE = """\
You are extracting the answer from a web page.
Address every criterion in the task explicitly.
Be specific and complete. Include names, numbers, URLs where relevant.\
"""


def _parse_actions(raw: str) -> list[tuple[str, dict]]:
    """Parse LLM response into (action_type, params) tuples."""
    results = []
    for line in (raw or "").strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        m = re.match(r'CLICK\s*\[?(\d+)\]?', line, re.IGNORECASE)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'TYPE\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("type_text", {"element_id": int(m.group(1)), "text": m.group(2)}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'SELECT\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("select", {"element_id": int(m.group(1)), "value": m.group(2)}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'SCROLL\s*(UP|DOWN)', line, re.IGNORECASE)
        if m:
            results.append(("scroll", {"direction": m.group(1).lower()}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'NAVIGATE\s+["\']?(https?://\S+?)["\']?\s*$', line, re.IGNORECASE)
        if m:
            results.append(("navigate", {"url": m.group(1)}))
            break

        m = re.match(r'PRESS\s+(\w+)', line, re.IGNORECASE)
        if m:
            results.append(("press_key", {"key": m.group(1)}))
            if len(results) >= 5:
                break
            continue

        if re.match(r'DONE', line, re.IGNORECASE):
            results.append(("done", {}))
            break

        m = re.search(r'\[(\d+)\]', line)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))
            if len(results) >= 5:
                break

    return results


class Navigator:
    """Executes a single subtask against the browser."""

    def execute(
        self,
        subtask: Subtask,
        fantoma,
        llm,
        tracker: StateTracker,
        max_steps: int = 15,
        start_domain: str = "",
        sensitive_data: dict = None,
    ) -> NavigatorResult:
        steps_detail = []
        sensitive_data = sensitive_data or {}
        dom_mode = MODE_MAP.get(subtask.mode, "navigate")
        change_line = "First step"
        last_content = ""

        for step_num in range(1, max_steps + 1):
            page = fantoma._engine.get_page()
            current_url = page.url

            # Get filtered DOM
            aria = fantoma._dom.extract(page, task=subtask.instruction, mode=dom_mode)
            for name, value in sensitive_data.items():
                aria = aria.replace(value, f"<secret:{name}>")

            # Get page content for state tracking
            try:
                last_content = fantoma._dom.extract_content(page)[:800]
            except Exception:
                last_content = ""

            # Build prompt
            system = NAVIGATOR_SYSTEM.format(
                instruction=subtask.instruction,
                done_when=subtask.done_when,
            )
            user_msg = f"Change: {change_line}\n\nPage ({current_url}):\n{aria}"

            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]

            raw = llm.chat(messages, max_tokens=300)
            if not raw:
                continue

            actions = _parse_actions(raw)
            if not actions:
                continue

            for action_type, params in actions:
                if action_type == "done":
                    data = self._extract_answer(subtask, fantoma, llm)
                    return NavigatorResult(
                        status="done", data=data, steps_taken=step_num,
                        steps_detail=steps_detail, final_url=current_url,
                    )

                for name, value in sensitive_data.items():
                    if "text" in params:
                        params["text"] = params["text"].replace(f"<secret:{name}>", value)

                method = getattr(fantoma, action_type)
                action_desc = f"{action_type}({params})"
                try:
                    result = method(**params)
                    outcome = "OK" if result["success"] else "FAILED"
                except Exception as e:
                    log.warning("Action %s failed: %s", action_desc, e)
                    result = {"success": False}
                    outcome = "ERROR"

                steps_detail.append({
                    "step": step_num, "action": action_desc,
                    "success": result.get("success", False),
                    "url": fantoma._engine.get_page().url,
                })

                # Collect mutations immediately after action
                try:
                    mutations = collect_mutations(fantoma._engine.get_page())
                    change_line = format_mutations(mutations)
                    if not change_line:
                        change_line = "No changes detected"
                except Exception:
                    change_line = "No changes detected"

                # Update state tracker
                tracker.add(
                    fantoma._engine.get_page().url,
                    last_content,
                    f"{action_desc} -> {outcome}",
                )

                if not result.get("success", False):
                    break

            # Check stagnation
            should_stop, reason = tracker.should_stop()
            if should_stop:
                log.info("Navigator stopping: %s (step %d)", reason, step_num)
                return NavigatorResult(
                    status="stagnant", data=f"Stopped: {reason}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=fantoma._engine.get_page().url,
                )

            # Check domain drift
            current_url = fantoma._engine.get_page().url
            if self._is_domain_drift(current_url, start_domain):
                log.info("Domain drift detected: %s", current_url)
                return NavigatorResult(
                    status="failed", data=f"Domain drift to {current_url}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=current_url,
                )

        return NavigatorResult(
            status="max_steps", data="Step budget exhausted",
            steps_taken=max_steps, steps_detail=steps_detail,
            final_url=fantoma._engine.get_page().url,
        )

    def _extract_answer(self, subtask: Subtask, fantoma, llm) -> str:
        """Extract answer from current page when navigator says DONE."""
        try:
            page = fantoma._engine.get_page()
            content = fantoma._dom.extract_content(page)
            messages = [
                {"role": "system", "content": EXTRACT_ON_DONE},
                {"role": "user", "content": f"Task: {subtask.instruction}\n\nPage content:\n{content}"},
            ]
            return llm.chat(messages, max_tokens=1000) or ""
        except Exception as e:
            log.warning("Extract answer failed: %s", e)
            return ""

    @staticmethod
    def _is_domain_drift(current_url: str, start_domain: str) -> bool:
        """Check if current URL has drifted from the expected domain."""
        if not start_domain:
            return False
        try:
            current = urlparse(current_url).netloc.lower()
            start = start_domain.lower()
            # Allow subdomain matching: www.amazon.com matches amazon.com
            return not (current == start or current.endswith("." + start) or start.endswith("." + current))
        except Exception:
            return False
