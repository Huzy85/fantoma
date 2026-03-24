"""High-level browser actions used by the executor."""

import random
import time


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


def type_into(engine, element_or_selector, text: str, clear_first: bool = True):
    """Type text character by character with human delays."""
    page = engine.get_page()
    if isinstance(element_or_selector, str):
        element = page.query_selector(element_or_selector)
    else:
        element = element_or_selector

    if not element:
        return False

    element.click(timeout=5000)

    if clear_first:
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")

    for char in text:
        page.keyboard.type(char)
        if engine.humanizer:
            time.sleep(engine.humanizer.type_char_delay())

    if engine.humanizer:
        engine.humanizer.action_pause()

    return True


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
