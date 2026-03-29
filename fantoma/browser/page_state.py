"""Post-action verification and inline error detection.

After every browser action, checks what actually happened:
- Did the URL change?
- Did the DOM change?
- Are there error messages on the page?
- How many new interactive elements appeared?

All checks are code-only — no LLM calls.
"""
import hashlib
import logging

log = logging.getLogger("fantoma.page_state")

# JS that scans the page for visible error messages.
_ERROR_DETECTION_JS = """() => {
    const errors = new Set();
    const MAX = 3;

    // 1. role="alert" or aria-live="assertive"
    const alerts = document.querySelectorAll('[role="alert"], [aria-live="assertive"]');
    for (const el of alerts) {
        const text = el.textContent?.trim();
        if (text && text.length < 200 && el.offsetParent !== null) {
            errors.add(text);
            if (errors.size >= MAX) return [...errors];
        }
    }

    // 2. Error CSS classes
    const errorClasses = document.querySelectorAll(
        '.error, .invalid, .warning, .danger, .alert-danger, .form-error, ' +
        '.field-error, .input-error, .validation-error, .error-message'
    );
    for (const el of errorClasses) {
        const text = el.textContent?.trim();
        if (text && text.length < 200 && text.length > 3 && el.offsetParent !== null) {
            errors.add(text);
            if (errors.size >= MAX) return [...errors];
        }
    }

    // 3. aria-invalid="true" — find their error descriptions
    const invalidInputs = document.querySelectorAll('[aria-invalid="true"]');
    for (const input of invalidInputs) {
        const describedBy = input.getAttribute('aria-describedby') || input.getAttribute('aria-errormessage');
        if (describedBy) {
            for (const id of describedBy.split(/\\s+/)) {
                const desc = document.getElementById(id);
                if (desc) {
                    const text = desc.textContent?.trim();
                    if (text && text.length < 200) {
                        errors.add(text);
                        if (errors.size >= MAX) return [...errors];
                    }
                }
            }
        }
    }

    // 4. Visible text pattern matching (last resort)
    const patterns = /invalid|incorrect|failed|try again|required field|already exists|too short|doesn't match|not found|wrong password|wrong email/i;
    const candidates = document.querySelectorAll('p, span, div, li, label');
    for (const el of candidates) {
        if (el.children.length > 2) continue;
        const text = el.textContent?.trim();
        if (text && text.length > 5 && text.length < 150 && el.offsetParent !== null) {
            if (patterns.test(text)) {
                errors.add(text);
                if (errors.size >= MAX) return [...errors];
            }
        }
    }

    return [...errors];
}"""


def detect_errors(page) -> list[str]:
    """Scan the page for visible error messages. Returns up to 3 error strings."""
    try:
        errors = page.evaluate(_ERROR_DETECTION_JS)
        if isinstance(errors, list):
            return errors[:3]
        return []
    except Exception as e:
        log.debug("Error detection failed: %s", e)
        return []


def verify_action(page, pre_url: str, pre_dom_hash: str, dom_extractor) -> dict:
    """Check what happened after a browser action.

    Args:
        page: Playwright page object
        pre_url: URL before the action
        pre_dom_hash: DOM hash before the action
        dom_extractor: AccessibilityExtractor (for element count)

    Returns:
        ActionOutcome dict with url_changed, error_found, new_elements, dom_changed
    """
    current_url = page.url
    url_changed = current_url != pre_url
    new_elements = len(dom_extractor._last_interactive)
    errors = detect_errors(page)
    error_found = errors[0] if errors else None

    try:
        body_text = page.inner_text("body")[:2000]
        current_hash = hashlib.md5(body_text.encode()).hexdigest()[:12]
    except Exception:
        current_hash = "unknown"

    dom_changed = current_hash != pre_dom_hash

    return {
        "url_changed": url_changed,
        "error_found": error_found,
        "new_elements": new_elements,
        "dom_changed": dom_changed,
    }


def dom_hash(page) -> str:
    """Compute a short hash of the page body text for change detection."""
    try:
        body_text = page.inner_text("body")[:2000]
        return hashlib.md5(body_text.encode()).hexdigest()[:12]
    except Exception:
        return "unknown"
