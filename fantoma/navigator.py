# fantoma/navigator.py
"""Single-subtask execution loop for the hierarchical agent."""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from fantoma.browser.observer import collect_mutations, format_mutations
from fantoma.browser.page_state import classify_blocker
from fantoma.planner import Planner, Subtask
from fantoma.state_tracker import StateTracker

log = logging.getLogger("fantoma.navigator")

MODE_MAP = Planner.MODE_MAP


@dataclass
class NavigatorResult:
    status: str         # "done" | "stagnant" | "failed" | "max_steps" | "blocked"
    data: str
    steps_taken: int
    steps_detail: list
    final_url: str
    failure_reason: str = ""    # "scroll_limit" | "action_cycle" | "dom_stagnant" | "rate_limit" | "login_wall" | "captcha" | "domain_drift" | "llm_empty"
    last_actions: list = None   # last 5 actions before stop


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
BACK
PRESS Enter
DONE

Rules:
- Match [number] to the element list shown below.
- To fill a form: TYPE each field, then CLICK submit, all in one response.
- After typing in a search field, add PRESS Enter.
- BACK returns to the previous page (use when current page is a dead end).
- NAVIGATE, BACK, and DONE end the sequence.
- Read the Content section first -- if it contains the answer, say DONE immediately.
- Reply with ONLY action lines, nothing else.

Before saying DONE, verify:
- The user's asked-for values have evidence in the current page or in prior data.
- If you typed text into any field during this subtask, you MUST also press
  Enter on it OR click a submit/search button before saying DONE. Typed text
  that is never submitted is incomplete -- the page state has not changed.
- If you are on a Google or Bing search results page and the user wants a
  specific resource (a product page, a course page, an article, a hotel),
  CLICK the first organic result whose title contains your task keywords.
  Never say DONE on a search results page unless the task is to LIST the
  search results themselves.
- If no evidence supports the goal, do NOT say DONE. Take more actions instead.\
"""

EXTRACT_ON_DONE = """\
You are extracting the answer from a web page.

Rules:
- Give the ACTUAL answer with specific data (names, numbers, dates, URLs).
- NEVER give instructions like "to find X, do Y". The answer must contain the data itself.
- If the page contains partial information, extract what is available.
- Address every criterion in the task explicitly.
- If information is not on the page, say exactly what is missing.
- Report every relevant value that appears in the page content below. Titles, prices, dates, specs, pronunciations, descriptions — extract and state them, paraphrasing naturally where that reads better than a raw quote.
- Do not invent values from general knowledge that are not in the page content. If a specific asked-for value is genuinely absent from the page, note which value is missing — never default to "not on page" when the page actually contains the answer.\
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

        if re.match(r'BACK\b', line, re.IGNORECASE):
            results.append(("go_back", {}))
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
        empty_streak = 0  # consecutive empty/unparseable LLM responses

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
            actions = _parse_actions(raw) if raw else []

            # Track consecutive empty/unparseable responses. After 2 in a row,
            # bail out with failure_reason="llm_empty" so the orchestrator
            # can replan or escalate to a stronger model.
            if not actions:
                empty_streak += 1
                log.info("Empty/unparseable LLM response (streak=%d)", empty_streak)
                if empty_streak >= 2:
                    return NavigatorResult(
                        status="failed",
                        data="LLM produced no parseable actions",
                        steps_taken=step_num,
                        steps_detail=steps_detail,
                        final_url=current_url,
                        failure_reason="llm_empty",
                        last_actions=[s["action"] for s in steps_detail[-5:]],
                    )
                continue
            empty_streak = 0

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
                post_action_url = fantoma._engine.get_page().url
                tracker.add(
                    post_action_url,
                    last_content,
                    f"{action_desc} -> {outcome}",
                )

                # Check domain drift immediately after every action, not just
                # at end-of-batch. A bait-click that redirects to a partner
                # site must break the remaining actions in the batch so we
                # don't keep interacting with the wrong domain.
                if self._is_domain_drift(post_action_url, start_domain):
                    log.info("Domain drift detected mid-batch: %s", post_action_url)
                    tail = [s["action"] for s in steps_detail[-5:]]
                    return NavigatorResult(
                        status="failed", data=f"Domain drift to {post_action_url}",
                        steps_taken=step_num, steps_detail=steps_detail,
                        final_url=post_action_url,
                        failure_reason="domain_drift", last_actions=tail,
                    )

                if not result.get("success", False):
                    break

            tail = [s["action"] for s in steps_detail[-5:]]

            # Check for blockers (rate limit, login wall, CAPTCHA)
            try:
                blocker = classify_blocker(fantoma._engine.get_page())
            except Exception:
                blocker = None
            if blocker:
                log.info("Blocker detected: %s (step %d)", blocker, step_num)
                data = self._extract_answer(subtask, fantoma, llm)
                return NavigatorResult(
                    status="blocked", data=data or f"Blocked: {blocker}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=fantoma._engine.get_page().url,
                    failure_reason=blocker, last_actions=tail,
                )

            # Check stagnation
            should_stop, reason = tracker.should_stop()
            if should_stop:
                log.info("Navigator stopping: %s (step %d)", reason, step_num)
                data = self._extract_answer(subtask, fantoma, llm)
                return NavigatorResult(
                    status="stagnant", data=data or f"Stopped: {reason}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=fantoma._engine.get_page().url,
                    failure_reason=reason, last_actions=tail,
                )

            # Check domain drift
            current_url = fantoma._engine.get_page().url
            if self._is_domain_drift(current_url, start_domain):
                log.info("Domain drift detected: %s", current_url)
                return NavigatorResult(
                    status="failed", data=f"Domain drift to {current_url}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=current_url,
                    failure_reason="domain_drift", last_actions=tail,
                )

        # Extract whatever is on the page before giving up
        data = self._extract_answer(subtask, fantoma, llm)
        tail = [s["action"] for s in steps_detail[-5:]]
        return NavigatorResult(
            status="max_steps", data=data or "Step budget exhausted",
            steps_taken=max_steps, steps_detail=steps_detail,
            final_url=fantoma._engine.get_page().url,
            failure_reason="max_steps", last_actions=tail,
        )

    def _extract_answer(self, subtask: Subtask, fantoma, llm) -> str:
        """Extract answer from current page when a subtask reaches a stop state.

        Uses raw body inner_text rather than the ARIA accessibility tree
        because answer extraction needs human-readable page content, not a
        navigation-oriented element list. `_dom.extract_content` frequently
        returned <200 chars of nav links for content pages (Wikipedia,
        Cambridge Dictionary), leaving the LLM with nothing to report.
        """
        try:
            page = fantoma._engine.get_page()
            # Raw text first — this is what the answer-extraction LLM needs.
            try:
                body = page.inner_text("body") or ""
            except Exception:
                body = ""
            title = ""
            try:
                title = page.title() or ""
            except Exception:
                pass
            # Cap at ~12k chars to stay within LLM context while preserving
            # enough of the page for real answer extraction.
            body = body[:12000]
            content = f"Page title: {title}\nURL: {page.url}\n\n{body}"
            log.info(
                "Extract: body=%d chars title=%r url=%s",
                len(body), title[:80], page.url,
            )
            messages = [
                {"role": "system", "content": EXTRACT_ON_DONE},
                {"role": "user", "content": f"Task: {subtask.instruction}\n\nPage content:\n{content}"},
            ]
            answer = llm.chat(messages, max_tokens=1000) or ""
            log.info("Extract: answer=%d chars, preview=%r", len(answer), answer[:150])
            return answer
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
