"""High-level browser actions used by the executor.

type_into uses a unified fill approach inspired by Bitwarden's autofill engine:
one sequence that covers React, Vue, vanilla HTML, and any other framework.
No detection, no branching — just fires everything in one shot.

Backup of previous version: actions.py.backup-20260326
"""

import logging
import random
import time

log = logging.getLogger("fantoma.actions")


def click_element(engine, element_or_selector):
    """Activate an element via focus + keyboard — no mouse events.

    Uses the accessibility interaction model: focus the element, then
    press Enter (links, buttons) or Space (checkboxes, radios). This
    produces zero mouse telemetry. Falls back to JS el.click() if
    keyboard activation doesn't work.
    """
    page = engine.get_page()
    if isinstance(element_or_selector, str):
        element = page.query_selector(element_or_selector)
    else:
        element = element_or_selector

    if not element:
        return False

    # Get element role/type to pick the right key
    tag_and_type = element.evaluate("""el => ({
        tag: el.tagName.toLowerCase(),
        type: (el.getAttribute('type') || '').toLowerCase(),
        role: (el.getAttribute('role') || '').toLowerCase(),
    })""")
    tag = tag_and_type["tag"]
    el_type = tag_and_type["type"]
    role = tag_and_type["role"]

    # Focus the element
    try:
        element.focus()
    except Exception:
        # Some elements reject focus — try JS
        try:
            element.evaluate("el => el.focus()")
        except Exception:
            pass

    is_checkbox = el_type in ("checkbox", "radio") or role in ("checkbox", "radio", "switch")

    # Activate via keyboard
    key = "Space" if is_checkbox else "Enter"
    try:
        page.keyboard.press(key)
    except Exception:
        # Keyboard failed — fall back to JS click (no mouse events)
        try:
            element.evaluate("el => el.click()")
        except Exception:
            return False

    if engine.humanizer:
        engine.humanizer.action_pause()

    return True


def _focus_element(page, element):
    """Focus an input element — no mouse events."""
    try:
        element.focus()
    except Exception:
        try:
            element.evaluate("el => el.focus()")
        except Exception:
            # Focus failed — try dismissing consent overlay first
            from fantoma.browser.consent import dismiss_consent
            dismiss_consent(page)
            time.sleep(0.5)
            try:
                element.focus()
            except Exception:
                return False
    time.sleep(0.2)
    return True


def type_into(engine, element_or_selector, text: str, clear_first: bool = True):
    """Type text into any input element. One sequence that works everywhere.

    Combines techniques from Bitwarden's autofill engine and React issue #11488:
    1. Click + focus the element
    2. Reset React's _valueTracker (if present — costs nothing if not)
    3. Set value via nativeSetter (bypasses React's override, works on vanilla too)
    4. Fire the full event sequence: keydown, keyup, input, change
    5. Verify the value stuck
    6. If it didn't — fall back to keyboard character-by-character

    No framework detection needed. The same sequence handles React, Vue, Angular,
    vanilla HTML, and every other framework because it covers all event paths.
    """
    page = engine.get_page()
    if isinstance(element_or_selector, str):
        element = page.query_selector(element_or_selector)
    else:
        element = element_or_selector

    if not element:
        return False

    if not _focus_element(page, element):
        return False

    # Human-like path: type character by character with realistic key-pair delays.
    # Sites like X detect instant DOM value injection as automation.
    if engine.humanizer:
        # Reset React's _valueTracker first — without this, React controlled
        # inputs silently discard keystrokes during re-renders
        element.evaluate('''(el) => {
            const tracker = el._valueTracker;
            if (tracker) tracker.setValue('');
        }''')

        if clear_first:
            page.keyboard.press("Control+a")
            page.keyboard.press("Backspace")
            time.sleep(0.1)

        prev_char = ""
        for char in text:
            delay = engine.humanizer.type_char_delay(prev_char, char)
            time.sleep(delay)
            page.keyboard.type(char)
            prev_char = char

        # Verify
        try:
            actual = element.input_value()
            if actual == text:
                return True
        except Exception:
            pass

        # If keyboard typing didn't stick (rare), fall through to DOM injection
        log.info("Keyboard typing didn't stick — falling back to DOM injection")

    # Fast path: instant DOM injection via nativeSetter.
    # Used when humanizer is disabled (speed over stealth) or as fallback.
    element.evaluate('''(el, args) => {
        const [text, clear] = args;

        // Focus properly
        el.blur();
        el.focus();

        // Reset React's _valueTracker (harmless on non-React)
        const tracker = el._valueTracker;
        if (tracker) {
            tracker.setValue('');
        }

        // Clear if needed
        if (clear) {
            const proto = Object.getPrototypeOf(el);
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (setter) setter.call(el, '');
            else el.value = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }

        // Set value via native prototype setter (bypasses React override)
        const proto = Object.getPrototypeOf(el);
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) setter.call(el, text);
        else el.value = text;

        // Fire all events
        el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true }));
        el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }''', [text, clear_first])

    # Verify the value stuck
    try:
        actual = element.input_value()
        if actual == text:
            return True
    except Exception:
        pass

    return False


def scroll_page(engine, direction: str = "down", amount: int = None):
    """Scroll via keyboard — no mouse wheel events.

    Uses PageDown/PageUp like a screen reader user would.
    """
    page = engine.get_page()
    key = "PageUp" if direction == "up" else "PageDown"
    page.keyboard.press(key)

    if engine.humanizer:
        time.sleep(random.uniform(*engine.humanizer.scroll_delay))

    return True


def wait_for_navigation(engine, timeout: int = 10000):
    """Wait for page to finish loading."""
    try:
        engine.get_page().wait_for_load_state("domcontentloaded", timeout=timeout)
        return True
    except Exception:
        return False


def wait_for_network_idle(engine, timeout: int = 15000):
    """Wait for network activity to settle — catches SPA transitions (React, Vue, etc).

    Use after actions that trigger navigation or heavy loading (login, form submit).
    Falls back to domcontentloaded if networkidle times out.
    """
    try:
        engine.get_page().wait_for_load_state("networkidle", timeout=timeout)
        return True
    except Exception:
        try:
            engine.get_page().wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        return False


def search_page(page, query: str) -> list[dict]:
    """Find all visible text matches on the page. Like Ctrl+F. Free — no LLM cost."""
    try:
        results = page.evaluate("""(query) => {
            const matches = [];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
            let idx = 0;
            const queryLower = query.toLowerCase();
            while (walker.nextNode()) {
                const text = walker.currentNode.textContent;
                if (text.toLowerCase().includes(queryLower)) {
                    const el = walker.currentNode.parentElement;
                    if (el && el.offsetParent !== null) {
                        const full = el.innerText || text;
                        const pos = full.toLowerCase().indexOf(queryLower);
                        const start = Math.max(0, pos - 30);
                        const end = Math.min(full.length, pos + query.length + 30);
                        matches.push({text: full.substring(start, end).trim(), index: idx});
                        idx++;
                    }
                }
            }
            return matches.slice(0, 20);
        }""", query)
        return results or []
    except Exception as e:
        log.warning("search_page failed: %s", e)
        return []


def find_elements(page, selector: str) -> list[dict]:
    """Query elements by CSS selector. Like browser DevTools. Free — no LLM cost."""
    try:
        results = page.evaluate("""(selector) => {
            const els = document.querySelectorAll(selector);
            return Array.from(els)
                .filter(el => el.offsetParent !== null)
                .slice(0, 20)
                .map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    name: el.getAttribute('name') || '',
                    id: el.getAttribute('id') || '',
                    text: (el.innerText || el.value || '').substring(0, 100).trim(),
                    href: el.getAttribute('href') || '',
                }));
        }""", selector)
        return results or []
    except Exception as e:
        log.warning("find_elements failed: %s", e)
        return []
