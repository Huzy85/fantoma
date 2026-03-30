"""Iframe ARIA extraction — find and extract elements from iframes.

Enumerates child frames on the page, extracts ARIA snapshots from each,
and returns interactive elements tagged with their source frame.

Payment forms, embedded logins, and consent dialogs live inside iframes.
Without this, Fantoma is blind to them.

Limitations:
- Closed shadow DOM iframes are not accessible (Playwright limitation).
- Cross-origin iframes work in Chromium (CDP level access) but may fail in Firefox.
- Max 5 frames processed by default to avoid slowdowns on ad-heavy pages.
"""
import logging
import re

log = logging.getLogger("fantoma.frames")

# ARIA roles that represent interactive elements (same as accessibility.py)
_INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "searchbox",
    "checkbox", "radio", "slider", "switch", "tab",
    "menuitem", "option", "spinbutton",
}

_MAX_FRAMES = 5
_SKIP_URLS = {"about:blank", "", "data:,", "about:srcdoc"}


def extract_frame_elements(frame) -> list[dict]:
    """Extract interactive elements from a single frame via ARIA snapshot.

    Returns list of element dicts with role, name, state, and _frame (frame name).
    """
    try:
        snapshot = frame.locator("body").aria_snapshot()
    except Exception as e:
        log.debug("Frame ARIA snapshot failed for '%s': %s", frame.name, e)
        return []

    if not snapshot or not snapshot.strip():
        return []

    frame_name = frame.name or frame.url.split("/")[-1][:20]
    elements = []

    for line in snapshot.split("\n"):
        line = line.strip().lstrip("- ")
        if not line:
            continue

        match = re.match(r'(\w+)\s*"([^"]*)"(?:\s*\[(.+?)\])?', line)
        if not match:
            continue

        role = match.group(1)
        name = match.group(2)
        if role not in _INTERACTIVE_ROLES or not name:
            continue

        state = ""
        attrs_str = match.group(3) or ""
        if attrs_str:
            if "checked" in attrs_str:
                state = " [checked]"
            elif "disabled" in attrs_str:
                state = " [disabled]"

        elements.append({
            "role": role,
            "name": name,
            "state": state,
            "raw": {},
            "_frame": frame_name,
        })

    return elements


def collect_all_frame_elements(page, max_frames: int = _MAX_FRAMES) -> list[dict]:
    """Collect interactive elements from all child iframes on the page.

    Skips the main frame (already extracted by AccessibilityExtractor).
    Skips about:blank and empty frames.
    Caps at max_frames to avoid slowdowns on ad-heavy pages.

    Returns list of element dicts tagged with _frame name.
    """
    main_frame = page.main_frame
    elements = []
    frames_processed = 0

    for frame in page.frames:
        if frame == main_frame:
            continue
        if frame.url in _SKIP_URLS:
            continue
        if frames_processed >= max_frames:
            break

        frame_elements = extract_frame_elements(frame)
        if frame_elements:
            elements.extend(frame_elements)
            frames_processed += 1
            log.info("Frame '%s': %d elements", frame.name or frame.url[:30], len(frame_elements))

    return elements
