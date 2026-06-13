# fantoma/dom/aria_diff.py
"""ARIA-level snapshot diffing for the navigator change_line.

Replaces the MutationObserver approach with semantic-level diffs the LLM
already understands, since it reads the ARIA tree every step.

Usage in navigator:
    before = aria_snapshot(page)
    # ... execute action ...
    after  = aria_snapshot(page)
    change_line = aria_diff(before, after) or "No visible changes"
"""

import re
import logging

log = logging.getLogger("fantoma.aria_diff")

# Roles worth tracking — skip structural noise (text, generic, paragraph)
_TRACKED = {
    "button", "link", "textbox", "checkbox", "radio", "combobox",
    "listbox", "option", "menuitem", "tab", "alert", "status",
    "progressbar", "dialog", "img", "searchbox", "spinbutton",
}


def _parse_line(line: str) -> tuple[str, str, str] | None:
    """
    Parse one aria_snapshot() line into (role, name, value).
    Returns None for non-tracked or unrecognised lines.

    Playwright format examples:
      - button "Submit"
      - textbox "Email" [value="user@example.com"]
      - checkbox "Remember me" [checked]
      - alert "Error: required field"
    """
    stripped = re.sub(r'^\s*-\s*', '', line).strip()
    if not stripped:
        return None

    m = re.match(r'^(\w+)(?:\s+"([^"]*)")?(.*)$', stripped)
    if not m:
        return None

    role = m.group(1).lower()
    if role not in _TRACKED:
        return None

    name = m.group(2) or ""
    rest = m.group(3).strip()

    # Extract value from [value="..."] or [checked] / [disabled]
    vm = re.search(r'value="([^"]*)"', rest)
    if vm:
        value = vm.group(1)
    elif "[checked]" in rest:
        value = "checked"
    elif "[disabled]" in rest:
        value = "disabled"
    else:
        value = ""

    return role, name, value


def aria_snapshot(page) -> dict[tuple[str, str], str]:
    """
    Return {(role, name): value} for all tracked interactive elements.
    Empty dict on any error (safe fallback).
    """
    try:
        raw = page.locator("body").aria_snapshot()
    except Exception:
        return {}

    result: dict[tuple[str, str], str] = {}
    for line in raw.split("\n"):
        parsed = _parse_line(line)
        if parsed:
            role, name, value = parsed
            key = (role, name)
            # Keep first occurrence (topmost in tree)
            if key not in result:
                result[key] = value
    return result


def aria_diff(before: dict, after: dict) -> str:
    """
    Compact diff between two aria_snapshot() dicts.
    Returns empty string when there are no meaningful changes.
    Capped at 8 lines to keep the navigator prompt tight.
    """
    lines: list[str] = []

    added_keys   = set(after) - set(before)
    removed_keys = set(before) - set(after)
    common_keys  = set(before) & set(after)

    for role, name in sorted(added_keys)[:4]:
        label = f'"{name}"' if name else ""
        val   = f' = "{after[(role, name)]}"' if after[(role, name)] else ""
        lines.append(f"+ [{role}] {label}{val}".strip())

    for role, name in sorted(removed_keys)[:4]:
        label = f'"{name}"' if name else ""
        lines.append(f"- [{role}] {label}".strip())

    for role, name in sorted(common_keys):
        bv = before[(role, name)]
        av = after[(role, name)]
        if bv != av:
            label = f'"{name}"' if name else ""
            lines.append(f'~ [{role}] {label}: "{bv}" → "{av}"')
            if len(lines) >= 8:
                break

    return "\n".join(lines[:8])
