"""Action-trace cache — replay successful task plans with zero LLM calls.

Records the sequence of successful actions for a (domain, task) pair as
ARIA-node signatures (role + accessible name + value), so a repeat of the
same task on the same site replays deterministically without calling the LLM.

On replay, if a step's element cannot be resolved (the page changed), replay
aborts and the caller falls back to the normal LLM agent loop, then re-records
the fresh plan. That is the self-healing path: a stale cache costs one normal
run, never a wrong action.

DOM/ARIA-native by design — steps are keyed by (role, name), never by brittle
element indices, CSS, or XPath. This is the biggest single win for weak local
LLMs: a repeated task on a stable site costs zero tokens.
"""

import json
import logging
import os
import sqlite3
import time

log = logging.getLogger("fantoma.action_cache")

DEFAULT_DB = os.path.join(
    os.path.expanduser("~"), ".local", "share", "fantoma", "action_cache.db"
)

# Only deterministic, replayable actions are cached. An answer-extraction or
# DONE step is not replayed — the caller re-extracts at the end.
REPLAYABLE = {"click", "type_text", "select", "press_key", "scroll", "navigate", "go_back"}


def normalize_task(task: str) -> str:
    """Lowercase + collapse whitespace so trivial wording differences hit the same key."""
    return " ".join((task or "").lower().split())


class ActionCache:
    """SQLite-backed store of replayable action plans keyed by (domain, task)."""

    def __init__(self, db_path: str = None, enabled: bool = True):
        self.enabled = enabled
        self.db_path = db_path or DEFAULT_DB
        self._conn = None
        if not enabled:
            return
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS plans (
                    domain TEXT NOT NULL,
                    task TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    last_used REAL NOT NULL,
                    PRIMARY KEY (domain, task)
                )"""
            )
            self._conn.commit()
        except Exception as e:
            log.warning("ActionCache disabled (db init failed): %s", e)
            self.enabled = False
            self._conn = None

    def lookup(self, domain: str, task: str):
        """Return the cached step list for (domain, task), or None."""
        if not self._conn or not domain:
            return None
        try:
            cur = self._conn.execute(
                "SELECT steps FROM plans WHERE domain=? AND task=?",
                (domain, normalize_task(task)),
            )
            row = cur.fetchone()
            if not row:
                return None
            steps = json.loads(row[0])
            return steps or None
        except Exception as e:
            log.warning("ActionCache lookup failed: %s", e)
            return None

    def record(self, domain: str, task: str, steps: list) -> bool:
        """Store a fresh plan. Drops non-replayable steps; no-ops on empty result."""
        if not self._conn or not domain or not steps:
            return False
        clean = [
            {
                "action": s.get("action"),
                "role": s.get("role", ""),
                "name": s.get("name", ""),
                "value": s.get("value"),
            }
            for s in steps
            if s.get("action") in REPLAYABLE
        ]
        if not clean:
            return False
        try:
            now = time.time()
            self._conn.execute(
                "INSERT INTO plans (domain, task, steps, hits, created_at, last_used) "
                "VALUES (?,?,?,0,?,?) "
                "ON CONFLICT(domain, task) DO UPDATE SET steps=excluded.steps, last_used=excluded.last_used",
                (domain, normalize_task(task), json.dumps(clean), now, now),
            )
            self._conn.commit()
            return True
        except Exception as e:
            log.warning("ActionCache record failed: %s", e)
            return False

    def mark_used(self, domain: str, task: str) -> None:
        """Bump the hit counter after a successful replay."""
        if not self._conn or not domain:
            return
        try:
            self._conn.execute(
                "UPDATE plans SET hits=hits+1, last_used=? WHERE domain=? AND task=?",
                (time.time(), domain, normalize_task(task)),
            )
            self._conn.commit()
        except Exception as e:
            log.warning("ActionCache mark_used failed: %s", e)

    def invalidate(self, domain: str, task: str) -> None:
        """Drop a stale plan after a failed replay so the next run re-records."""
        if not self._conn or not domain:
            return
        try:
            self._conn.execute(
                "DELETE FROM plans WHERE domain=? AND task=?",
                (domain, normalize_task(task)),
            )
            self._conn.commit()
        except Exception as e:
            log.warning("ActionCache invalidate failed: %s", e)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
