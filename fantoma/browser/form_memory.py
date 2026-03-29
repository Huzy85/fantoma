"""Form Memory — SQLite database that records what Fantoma sees on login pages.

Two purposes:
1. Matching fallback — when hardcoded labels don't match, check past visits.
2. Debug snapshots — full accessibility tree saved at every step.

The live page is always the boss. The database is a hint, not a replacement.
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger("fantoma.form_memory")

_DEFAULT_DB_PATH = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "fantoma", "form_memory.db"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    domain TEXT PRIMARY KEY,
    last_seen TEXT NOT NULL,
    total_attempts INTEGER NOT NULL DEFAULT 0,
    total_successes INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS form_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    field_label TEXT NOT NULL,
    field_role TEXT NOT NULL DEFAULT '',
    field_purpose TEXT NOT NULL,
    submit_label TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL DEFAULT 0,
    seen_count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(domain, step_number, field_label, field_purpose)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    visit_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    tree_text TEXT NOT NULL DEFAULT '',
    elements_json TEXT NOT NULL DEFAULT '',
    action_taken TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT ''
);
"""


class FormMemory:
    def __init__(self, db_path=None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def lookup(self, domain, step_number, elements):
        """Return {label: purpose} for fields that exist on the live page."""
        cursor = self._conn.execute(
            "SELECT field_label, field_purpose, seen_count FROM form_steps "
            "WHERE domain = ? AND step_number = ? AND success = 1 "
            "ORDER BY seen_count DESC",
            (domain, step_number)
        )
        rows = cursor.fetchall()
        if not rows:
            return {}

        live_labels = {e.get("label", "") for e in elements}
        result = {}
        for row in rows:
            if row["field_label"] in live_labels:
                result[row["field_label"]] = row["field_purpose"]
        return result

    def record_step(self, domain, visit_id, step_number, field_label,
                    field_role, field_purpose, submit_label, success,
                    tree_text, elements_json, url, action, result):
        """Record a form step — upserts form_steps, inserts snapshot."""
        now = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            "INSERT INTO form_steps "
            "(domain, step_number, field_label, field_role, field_purpose, "
            " submit_label, success, seen_count, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?) "
            "ON CONFLICT(domain, step_number, field_label, field_purpose) DO UPDATE SET "
            "seen_count = seen_count + 1, last_seen = ?, success = ?",
            (domain, step_number, field_label, field_role, field_purpose,
             submit_label, int(success), now, now, now, int(success))
        )

        self._conn.execute(
            "INSERT INTO snapshots "
            "(domain, visit_id, step_number, timestamp, url, tree_text, "
            " elements_json, action_taken, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (domain, visit_id, step_number, now, url, tree_text,
             elements_json, action, result)
        )
        self._conn.commit()

    def record_visit(self, domain, success):
        """Update the sites table."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO sites (domain, last_seen, total_attempts, total_successes) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(domain) DO UPDATE SET "
            "last_seen = ?, total_attempts = total_attempts + 1, "
            "total_successes = total_successes + ?",
            (domain, now, int(success), now, int(success))
        )
        self._conn.commit()

    def get_history(self, domain):
        """Return all form_steps and site stats for a domain."""
        site = self._conn.execute(
            "SELECT * FROM sites WHERE domain = ?", (domain,)
        ).fetchone()
        steps = self._conn.execute(
            "SELECT * FROM form_steps WHERE domain = ? ORDER BY step_number",
            (domain,)
        ).fetchall()
        return {
            "total_attempts": site["total_attempts"] if site else 0,
            "total_successes": site["total_successes"] if site else 0,
            "steps": [dict(s) for s in steps],
        }

    def get_snapshot(self, domain, visit_id=None):
        """Return snapshots for debugging."""
        if visit_id:
            rows = self._conn.execute(
                "SELECT * FROM snapshots WHERE domain = ? AND visit_id = ? "
                "ORDER BY step_number",
                (domain, visit_id)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM snapshots WHERE domain = ? ORDER BY timestamp DESC LIMIT 20",
                (domain,)
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self._conn.close()
        except Exception:
            pass
