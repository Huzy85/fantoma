# fantoma/planner.py
"""Task decomposition and re-planning for the hierarchical agent."""

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("fantoma.planner")


@dataclass
class Subtask:
    instruction: str
    mode: str       # "interact" | "read" | "find"
    done_when: str


@dataclass
class Checkpoint:
    url: str
    subtask: Subtask
    result_summary: str


DECOMPOSE_SYSTEM = """\
You break web tasks into 2-5 concrete steps.
For each step, provide:
- instruction: what to do (name specific elements, URLs, search terms)
- mode: "interact" (forms, buttons), "read" (extract info), "find" (locate something)
- done_when: how to verify completion

Rules:
- Be specific. "Click the search box and type 'quantum computing'" not "search for it".
- If the task asks to extract information, the last step should be mode "read".
- If you need to search, specify the search term explicitly.
- PREFER direct URLs over clicking through menus. Use search URL patterns:
  - ArXiv: https://arxiv.org/search/?query=TERM&searchtype=all
  - Google: https://www.google.com/search?q=TERM
  - GitHub: https://github.com/search?q=TERM&type=repositories
  - Wikipedia: https://en.wikipedia.org/wiki/TERM
  - Amazon: https://www.amazon.com/s?k=TERM
  - For other sites: check if the current URL reveals a search pattern, then use it.
- The first step should navigate directly to the relevant page when possible.
- Return a numbered list, one step per line, in this format:
  1. instruction: ... | mode: ... | done_when: ..."""

REPLAN_ADDITION = """\
The previous approach failed on this step: {failed_instruction}
Failure reason: {failure_reason}
Last actions: {last_actions}
Completed so far: {completed_summary}
Previously visited URLs (avoid revisiting as a dead-end): {visited_urls}
Current page: {page_summary}

{failure_guidance}

Previously failed strategies: {failed_strategies}"""

# Guidance per failure type -- tells the LLM what specifically to do differently
_FAILURE_GUIDANCE = {
    "scroll_limit": (
        "The agent scrolled 3+ times without finding new content. "
        "You MUST try a completely different approach:\n"
        "- Use the site's search functionality with specific terms\n"
        "- Navigate directly to a known URL pattern\n"
        "- Try a different page or section entirely"
    ),
    "action_cycle": (
        "The agent kept repeating the same 1-2 actions in a loop. "
        "The current approach is fundamentally broken. You MUST:\n"
        "- Try clicking different elements or using keyboard navigation\n"
        "- Navigate to a different URL entirely\n"
        "- Simplify the goal and extract partial information"
    ),
    "dom_stagnant": (
        "The page stopped responding to actions (DOM unchanged for 3 steps). "
        "The page may be broken or require different interaction:\n"
        "- Go BACK and try a different navigation path\n"
        "- Navigate directly to a URL\n"
        "- Try the task on a different section of the site"
    ),
    "rate_limit": (
        "The site is rate-limiting or blocking requests. Do NOT retry the same approach. "
        "Options:\n"
        "- Extract whatever partial data is already available\n"
        "- Try a different search query or URL to avoid the rate limit\n"
        "- Simplify the goal and accept partial results"
    ),
    "login_wall": (
        "The site requires login to continue. Since we cannot log in, you MUST:\n"
        "- Extract whatever data is visible without login\n"
        "- Try accessing the content via a different URL or search\n"
        "- Accept partial results from what was already gathered"
    ),
    "captcha": (
        "A CAPTCHA challenge appeared. Try to work around it:\n"
        "- Navigate to a different page that might not trigger CAPTCHA\n"
        "- Use a direct URL instead of search\n"
        "- Extract whatever data is already available"
    ),
    "domain_drift": (
        "Navigation drifted to a different domain. Go back to the original site:\n"
        "- Navigate directly to the original site URL\n"
        "- Use a different approach that stays on the target domain"
    ),
    "llm_empty": (
        "The previous LLM was unable to produce valid actions for this step. "
        "The prompt or DOM may have confused it. You MUST:\n"
        "- Break the step into smaller, more explicit instructions\n"
        "- Prefer NAVIGATE with a direct URL over CLICK chains\n"
        "- Use shorter, simpler done_when criteria"
    ),
}

_DEFAULT_GUIDANCE = (
    "You MUST try a completely different strategy. Options:\n"
    "- Navigate directly to a URL instead of clicking through menus\n"
    "- Use search functionality instead of browsing categories\n"
    "- Simplify the goal -- extract partial information and move on\n"
    "- Try a different section of the site"
)

SUMMARISE_SYSTEM = """\
You are producing the final answer to a web task from data gathered across multiple pages.

Rules:
- Give the ACTUAL answer with concrete data: names, numbers, dates, URLs, titles.
- NEVER give instructions like "to find X, visit Y" or "you would need to". The user wants the answer, not directions.
- If you have partial data, present exactly what is in the data. Partial answers score better than no answer.
- Report every relevant value that appears in the gathered data. Paraphrase naturally when that reads better than a raw quote.
- Do not invent values from general knowledge that are not in the gathered data. If an asked-for value is genuinely absent from the data, state which value is missing — never fall back to "not found" when the data contains the answer.
- Do not merge observed data with assumed information. Concrete details in your answer must be traceable to the gathered data below.
- If data is contradictory, pick the most specific/recent.
- Keep it concise. One paragraph max."""

_VALID_MODES = {"interact", "read", "find"}


class Planner:
    MODE_MAP = {"find": "navigate", "interact": "form", "read": "content"}

    def __init__(self, llm):
        self._llm = llm
        self._replan_count = 0
        self._max_replans = 3
        self._failed_strategies: list[str] = []

    def reset(self) -> None:
        """Reset replan state for a new task."""
        self._replan_count = 0
        self._failed_strategies.clear()

    def decompose(self, task: str, page_summary: str) -> list[Subtask]:
        messages = [
            {"role": "system", "content": DECOMPOSE_SYSTEM},
            {"role": "user", "content": f"Task: {task}\n\nCurrent page:\n{page_summary}"},
        ]
        raw = self._llm.chat(messages, max_tokens=500)
        subtasks = _parse_subtasks(raw)
        if not subtasks:
            subtasks = [Subtask(instruction=task, mode="find", done_when="Task is complete")]
        return subtasks[:5]

    def replan(self, task: str, completed: list, failed: "Subtask", page_summary: str,
               failure_reason: str = "", last_actions: list = None,
               visited_urls: list = None) -> list["Subtask"] | None:
        self._replan_count += 1
        if self._replan_count > self._max_replans:
            return None

        self._failed_strategies.append(failed.instruction)
        completed_summary = "; ".join(s.instruction for s, _ in completed) if completed else "Nothing completed yet"
        guidance = _FAILURE_GUIDANCE.get(failure_reason, _DEFAULT_GUIDANCE)
        actions_str = "; ".join(last_actions) if last_actions else "none"
        # Cap the visited-urls list so the prompt doesn't balloon on long runs.
        urls_str = "; ".join(visited_urls[-12:]) if visited_urls else "none"

        addition = REPLAN_ADDITION.format(
            failed_instruction=failed.instruction,
            failure_reason=failure_reason or "unknown",
            last_actions=actions_str,
            completed_summary=completed_summary,
            visited_urls=urls_str,
            page_summary=page_summary,
            failure_guidance=guidance,
            failed_strategies="; ".join(self._failed_strategies),
        )

        messages = [
            {"role": "system", "content": DECOMPOSE_SYSTEM + "\n\n" + addition},
            {"role": "user", "content": f"Task: {task}\n\nCurrent page:\n{page_summary}"},
        ]
        raw = self._llm.chat(messages, max_tokens=500)
        subtasks = _parse_subtasks(raw)
        if not subtasks:
            return None
        return subtasks[:5]

    def summarise(self, task: str, completed: list) -> str:
        gathered = []
        for subtask, result in completed:
            gathered.append(f"Step: {subtask.instruction}\nResult: {result.data}")
        all_data = "\n\n".join(gathered)

        messages = [
            {"role": "system", "content": SUMMARISE_SYSTEM},
            {"role": "user", "content": f"Task: {task}\n\nData gathered:\n{all_data}"},
        ]
        return self._llm.chat(messages, max_tokens=1000) or ""


def _parse_subtasks(raw: str) -> list[Subtask]:
    """Parse numbered subtask lines from LLM output."""
    if not raw:
        return []
    subtasks = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Strip leading number: "1. " or "1) "
        line = re.sub(r"^\d+[\.\)]\s*", "", line)

        instruction = ""
        mode = "find"
        done_when = ""

        # Parse pipe-separated fields
        parts = line.split("|")
        for part in parts:
            part = part.strip()
            lower = part.lower()
            if lower.startswith("instruction:"):
                instruction = part.split(":", 1)[1].strip()
            elif lower.startswith("mode:"):
                m = part.split(":", 1)[1].strip().lower()
                if m in _VALID_MODES:
                    mode = m
            elif lower.startswith("done_when:"):
                done_when = part.split(":", 1)[1].strip()

        # Fallback: if no pipe format, treat entire line as instruction
        if not instruction:
            instruction = line
            done_when = "Step complete"

        if instruction:
            subtasks.append(Subtask(instruction=instruction, mode=mode, done_when=done_when))
    return subtasks
