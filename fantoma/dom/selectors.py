"""Element targeting utilities — build robust selectors and find elements by text/role."""

from typing import Any, Optional


def build_selector(element_info: dict) -> str:
    """Build a robust CSS selector for an element based on extracted info.

    Priority order:
        1. data-testid (most stable, designed for automation)
        2. id (usually unique)
        3. aria-label (accessible and stable)
        4. unique class + tag combination
        5. tag + nth-child fallback

    Args:
        element_info: Dict with keys like tag, id, dataTestId, ariaLabel,
                      name, type, className, etc.

    Returns:
        CSS selector string.
    """
    # 1. data-testid — best option, explicitly for testing
    test_id = element_info.get("dataTestId") or element_info.get("data-testid", "")
    if test_id:
        return f'[data-testid="{test_id}"]'

    # 2. id — almost always unique
    el_id = element_info.get("id", "")
    if el_id:
        return f"#{_css_escape(el_id)}"

    tag = element_info.get("tag", "*")

    # 3. aria-label — stable and meaningful
    aria = element_info.get("ariaLabel") or element_info.get("aria-label", "")
    if aria:
        return f'{tag}[aria-label="{_escape_attr(aria)}"]'

    # 4. name attribute (common on form fields)
    name = element_info.get("name", "")
    if name:
        return f'{tag}[name="{_escape_attr(name)}"]'

    # 5. placeholder (inputs) — svl
    placeholder = element_info.get("placeholder", "")
    if placeholder:
        return f'{tag}[placeholder="{_escape_attr(placeholder)}"]'

    # 6. type attribute for inputs
    el_type = element_info.get("type", "")
    if tag == "input" and el_type:
        return f'input[type="{el_type}"]'

    # 7. role attribute
    role = element_info.get("role", "")
    if role:
        return f'[role="{role}"]'

    # 8. Bare tag fallback
    return tag


def find_by_text(page, text: str, tag: str = None) -> Optional[Any]:
    """Find an element by its visible text content.

    Args:
        page: Playwright Page object.
        text: Visible text to search for (case-insensitive substring match).
        tag: Optional tag name to restrict the search (e.g., 'button', 'a').

    Returns:
        Playwright ElementHandle or None.
    """
    # Use Playwright's built-in text selector for robustness
    if tag:
        selector = f'{tag}:has-text("{_escape_attr(text)}")'
    else:
        selector = f':has-text("{_escape_attr(text)}")'

    try:
        # get_by_text is more reliable for exact/substring matching
        locator = page.get_by_text(text, exact=False)
        if tag:
            locator = page.locator(tag).filter(has_text=text)
        # Return the first match as an element handle
        if locator.count() > 0:
            return locator.first.element_handle()
    except Exception:
        pass

    # Fallback: try CSS :has-text pseudo-selector
    try:
        el = page.query_selector(selector)
        if el:
            return el
    except Exception:
        pass

    return None


def find_by_role(page, role: str, name: str = None) -> Optional[Any]:
    """Find an element by ARIA role and optional accessible name.

    Args:
        page: Playwright Page object.
        role: ARIA role (e.g., 'button', 'link', 'tab').
        name: Optional accessible name to match.

    Returns:
        Playwright ElementHandle or None.
    """
    try:
        locator = page.get_by_role(role, name=name) if name else page.get_by_role(role)
        if locator.count() > 0:
            return locator.first.element_handle()
    except Exception:
        pass

    # Fallback: CSS attribute selector
    selector = f'[role="{role}"]'
    if name:
        # Try aria-label match
        selector = f'[role="{role}"][aria-label="{_escape_attr(name)}"]'

    try:
        el = page.query_selector(selector)
        if el:
            return el
    except Exception:
        pass

    return None


def _css_escape(value: str) -> str:
    """Escape a string for use as a CSS identifier."""
    # Replace characters that need escaping in CSS selectors
    result = []
    for ch in value:
        if ch.isalnum() or ch in ("-", "_"):
            result.append(ch)
        else:
            result.append(f"\\{ch}")
    return "".join(result)


def _escape_attr(value: str) -> str:
    """Escape a string for use in a CSS attribute value (inside double quotes)."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
