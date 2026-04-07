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
- Return a numbered list, one step per line, in this format:
  1. instruction: ... | mode: ... | done_when: ..."""

REPLAN_ADDITION = """\
The previous approach failed on this step: {failed_instruction}
Completed so far: {completed_summary}
Current page: {page_summary}

You MUST try a completely different strategy. Options:
- Navigate directly to a URL instead of clicking through menus
- Use search functionality instead of browsing categories
- Simplify the goal -- extract partial information and move on
- Try a different section of the site

Previously failed strategies: {failed_strategies}"""

SUMMARISE_SYSTEM = """\
You are extracting the answer to a web task from data gathered across multiple pages.
Address every criterion in the task explicitly.
Be specific and complete -- vague answers will fail evaluation."""

_VALID_MODES = {"interact", "read", "find"}


class Planner:
    MODE_MAP = {"find": "navigate", "interact": "form", "read": "content"}

    def __init__(self, llm):
        self._llm = llm
        self._replan_count = 0
        self._max_replans = 3
        self._failed_strategies: list[str] = []

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

    def replan(self, task: str, completed: list, failed: "Subtask", page_summary: str) -> list["Subtask"] | None:
        self._replan_count += 1
        if self._replan_count > self._max_replans:
            return None

        self._failed_strategies.append(failed.instruction)
        completed_summary = "; ".join(s.instruction for s, _ in completed) if completed else "Nothing completed yet"

        addition = REPLAN_ADDITION.format(
            failed_instruction=failed.instruction,
            completed_summary=completed_summary,
            page_summary=page_summary,
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
