"""Autocomplete handler — detects and selects dropdown suggestions after typing."""
import logging
import time

log = logging.getLogger("fantoma.autocomplete")


def handle_autocomplete(page, typed_text: str, timeout: float = 3.0) -> bool:
    """After typing, check for autocomplete suggestions and click the best match.
    
    Returns True if a suggestion was found and clicked, False otherwise.
    This is pure code — no LLM needed.
    """
    time.sleep(timeout)  # Wait for suggestions to render
    
    # Find the active/focused input
    input_rect = page.evaluate('''() => {
        const input = document.activeElement;
        if (!input || input.tagName !== 'INPUT') return null;
        const rect = input.getBoundingClientRect();
        return {top: rect.top, left: rect.left, bottom: rect.bottom, right: rect.right, width: rect.width};
    }''')
    
    if not input_rect:
        return False
    
    # Find all visible clickable elements near the input (within 300px below)
    suggestions = page.evaluate('''(params) => {
        const {bottom, left, right} = params.inputRect;
        const typed = params.typedText.toLowerCase();
        const candidates = [];
        
        // Look for list items, options, suggestions near the input
        const selectors = 'li, [role="option"], [role="button"], div[class*="suggest"], div[class*="result"]';
        document.querySelectorAll(selectors).forEach(el => {
            const rect = el.getBoundingClientRect();
            if (rect.height === 0 || rect.width === 0) return;
            
            // Must be below the input and roughly aligned horizontally
            if (rect.top < bottom - 5) return;
            if (rect.top > bottom + 300) return;
            if (rect.right < left - 50 || rect.left > right + 50) return;
            
            const text = el.textContent?.trim() || '';
            if (!text) return;
            
            candidates.push({
                text: text.substring(0, 100),
                top: rect.top,
                exactMatch: text.toLowerCase() === typed,
                startsWith: text.toLowerCase().startsWith(typed),
                contains: text.toLowerCase().includes(typed),
                selector: el.id ? '#' + el.id : null,
                index: candidates.length,
            });
        });
        
        return candidates;
    }''', {"inputRect": input_rect, "typedText": typed_text})
    
    if not suggestions:
        log.debug("No autocomplete suggestions found near input")
        return False
    
    log.info("Found %d autocomplete suggestions", len(suggestions))
    
    # Pick the best match: exact > startsWith > contains > first
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
        best = suggestions[0]  # Just pick the first one
    
    log.info("Clicking suggestion: '%s' (exact=%s)", best["text"][:50], best["exactMatch"])
    
    # Click the suggestion using its position (most reliable)
    try:
        page.evaluate(f'''() => {{
            const typed = "{typed_text.lower()}";
            const selectors = 'li, [role="option"], [role="button"]';
            const candidates = Array.from(document.querySelectorAll(selectors)).filter(el => {{
                const rect = el.getBoundingClientRect();
                return rect.height > 0 && el.textContent?.trim()?.toLowerCase() === typed;
            }});
            if (candidates.length > 0) {{
                candidates[0].click();
                return true;
            }}
            // Fallback: click first suggestion near input
            const input = document.activeElement;
            if (!input) return false;
            const inputRect = input.getBoundingClientRect();
            const nearby = Array.from(document.querySelectorAll(selectors)).filter(el => {{
                const rect = el.getBoundingClientRect();
                return rect.height > 0 && rect.top >= inputRect.bottom - 5 && rect.top <= inputRect.bottom + 300;
            }});
            if (nearby.length > 0) {{
                nearby[0].click();
                return true;
            }}
            return false;
        }}''')
        time.sleep(1)
        return True
    except Exception as e:
        log.warning("Failed to click suggestion: %s", e)
        return False
