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
    const patterns = /invalid|incorrect|failed|try again|required field|already exists|too short|doesn't match|not found|wrong password|wrong email|too many requests|rate limit|blocked|access denied|forbidden|captcha|verify you are human|sign in to continue|log in to continue|please log in|please sign in/i;
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


def classify_blocker(page) -> str | None:
    """Detect if the page is a blocker (rate limit, login wall, CAPTCHA).

    Returns "rate_limit", "login_wall", "captcha", or None.
    """
    try:
        return page.evaluate("""() => {
            const text = document.body?.innerText?.toLowerCase() || '';
            const url = location.href.toLowerCase();

            // Rate limit / access denied
            if (/too many requests|rate limit|429|throttl/.test(text) ||
                /access denied|forbidden|403/.test(text))
                return 'rate_limit';

            // CAPTCHA challenge
            if (/captcha|verify you are human|are you a robot|challenge/.test(text) ||
                document.querySelector('iframe[src*="captcha"], iframe[src*="challenge"]'))
                return 'captcha';

            // Login wall
            if (/(sign|log)\\s*in\\s*(to continue|required|to access)/i.test(text) ||
                /\\/login|\\/signin|\\/sign-in|\\/auth/.test(url))
                return 'login_wall';

            return null;
        }""")
    except Exception:
        return None


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


# ── Task intent inference and progress assessment ───────────────

_AUTH_KEYWORDS = ("login", "sign in", "log in", "authenticate", "signin")
_EXTRACT_KEYWORDS = ("extract", "scrape", "read", "copy", "get")
_NAVIGATE_KEYWORDS = ("go to", "visit", "open", "navigate")

_AUTH_URL_SEGMENTS = ("login", "signin", "sign-in", "sign_in", "authenticate", "auth")
_SUBMIT_NAMES = ("submit", "sign in", "login", "log in", "send", "confirm", "register")


def _infer_task_intent(task: str) -> str | None:
    """Map a task description to an intent category.

    Returns "auth", "extract", "navigate", or None.
    Uses word-boundary matching to avoid false positives (e.g. "target" matching "get").
    """
    import re
    task_lower = task.lower()
    for kw in _AUTH_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', task_lower):
            return "auth"
    for kw in _EXTRACT_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', task_lower):
            return "extract"
    for kw in _NAVIGATE_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', task_lower):
            return "navigate"
    return None


def assess_progress(page, action: str, task: str, dom_extractor,
                    pre_url: str = None, action_element: dict = None) -> dict:
    """Assess whether an action achieved its intent and whether the task is progressing.

    Returns {"action_ok": bool, "progress_ok": bool|None, "reason": str}
    """
    import re

    action_verb = action.strip().split()[0].upper() if action.strip() else ""
    current_url = page.url
    reasons = []

    # ── Layer 1: Action-level verification ──────────────────────
    action_ok = True  # default for unchecked actions

    if action_verb == "TYPE":
        # Read active element value, check typed text is present
        match = re.search(r"""['"](.+?)['"]""", action)
        typed_text = match.group(1) if match else ""
        try:
            value = page.evaluate("() => document.activeElement?.value || ''")
        except Exception:
            value = ""
        action_ok = bool(typed_text and typed_text.lower() in (value or "").lower())
        reasons.append(f"TYPE: value={'present' if action_ok else 'missing'}")

    elif action_verb == "SELECT":
        match = re.search(r"""['"](.+?)['"]""", action)
        selected_text = match.group(1) if match else ""
        try:
            value = page.evaluate("() => document.activeElement?.value || ''")
        except Exception:
            value = ""
        action_ok = bool(selected_text and selected_text.lower() in (value or "").lower())
        reasons.append(f"SELECT: value={'present' if action_ok else 'missing'}")

    elif action_verb == "CLICK":
        url_changed = pre_url is not None and current_url != pre_url
        if action_element:
            role = (action_element.get("role") or "").lower()
            name = (action_element.get("name") or "").lower()

            is_submit = (role == "button" and
                         any(sn in name for sn in _SUBMIT_NAMES))
            is_link = role == "link"

            if is_submit or is_link:
                action_ok = url_changed
                reasons.append(f"CLICK {'submit' if is_submit else 'link'}: URL {'changed' if url_changed else 'unchanged'}")
            else:
                reasons.append("CLICK: non-submit/link element")
        else:
            reasons.append("CLICK: no element info")

    elif action_verb in ("SCROLL", "WAIT"):
        action_ok = True
        reasons.append(f"{action_verb}: auto-ok")
    else:
        reasons.append(f"{action_verb}: auto-ok")

    # ── Layer 2: Task-level progress ────────────────────────────
    intent = _infer_task_intent(task)
    progress_ok = None

    if intent == "auth":
        # URL no longer contains login/signin/etc segments
        url_lower = current_url.lower()
        still_on_auth = any(seg in url_lower for seg in _AUTH_URL_SEGMENTS)
        progress_ok = not still_on_auth
        reasons.append(f"auth: {'left' if progress_ok else 'still on'} auth page")

    elif intent == "extract":
        try:
            body_len = len(page.inner_text("body"))
        except Exception:
            body_len = 0
        progress_ok = body_len > 200
        reasons.append(f"extract: body {body_len} chars")

    elif intent == "navigate":
        if pre_url is not None:
            progress_ok = current_url != pre_url
            reasons.append(f"navigate: URL {'changed' if progress_ok else 'unchanged'}")
        else:
            progress_ok = None
            reasons.append("navigate: no pre_url")

    return {
        "action_ok": action_ok,
        "progress_ok": progress_ok,
        "reason": "; ".join(reasons),
    }
