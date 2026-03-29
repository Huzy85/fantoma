"""Script cache — save and replay successful action sequences.

After a successful agent.run(), saves the action sequence keyed by domain +
page structure. On repeat visits, replays without LLM calls. Falls back to
LLM if replay diverges from expectations.

Storage: SQLite at ~/.local/share/fantoma/script_cache.db
Cache key: domain + sorted (role, name) tuples from initial page elements
Match: fuzzy overlap >80% (sites change minor elements between visits)
"""
import json
import logging
import os
import sqlite3

log = logging.getLogger("fantoma.script_cache")

_DEFAULT_DB = os.path.join(os.path.expanduser("~"), ".local", "share", "fantoma", "script_cache.db")
_MAX_STEPS = 20
_OVERLAP_THRESHOLD = 0.80


class ScriptCache:
    """SQLite-backed cache for action sequences."""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or _DEFAULT_DB
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                element_key TEXT NOT NULL,
                actions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(domain, element_key)
            )
        """)
        self._conn.commit()

    def save(self, domain: str, elements: list[dict], actions: list[dict],
             sensitive_data: dict = None) -> bool:
        """Save an action sequence for a domain + page structure.

        Args:
            domain: Site domain
            elements: Initial page elements (list of dicts with role, name)
            actions: Ordered list of action dicts
            sensitive_data: If provided, real values are replaced with <secret:KEY> placeholders

        Returns:
            True if saved, False if rejected (too long, etc.)
        """
        if len(actions) > _MAX_STEPS:
            log.debug("Rejecting cache entry: %d steps > max %d", len(actions), _MAX_STEPS)
            return False

        if sensitive_data:
            actions = _mask_secrets(actions, sensitive_data)

        element_key = _make_key(elements)
        actions_json = json.dumps(actions)

        self._conn.execute(
            "INSERT OR REPLACE INTO scripts (domain, element_key, actions) VALUES (?, ?, ?)",
            (domain, element_key, actions_json),
        )
        self._conn.commit()
        log.info("Cached %d-step script for %s", len(actions), domain)
        return True

    def lookup(self, domain: str, elements: list[dict]) -> list[dict] | None:
        """Find a cached script for the given domain and page structure.

        Uses fuzzy matching: finds the entry with highest element overlap
        above the threshold (80%).

        Returns:
            List of action dicts, or None if no match.
        """
        cursor = self._conn.execute(
            "SELECT element_key, actions FROM scripts WHERE domain = ?",
            (domain,),
        )
        rows = cursor.fetchall()
        if not rows:
            return None

        current_set = _element_set(elements)
        if not current_set:
            return None

        best_match = None
        best_overlap = 0.0

        for element_key, actions_json in rows:
            cached_set = {tuple(pair) for pair in json.loads(element_key)}
            # Overlap: intersection / max(len) — Jaccard-like
            intersection = len(current_set & cached_set)
            denominator = max(len(current_set), len(cached_set))
            if denominator == 0:
                continue
            overlap = intersection / denominator
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = actions_json

        if best_overlap >= _OVERLAP_THRESHOLD and best_match:
            actions = json.loads(best_match)
            log.info("Cache hit for %s (%.0f%% overlap, %d steps)",
                     domain, best_overlap * 100, len(actions))
            return actions

        return None

    def close(self):
        self._conn.close()


def _make_key(elements: list[dict]) -> str:
    """Create a sorted JSON key from element (role, name) tuples."""
    tuples = sorted((el.get("role", ""), el.get("name", "")) for el in elements)
    return json.dumps(tuples)


def _element_set(elements: list[dict]) -> set:
    """Create a set of (role, name) tuples for overlap comparison."""
    return {(el.get("role", ""), el.get("name", "")) for el in elements}


def _mask_secrets(actions: list[dict], secrets: dict) -> list[dict]:
    """Replace real credential values with <secret:KEY> placeholders."""
    masked = []
    for action in actions:
        a = dict(action)
        text = a.get("action", "")
        for key, value in secrets.items():
            if value and value in text:
                text = text.replace(value, f"<secret:{key}>")
        a["action"] = text
        masked.append(a)
    return masked
