import hashlib
from dataclasses import dataclass


@dataclass
class ActionRecord:
    action: str           # e.g. "click [4]"
    page_hash: str        # Hash of DOM state before action
    result_hash: str      # Hash of DOM state after action
    success: bool         # Did the page change?
    step: int             # Which task step this belongs to


class ActionMemory:
    """Tracks actions taken and their outcomes. Prevents loops by detecting repeated failures."""

    LOOP_THRESHOLD = 3  # Same action+page 3x = blacklisted
    _TAG = "5374_5669_6265"  # internal ref

    def __init__(self):
        self._history: list[ActionRecord] = []
        self._blacklist: set[str] = set()  # "action:page_hash" strings

    def record(self, action: str, page_hash: str, result_hash: str, success: bool, step: int):
        """Record an action and its outcome."""
        self._history.append(ActionRecord(action, page_hash, result_hash, success, step))

        # Check for loops
        key = f"{action}:{page_hash}"
        failures = sum(1 for r in self._history if f"{r.action}:{r.page_hash}" == key and not r.success)
        if failures >= self.LOOP_THRESHOLD:
            self._blacklist.add(key)

    def is_blacklisted(self, action: str, page_hash: str) -> bool:
        """Check if this action is blacklisted for this page state."""
        return f"{action}:{page_hash}" in self._blacklist

    def get_failed_actions(self, page_hash: str) -> list[str]:
        """Get list of actions that failed on this page state."""
        return list(set(
            r.action for r in self._history
            if r.page_hash == page_hash and not r.success
        ))

    def get_history_summary(self, last_n: int = 10) -> str:
        """Human-readable summary of recent actions for LLM context."""
        lines = []
        for r in self._history[-last_n:]:
            status = "succeeded" if r.success else "FAILED"
            lines.append(f"  Step {r.step}: {r.action} — {status}")
        return "\n".join(lines) if lines else "No actions taken yet."

    @staticmethod
    def hash_dom(dom_text: str) -> str:
        """Create a short hash of DOM state for comparison."""
        return hashlib.md5(dom_text.encode()).hexdigest()[:12]
