"""Autocomplete handler — detects and selects dropdown suggestions after typing."""
import logging
import time

log = logging.getLogger("fantoma.autocomplete")


def handle_autocomplete(page, typed_text: str, timeout: float = 3.0) -> bool:
    """After typing, check for autocomplete suggestions and click the best match.

    Returns True if a suggestion was found and clicked, False otherwise.
    This is pure code — no LLM needed.

    Only matches genuine autocomplete/dropdown suggestions — NOT form buttons,
    submit buttons, or navigation elements.
    """
    time.sleep(timeout)  # Wait for suggestions to render

    # Find the active/focused input
    input_rect = page.evaluate('''() => {
        const input = document.activeElement;
        if (!input || (input.tagName !== 'INPUT' && input.tagName !== 'TEXTAREA')) return null;
        const rect = input.getBoundingClientRect();
        return {top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right, width: rect.width};
    }''')

    if not input_rect:
        return False

    # Find autocomplete suggestions near the input
    # IMPORTANT: exclude buttons, submit buttons, nav elements — only match
    # actual dropdown/suggestion items that contain the typed text
    suggestions = page.evaluate('''(params) => {
        const {bottom, left, right} = params.inputRect;
        const typed = params.typedText.toLowerCase();
        const candidates = [];

        // Only look for actual suggestion/option elements — NOT buttons
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
            'datalist option',
        ].join(', ');

        document.querySelectorAll(selectors).forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.height === 0 || rect.width === 0) return;

            // Must be below the input and roughly aligned horizontally
            if (rect.top < bottom - 5) return;
            if (rect.top > bottom + 300) return;
            if (rect.right < left - 50 || rect.left > right + 50) return;

            const text = el.textContent?.trim() || '';
            if (!text) return;

            // Must relate to what was typed — ignore unrelated items
            const textLower = text.toLowerCase();
            const exactMatch = textLower === typed;
            const startsWith = textLower.startsWith(typed);
            const contains = textLower.includes(typed);
            const typedContains = typed.includes(textLower);

            if (!exactMatch && !startsWith && !contains && !typedContains) return;

            candidates.push({
                text: text.substring(0, 100),
                top: rect.top,
                exactMatch: exactMatch,
                startsWith: startsWith,
                contains: contains,
            });
        });

        return candidates;
    }''', {"inputRect": input_rect, "typedText": typed_text})

    if not suggestions:
        log.debug("No autocomplete suggestions found")
        return False

    log.info("Found %d autocomplete suggestions", len(suggestions))

    # Pick the best match: exact > startsWith > contains
    best = None
    for s in suggestions:
        if s["exactMatch"]:
            best = s
            break
        if s["startsWith"] and (best is None or not best.get("startsWith")):
            best = s
        if s["contains"] and best is None:
            best = s

    if best is None:
        log.debug("No suggestion matched the typed text")
        return False

    log.info("Clicking suggestion: '%s' (exact=%s)", best["text"][:50], best["exactMatch"])

    # Click using text content matching (avoids stale selectors)
    try:
        clicked = page.evaluate('''(params) => {
            const typed = params.typedText.toLowerCase();
            const targetText = params.targetText.toLowerCase();

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

            const matches = Array.from(document.querySelectorAll(selectors)).filter(el => {
                const rect = el.getBoundingClientRect();
                if (rect.height === 0) return false;
                const t = el.textContent?.trim()?.toLowerCase() || '';
                return t === targetText || t.includes(typed) || typed.includes(t);
            });

            if (matches.length > 0) {
                matches[0].click();
                return true;
            }
            return false;
        }''', {"typedText": typed_text.lower(), "targetText": best["text"].lower()})

        if clicked:
            time.sleep(1)
            return True
        else:
            log.debug("Could not click suggestion element")
            return False
    except Exception as e:
        log.warning("Failed to click suggestion: %s", e)
        return False
