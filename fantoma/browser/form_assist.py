"""Form assist — handles autocomplete suggestions and form progression after typing."""
import logging
import time

log = logging.getLogger("fantoma.form_assist")

# Buttons that advance a form — order matters (first match wins)
SUBMIT_PATTERNS = [
    "next", "continue", "submit", "log in", "login", "sign in", "signin",
    "sign up", "signup", "register", "create account", "proceed", "go",
    "send", "confirm", "verify", "ok",
]


def after_type(page, typed_text: str, timeout: float = 2.0) -> str:
    """Called after a successful TYPE action. Handles two things:

    1. Autocomplete suggestions — if a dropdown appeared with matching text, click it.
    2. Form progression — if the typed field is the only visible input and there's
       a clear submit/next button, click it to advance the form.

    Returns:
        "autocomplete" if a suggestion was clicked,
        "submit" if a form button was clicked,
        "" if nothing was done.
    """
    time.sleep(timeout)

    # Step 1: Check for autocomplete suggestions
    if _try_autocomplete(page, typed_text):
        return "autocomplete"

    # Step 2: Check if we should advance the form
    if _try_form_submit(page):
        return "submit"

    return ""


def _try_autocomplete(page, typed_text: str) -> bool:
    """Look for autocomplete dropdown suggestions matching the typed text."""
    try:
        suggestions = page.evaluate('''(typed) => {
            const input = document.activeElement;
            if (!input) return [];

            const inputRect = input.getBoundingClientRect();
            const selectors = [
                '[role="option"]',
                '[role="listbox"] > *',
                'li[class*="suggest"]',
                'li[class*="autocomplete"]',
                'li[class*="result"]',
                'li[class*="option"]',
                'div[class*="suggest"]',
                'div[class*="autocomplete"]',
                'div[class*="dropdown"] li',
                'ul[class*="suggest"] li',
                'ul[class*="dropdown"] li',
            ].join(', ');

            const typedLower = typed.toLowerCase();
            const matches = [];

            document.querySelectorAll(selectors).forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.height === 0 || rect.width === 0) return;
                if (rect.top < inputRect.bottom - 5 || rect.top > inputRect.bottom + 300) return;

                const text = (el.textContent || '').trim().toLowerCase();
                if (!text) return;
                if (text.includes(typedLower) || typedLower.includes(text)) {
                    matches.push(text);
                    el.click();
                }
            });

            return matches;
        }''', typed_text)

        if suggestions:
            log.info("Autocomplete: clicked suggestion matching '%s'", typed_text[:30])
            time.sleep(1)
            return True
    except Exception as e:
        log.debug("Autocomplete check failed: %s", e)

    return False


def _try_form_submit(page) -> bool:
    """If there's exactly one visible text input on the page and a clear
    submit/next button, click the button to advance the form.

    This handles the common pattern: type email → click Next → type password → click Login.
    Only acts when it's unambiguous — one input, one obvious button.
    """
    try:
        form_state = page.evaluate('''() => {
            // Count visible text inputs
            const inputs = Array.from(document.querySelectorAll(
                'input[type="text"], input[type="email"], input[type="password"], ' +
                'input[type="tel"], input:not([type])'
            )).filter(el => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.height > 0 && rect.width > 0 &&
                       style.display !== 'none' && style.visibility !== 'hidden';
            });

            if (inputs.length !== 1) return {inputs: inputs.length, button: null};

            // Find submit-like buttons
            const buttons = Array.from(document.querySelectorAll(
                'button, input[type="submit"], [role="button"]'
            )).filter(el => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.height > 0 && rect.width > 0 &&
                       style.display !== 'none' && style.visibility !== 'hidden';
            });

            // Return button texts for matching
            return {
                inputs: inputs.length,
                buttons: buttons.map(b => ({
                    text: (b.textContent || b.value || '').trim().toLowerCase(),
                    ariaLabel: (b.getAttribute('aria-label') || '').toLowerCase(),
                })),
            };
        }''')

        if not form_state or form_state.get("inputs") != 1:
            return False

        buttons = form_state.get("buttons", [])
        if not buttons:
            return False

        # Find the best submit button
        for pattern in SUBMIT_PATTERNS:
            for btn in buttons:
                text = btn.get("text", "")
                aria = btn.get("ariaLabel", "")
                if pattern == text or pattern == aria:
                    # Exact match — click it
                    # Press Enter — more reliable than clicking the button.
                    # Triggers native form submission which React forms handle correctly.
                    page.keyboard.press("Enter")
                    log.info("Form assist: pressed Enter (form has '%s' button)", pattern)
                    time.sleep(3)
                    return True

    except Exception as e:
        log.debug("Form submit check failed: %s", e)

    return False
