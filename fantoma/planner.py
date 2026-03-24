"""Task planner — breaks natural language tasks into browser steps."""
import logging
import re

from fantoma.llm.client import LLMClient
from fantoma.llm.prompts import PLANNER_SYSTEM

log = logging.getLogger("fantoma.planner")


class Planner:
    """Uses LLM to decompose a task into sequential browser steps."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def plan(self, task: str, start_url: str = None) -> list[str]:
        """Break a task into numbered steps.

        Returns list of step descriptions, e.g.:
        ["Navigate to google.com", "Type 'python tutorials' in search box", "Click Search"]
        """
        user_msg = f"Task: {task}"
        if start_url:
            user_msg += f"\nStarting URL: {start_url}"

        response = self.llm.chat([
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": user_msg},
        ])

        if not response:
            log.error("Planner got empty response from LLM")
            return []

        # Parse numbered steps from response
        steps = self._parse_steps(response)

        if not steps:
            log.warning("Could not parse steps from planner response: %s", response[:200])
            # Fallback: treat the whole response as a single step
            return [response.strip()]

        return steps

    def replan(self, task: str, completed_steps: list[str], failed_step: str,
               error: str, current_url: str) -> list[str]:
        """Re-plan after a failure. Gives LLM context about what worked and what didn't."""
        context = f"""Task: {task}
Current URL: {current_url}

Steps completed successfully:
{chr(10).join(f'  - {s}' for s in completed_steps) or '  (none)'}

Step that failed:
  FAILED: {failed_step}
  Error: {error}

Create a new plan to complete the task from the current state.
Do NOT repeat steps that already succeeded. Start from where we are now."""

        response = self.llm.chat([
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": context},
        ])

        return self._parse_steps(response) if response else []

    @staticmethod
    def _parse_steps(text: str) -> list[str]:
        """Extract numbered steps from LLM response.
        Handles formats like '1. Do X', '1) Do X', 'Step 1: Do X'
        """
        patterns = [
            r'^\s*\d+[\.\)]\s*(.+)$',           # "1. Do X" or "1) Do X"
            r'^\s*Step\s*\d+[:\.]?\s*(.+)$',     # "Step 1: Do X"
            r'^\s*-\s*(.+)$',                     # "- Do X" (bullet list)
        ]

        lines = text.strip().split('\n')
        steps = []

        for line in lines:
            line = line.strip()
            if not line:
                continue
            for pattern in patterns:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    step = match.group(1).strip()
                    if step:
                        steps.append(step)
                    break

        return steps
