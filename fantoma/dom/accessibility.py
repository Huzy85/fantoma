"""Accessibility-based DOM extraction — presents Fantoma as assistive technology.

Uses Playwright's ARIA snapshot instead of raw DOM traversal.
This gives cleaner, more structured data AND sites are legally
required to support it (WCAG 2.1, ADA, Equality Act 2010).

Screen readers interact with pages this way — Fantoma does the same.
"""

import logging
import re
from typing import Optional, Any

log = logging.getLogger("fantoma.accessibility")

# ARIA roles that represent interactive elements
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "searchbox",
    "checkbox", "radio", "slider", "switch", "tab",
    "menuitem", "option", "spinbutton",
}

# Roles to skip (structural, not interactive)
SKIP_ROLES = {
    "separator", "presentation", "none", "generic",
    "paragraph", "group", "list", "listitem",
    "navigation", "main", "banner", "contentinfo",
    "complementary", "region", "article", "section",
    "img",  # Images without interaction
}

# Defaults — overridden by ExtractionConfig when available
MAX_ELEMENTS = 15
MAX_HEADINGS = 25
MAX_CONTENT_ELEMENTS = 30

# Navigation/UI noise — names that indicate chrome, not content
NAV_NOISE = {
    "close", "dismiss", "menu", "toggle", "collapse", "expand",
    "show", "hide", "previous", "next", "back", "forward",
    "notifications", "settings", "preferences", "manage",
    "create a new", "add folder", "add label",
}


def _is_nav_noise(name: str) -> bool:
    """Check if an element name is navigation/UI noise rather than content."""
    name_lower = name.lower()
    return any(noise in name_lower for noise in NAV_NOISE)


def _parse_aria_line(line: str) -> dict | None:
    """Parse one line of ARIA snapshot into a structured dict.

    Examples:
        '- button "Search"' → {"role": "button", "name": "Search"}
        '- combobox "Search with DuckDuckGo"' → {"role": "combobox", "name": "Search with DuckDuckGo"}
        '- heading "Title" [level=1]' → {"role": "heading", "name": "Title", "level": "1"}
        '- checkbox "Agree" [checked]' → {"role": "checkbox", "name": "Agree", "checked": True}
    """
    line = line.strip().lstrip("- ")
    if not line:
        return None

    # Match: role "name" [attributes]
    match = re.match(r'(\w+)\s*"([^"]*)"(?:\s*\[(.+?)\])?', line)
    if not match:
        # Match: role [attributes] (no name)
        match2 = re.match(r'(\w+)(?:\s*\[(.+?)\])?$', line)
        if match2:
            return {"role": match2.group(1), "name": "", "attrs": match2.group(2) or ""}
        return None

    role = match.group(1)
    name = match.group(2)
    attrs_str = match.group(3) or ""

    result = {"role": role, "name": name}

    # Parse attributes: [checked], [disabled], [level=1], [value="text"]
    if attrs_str:
        for attr in attrs_str.split(", "):
            attr = attr.strip()
            if "=" in attr:
                k, v = attr.split("=", 1)
                result[k] = v.strip('"')
            else:
                result[attr] = True

    return result


def extract_aria(page, max_elements: int = None, max_headings: int = None) -> str:
    """Extract page content via ARIA accessibility tree.

    Returns a numbered element map similar to DOMExtractor but using
    ARIA roles and names instead of HTML tags and selectors.

    This is what a screen reader sees — clean, structured, legally protected.
    """
    title = page.title()
    url = page.url

    try:
        snapshot = page.locator("body").aria_snapshot()
    except Exception as e:
        log.warning("ARIA snapshot failed: %s — falling back to DOM", e)
        return ""

    # Parse snapshot into elements
    lines = snapshot.split("\n")
    interactive = []
    headings = []

    for line in lines:
        parsed = _parse_aria_line(line)
        if not parsed:
            continue

        role = parsed["role"]
        name = parsed.get("name", "")

        if role in SKIP_ROLES:
            continue

        if role == "heading" and name:
            level = parsed.get("level", "")
            headings.append(f"  (h{level}) {name}" if level else f"  {name}")
            continue

        if role == "text" and name:
            # Key page text — include if short
            if len(name) < 100:
                headings.append(f"  {name}")
            continue

        if role in INTERACTIVE_ROLES and name:
            state = ""
            if parsed.get("checked"):
                state = " [checked]"
            elif parsed.get("disabled"):
                state = " [disabled]"
            elif parsed.get("value"):
                state = f' (value: "{parsed["value"]}")'

            interactive.append({
                "role": role,
                "name": name,
                "state": state,
                "raw": parsed,
            })

    # Build output
    output = []
    output.append(f"Page: {title}")
    output.append(f"URL: {url}")
    output.append("")

    _max_el = max_elements or MAX_ELEMENTS
    _max_hd = max_headings or MAX_HEADINGS

    if interactive:
        shown = interactive[:_max_el]
        output.append(f"Elements ({len(shown)} of {len(interactive)}):")
        for i, el in enumerate(shown):
            output.append(f'[{i}] {el["role"]} "{el["name"]}"{el["state"]}')
    else:
        output.append("Elements: none found")

    if headings:
        output.append("")
        output.append("Page text:")
        for h in headings[:_max_hd]:
            output.append(h)

    return "\n".join(output)


def extract_aria_content(page) -> str:
    """Extract page content for data extraction — strips navigation UI, keeps content.

    Used when the goal is to READ data from the page (extract emails, products, etc.)
    rather than NAVIGATE it (click buttons, fill forms).

    Differences from extract_aria:
    - Filters out navigation buttons, menus, toolbars
    - Higher caps on headings and text (30 vs 10)
    - Includes all text nodes, not just short ones
    - Groups content by ARIA regions/landmarks when available
    """
    title = page.title()
    url = page.url

    try:
        snapshot = page.locator("body").aria_snapshot()
    except Exception as e:
        log.warning("ARIA content snapshot failed: %s", e)
        return ""

    if not snapshot or len(snapshot.strip()) < 10:
        return ""

    lines = snapshot.split("\n")
    content_items = []
    current_region = None

    for line in lines:
        parsed = _parse_aria_line(line)
        if not parsed:
            # Check for raw region markers in the ARIA snapshot
            stripped = line.strip().lstrip("- ")
            if stripped.startswith("region ") and '"' in stripped:
                region_name = stripped.split('"')[1]
                if not _is_nav_noise(region_name):
                    current_region = region_name
            continue

        role = parsed["role"]
        name = parsed.get("name", "")

        if not name:
            continue

        # Skip structural roles
        if role in SKIP_ROLES:
            continue

        # Skip navigation noise
        if role in INTERACTIVE_ROLES and _is_nav_noise(name):
            continue

        # Include headings (primary content signals)
        if role == "heading":
            level = parsed.get("level", "")
            prefix = f"(h{level}) " if level else ""
            if current_region:
                content_items.append(f"  [{current_region}] {prefix}{name}")
            else:
                content_items.append(f"  {prefix}{name}")
            continue

        # Include text nodes (the actual content)
        if role == "text":
            if current_region:
                content_items.append(f"  [{current_region}] {name}")
            else:
                content_items.append(f"  {name}")
            continue

        # Include links and buttons only if they look like content (not nav)
        if role in ("link", "button") and not _is_nav_noise(name):
            # Content links are typically longer or descriptive
            if len(name) > 15 or role == "link":
                content_items.append(f"  {role}: {name}")

    # Build output — content only, no element numbers (not for clicking)
    output = [f"Page: {title}", f"URL: {url}", "", "Page content:"]

    for item in content_items[:MAX_CONTENT_ELEMENTS]:
        output.append(item)

    if not content_items:
        output.append("  (no content found)")

    return "\n".join(output)


class AccessibilityExtractor:
    """ARIA-based element extraction — screen reader mode.

    Uses Playwright's ARIA snapshot for cleaner, more structured data.
    Falls back to DOMExtractor when ARIA tree is empty.
    """

    def __init__(self, max_elements: int = None, max_headings: int = None):
        self._last_interactive: list[dict] = []
        self._max_elements = max_elements
        self._max_headings = max_headings

    def extract(self, page) -> str:
        """Extract page via ARIA tree. Falls back to DOM if empty."""
        result = extract_aria(page, self._max_elements, self._max_headings)
        if not result or "Elements: none found" in result:
            log.debug("ARIA tree empty — falling back to DOM extraction")
            from fantoma.dom.extractor import DOMExtractor
            fallback = DOMExtractor()
            return fallback.extract(page)

        # Cache interactive elements for get_element_by_index
        self._last_interactive = self._parse_interactive_from_output(result)
        return result

    def extract_content(self, page) -> str:
        """Extract page content only — for data extraction, not navigation.

        Strips navigation UI, buttons, menus. Keeps headings, text, content links.
        Higher caps (30 items vs 10). Groups by ARIA regions when available.
        Falls back to full page inner_text if ARIA content extraction is empty.
        """
        result = extract_aria_content(page)
        if not result or "(no content found)" in result:
            # Fallback: raw page text
            try:
                text = page.inner_text("body")[:4000]
                return f"Page: {page.title()}\nURL: {page.url}\n\nPage content:\n{text}"
            except Exception:
                return ""
        return result

    def get_element_by_index(self, page, index: int) -> Optional[Any]:
        """Find element by ARIA role and name using Playwright locators.

        This is more stable than CSS selectors — ARIA attributes are
        semantically meaningful and sites are required to maintain them.
        """
        if index < 0 or index >= len(self._last_interactive):
            return None

        el = self._last_interactive[index]
        role = el["role"]
        name = el["name"]

        # Use Playwright's role-based locator (the modern, recommended way)
        try:
            locator = page.get_by_role(role, name=name)
            if locator.count() > 0:
                return locator.first.element_handle()
        except Exception:
            pass

        # Fallback: try aria-label
        try:
            element = page.query_selector(f'[aria-label="{name}"]')
            if element:
                return element
        except Exception:
            pass

        # Fallback: text-based
        try:
            locator = page.get_by_text(name, exact=True)
            if locator.count() > 0:
                return locator.first.element_handle()
        except Exception:
            pass

        return None

    @staticmethod
    def _parse_interactive_from_output(output: str) -> list[dict]:
        """Parse the numbered elements from the output string."""
        elements = []
        for line in output.split("\n"):
            match = re.match(r'\[(\d+)\]\s+(\w+)\s+"([^"]*)"', line)
            if match:
                elements.append({
                    "index": int(match.group(1)),
                    "role": match.group(2),
                    "name": match.group(3),
                })
        return elements
