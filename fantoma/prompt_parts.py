"""Pieces shared by every prompt Fantoma builds.

Prompts are assembled in a fixed order so a model server can reuse its prompt
cache from one step to the next: instructions and the action list first,
byte-identical for the whole task, then the task text and its date, then
per-step notes, then the page last. Nothing that changes from step to step
(step counters, URLs, timestamps) belongs in the first part.
"""

import datetime


def today_line(today: "datetime.date | None" = None) -> str:
    """'Today is Wednesday 2026-11-04.' so relative dates ("next Friday") resolve."""
    d = today or datetime.date.today()
    return f"Today is {d.strftime('%A')} {d.isoformat()}."
