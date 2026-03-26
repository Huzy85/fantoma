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
    """Click with mouse movement + human delay."""
    page = engine.get_page()
    if isinstance(element_or_selector, str):
        element = page.query_selector(element_or_selector)
    else:
        element = element_or_selector

    if not element:
        return False

    if engine.humanizer and engine.humanizer.should_move_mouse():
        engine.humanizer.move_to_element(page, element)

    element.click(timeout=5000)

    if engine.humanizer:
        engine.humanizer.action_pause()

    return True


def _focus_element(page, element):
    """Click to focus, dismissing overlays if needed."""
    try:
        element.click(timeout=5000)
    except Exception:
        from fantoma.browser.consent import dismiss_consent
        dismiss_consent(page)
        time.sleep(1)
        try:
            element.click(timeout=5000)
        except Exception:
            try:
                page.evaluate("el => el.click()", element)
            except Exception:
                return False
    time.sleep(0.3)
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

    # Unified fill: one JS call that covers every framework
    element.evaluate('''(el, args) => {
        const [text, clear] = args;

        // Step 1: focus properly
        el.blur();
        el.focus();

        // Step 2: reset React's _valueTracker (harmless on non-React)
        const tracker = el._valueTracker;
        if (tracker) {
            tracker.setValue('');
        }

        // Step 3: clear if needed
        if (clear) {
            const proto = Object.getPrototypeOf(el);
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (setter) setter.call(el, '');
            else el.value = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
        }

        // Step 4: simulate typing start
        el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true }));
        el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));

        // Step 5: set value via native prototype setter (bypasses React override)
        const proto = Object.getPrototypeOf(el);
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) setter.call(el, text);
        else el.value = text;

        // Step 6: simulate typing end + fire all events
        el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true }));
        el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }''', [text, clear_first])

    if engine.humanizer:
        engine.humanizer.action_pause()

    # Verify the value stuck
    try:
        actual = element.input_value()
        if actual == text:
            return True
    except Exception:
        pass

    # Fallback: keyboard character by character (real keypresses via Playwright)
    log.info("Unified fill didn't stick — falling back to keyboard.type()")
    if not _focus_element(page, element):
        return False

    if clear_first:
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")

    for char in text:
        page.keyboard.type(char)
        if engine.humanizer:
            time.sleep(engine.humanizer.type_char_delay())

    if engine.humanizer:
        engine.humanizer.action_pause()

    # Final verify
    try:
        actual = element.input_value()
        if actual == text:
            return True
    except Exception:
        pass

    return False


def scroll_page(engine, direction: str = "down", amount: int = None):
    """Scroll with human-like distance variation."""
    page = engine.get_page()
    if amount is None and engine.humanizer:
        amount = engine.humanizer.scroll_distance()
    elif amount is None:
        amount = 300

    if direction == "up":
        amount = -amount

    page.mouse.wheel(0, amount)

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
