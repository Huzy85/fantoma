# fantoma/state_tracker.py
"""DOM fingerprinting and stagnation detection for the navigator loop."""

import hashlib
import re
from collections import deque


class StateTracker:
    """Tracks page state changes to detect stagnation, action cycles, and scroll loops.

    Used by the navigator to know when to give up on a subtask and
    return control to the planner for re-planning.
    """

    def __init__(self, window: int = 6):
        self.fingerprints: deque[str] = deque(maxlen=window)
        self.action_norms: deque[str] = deque(maxlen=window)
        self._scroll_count: int = 0
        self._scroll_url: str = ""
        self._scroll_stale: int = 0  # scrolls where DOM didn't change
        self._last_fp: str = ""

    def add(self, url: str, content: str, action_str: str) -> None:
        """Record a step. Call after every action."""
        fp = hashlib.md5(f"{url}|{content[:800]}".encode()).hexdigest()
        self.fingerprints.append(fp)

        norm = re.sub(r"\{'element_id':\s*\d+\}", "{ID}", action_str)
        norm = re.sub(r"\s*->\s*(OK|FAILED|ERROR)", "", norm)
        self.action_norms.append(norm.strip())

        if "scroll(" in action_str:
            if url == self._scroll_url:
                self._scroll_count += 1
                # Track stale scrolls (DOM unchanged after scroll)
                if fp == self._last_fp:
                    self._scroll_stale += 1
                else:
                    self._scroll_stale = 0
            else:
                self._scroll_count = 1
                self._scroll_stale = 0
                self._scroll_url = url
        else:
            self._scroll_count = 0
            self._scroll_stale = 0
        self._last_fp = fp

    def is_stagnant(self) -> bool:
        """DOM fingerprint unchanged for 3 consecutive steps."""
        return len(self.fingerprints) >= 3 and len(set(list(self.fingerprints)[-3:])) == 1

    def is_cycling(self) -> bool:
        """Last 4 normalised actions have <= 2 unique values."""
        if len(self.action_norms) < 4:
            return False
        last4 = list(self.action_norms)[-4:]
        return len(set(last4)) <= 2

    def scroll_limit_hit(self) -> bool:
        """Scroll limit: 2+ stale scrolls (DOM unchanged after scroll) OR 5+ total scrolls on same URL."""
        return self._scroll_stale >= 2 or self._scroll_count >= 5

    def should_stop(self) -> tuple[bool, str]:
        """Check all conditions. Returns (should_stop, reason)."""
        if self.scroll_limit_hit():
            return True, "scroll_limit"
        if self.is_cycling():
            return True, "action_cycle"
        if self.is_stagnant():
            return True, "dom_stagnant"
        return False, ""

    def reset(self) -> None:
        """Clear state for a new subtask."""
        self.fingerprints.clear()
        self.action_norms.clear()
        self._scroll_count = 0
        self._scroll_url = ""
        self._scroll_stale = 0
        self._last_fp = ""
