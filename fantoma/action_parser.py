"""Action parsing and execution — converts LLM text responses into browser actions."""
import logging
import re

from fantoma.browser.actions import click_element, type_into, scroll_page
from fantoma.browser.consent import dismiss_consent

log = logging.getLogger("fantoma.actions")

# Action patterns (case-insensitive, flexible bracket/quote handling)
PATTERNS = {
    "click": re.compile(r'CLICK\s*\[?(\d+)\]?', re.IGNORECASE),
    "type": re.compile(r'TYPE\s*\[?(\d+)\]?\s*["\'](.+?)["\']', re.IGNORECASE),
    "select": re.compile(r'SELECT\s*\[?(\d+)\]?\s*["\'](.+?)["\']', re.IGNORECASE),
    "scroll": re.compile(r'SCROLL\s*(UP|DOWN)', re.IGNORECASE),
    "navigate": re.compile(r'NAVIGATE\s+(?:url\s+)?["\']?((?:https?://)?\S+\.\S+?)["\']?\s*$', re.IGNORECASE),
    "press": re.compile(r'PRESS\s+(\w+)', re.IGNORECASE),
    "wait": re.compile(r'WAIT', re.IGNORECASE),
    "done": re.compile(r'DONE', re.IGNORECASE),
}

# Patterns for extracting actions from verbose LLM responses
EXTRACT_PATTERNS = [
    r'(CLICK\s*\[?\d+\]?)',
    r'(TYPE\s*\[?\d+\]?\s*"[^"]*")',
    r'(NAVIGATE\s+(?:https?://)\S+)',
    r'(SCROLL\s+(?:up|down))',
    r'(PRESS\s+\w+)',
    r'(DONE)',
]


def normalize_action(raw_response: str, task_context: str = "") -> str:
    """Extract a clean action command from any LLM response.

    Handles verbose responses, bracket-only references, and free-form text.
    Returns a normalized action string like 'CLICK [3]' or 'TYPE [0] "hello"'.
    """
    action = (raw_response or "").strip()
    if not action:
        return ""

    # Try to find a clean action command in the text
    for pattern in EXTRACT_PATTERNS:
        m = re.search(pattern, action, re.IGNORECASE)
        if m:
            return m.group(1)

    # Phi-style: "[14] link ..." or "Navigate to [14]" → extract as CLICK [N]
    bracket_match = re.search(r'\[(\d+)\]', action)
    if bracket_match:
        return f"CLICK [{bracket_match.group(1)}]"

    # "Navigate to..." without URL — look for a URL in the task context
    if "navigate" in action.lower() and task_context:
        url_in_task = re.search(r'(https?://\S+)', task_context)
        if url_in_task:
            return f"NAVIGATE {url_in_task.group(1)}"

    return action


def execute_action(action: str, browser, dom_extractor) -> bool:
    """Parse and execute an action string. Returns True if executed successfully.

    Handles: CLICK [N], TYPE [N] "text", SELECT [N] "value", SCROLL up/down,
    NAVIGATE url, PRESS key, WAIT, DONE, and free-form fallbacks.
    """
    action_raw = action.strip() if action else ""
    page = browser.get_page()

    try:
        # CLICK [N]
        m = PATTERNS["click"].match(action_raw)
        if m:
            idx = int(m.group(1))
            element = dom_extractor.get_element_by_index(page, idx)
            if element:
                return _click_with_fallback(browser, page, element)
            log.warning("Element [%d] not found", idx)
            return False

        # TYPE [N] "text"
        m = PATTERNS["type"].match(action_raw)
        if m:
            idx = int(m.group(1))
            text = m.group(2)
            element = dom_extractor.get_element_by_index(page, idx)
            if element:
                return _type_with_fallback(browser, element, text, idx)
            log.warning("Element [%d] not found", idx)
            return False

        # SELECT [N] "value"
        m = PATTERNS["select"].match(action_raw)
        if m:
            idx = int(m.group(1))
            value = m.group(2)
            element = dom_extractor.get_element_by_index(page, idx)
            if element:
                element.select_option(label=value)
                return True
            return False

        # SCROLL up/down
        m = PATTERNS["scroll"].match(action_raw)
        if m:
            return scroll_page(browser, m.group(1).lower())

        # NAVIGATE url
        m = PATTERNS["navigate"].match(action_raw)
        if m:
            url = m.group(1)
            if not url.startswith("http"):
                url = f"https://{url}"
            browser.navigate(url)
            return True

        # PRESS key
        m = PATTERNS["press"].match(action_raw)
        if m:
            page.keyboard.press(m.group(1))
            return True

        # WAIT
        if PATTERNS["wait"].match(action_raw):
            import time
            time.sleep(2)
            return True

        # DONE
        if PATTERNS["done"].match(action_raw):
            return True

        # Free-form fallbacks
        return _handle_freeform(action_raw, page, browser)

    except Exception as e:
        log.error("Action execution error: %s", e)
        return False


def _click_with_fallback(browser, page, element) -> bool:
    """Click with consent dismiss and JS fallbacks."""
    try:
        return click_element(browser, element)
    except Exception as click_err:
        if "intercepts pointer events" in str(click_err) or "Timeout" in str(click_err):
            log.info("Click blocked by overlay — dismissing consent and retrying")
            dismiss_consent(page)
            try:
                return click_element(browser, element)
            except Exception:
                log.info("Falling back to JavaScript click")
                page.evaluate("el => el.click()", element)
                return True
        raise


def _type_with_fallback(browser, element, text: str, idx: int) -> bool:
    """Type with fill() first, fallback to character-by-character."""
    try:
        element.fill(text)
        return True
    except Exception:
        try:
            return type_into(browser, element, text)
        except Exception:
            log.warning("Could not type into element [%d]", idx)
            return False


def _handle_freeform(action_raw: str, page, browser) -> bool:
    """Handle free-form action text that doesn't match standard patterns."""
    lower = action_raw.lower()

    if "enter" in lower or "submit" in lower or "press enter" in lower:
        page.keyboard.press("Enter")
        return True

    if "go back" in lower or "back" in lower:
        page.go_back()
        return True

    if lower.startswith("navigate") and "url" in lower and "http" not in lower:
        log.warning("Model said 'NAVIGATE url' without a real URL — skipping")
        return False

    # Extract URL from free text
    url_in_text = re.search(r'(https?://\S+|www\.\S+|\w+\.(?:com|co\.uk|org|net|io)\S*)', action_raw)
    if url_in_text:
        url = url_in_text.group(1)
        if not url.startswith("http"):
            url = f"https://{url}"
        log.info("Extracted URL from free text: %s", url)
        browser.navigate(url)
        return True

    log.warning("Could not parse action: %s", action_raw[:100])
    return False
