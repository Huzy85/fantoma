"""Login handler — fills login/signup forms without any LLM.

Reads the accessibility tree, matches fields by label, fills credentials,
clicks submit. Handles multi-step flows (email → Next → password → Login).
No tokens, no loops, works on every site with standard form labels.

Supports FormMemory for database-assisted field matching on unknown labels
and retry-on-empty logic for slow SPAs that render asynchronously.
"""
import json
import logging
import re
import time
from urllib.parse import urlparse

log = logging.getLogger("fantoma.form_login")

# Labels that identify each field type (matched case-insensitive)
EMAIL_LABELS = [
    "email", "e-mail", "email address", "email or phone",
    "phone, email, or username", "phone or email",
    "username or email", "mobile number, username or email",
    "your email", "work email", "sign-in email",
    "apple id", "account name",
]

USERNAME_LABELS = [
    "username", "user name", "user id", "userid", "login",
    "account", "handle", "screen name", "display name",
    "acct", "user", "yourname",
]

PASSWORD_LABELS = [
    "password", "passwd", "current password", "your password",
    "enter password", "sign-in password",
]

CONFIRM_PASSWORD_LABELS = [
    "confirm password", "confirmpassword", "repeat password",
    "re-enter password", "retype password", "password again",
    "verify password", "password_confirmation", "password confirmation",
]

FIRST_NAME_LABELS = [
    "first name", "firstname", "given name", "first",
    "your name", "full name", "name (optional)",
]

# "name" alone is ambiguous — could be first name or display name.
# We match it only when no username field exists on the page.
GENERIC_NAME_LABELS = ["name"]

LAST_NAME_LABELS = [
    "last name", "lastname", "surname", "family name", "last",
]

SUBMIT_LABELS = [
    "next", "continue", "log in", "login", "sign in", "signin",
    "sign up", "signup", "register", "create account",
    "submit", "proceed", "go", "send", "confirm", "verify",
    "create", "join", "get started", "let's go",
]

# Labels to SKIP — these are not submit buttons
SKIP_LABELS = [
    "forgot", "reset", "help", "privacy", "terms", "cookie",
    "sign in with", "continue with", "log in with",
    "sign up with", "register with",
    "close", "cancel", "back", "skip",
    "apple", "facebook", "github", "google", "twitter",
    "microsoft", "linkedin", "discord", "slack", "mymlh",
]

# Retry settings for slow SPAs
_EMPTY_RETRY_COUNT = 3
_EMPTY_RETRY_DELAY = 3.0


def login(browser, dom_extractor, email="", username="", password="",
          first_name="", last_name="",
          max_steps=5, step_delay=3.0, memory=None, visit_id=None,
          captcha_config=None, llm=None):
    """Fill a login/signup form using pure code. No LLM needed.

    Args:
        browser: BrowserEngine instance (already navigated to login page)
        dom_extractor: AccessibilityExtractor instance
        email: email address to fill
        username: username to fill (used for verification challenges too)
        password: password to fill
        max_steps: max form pages to handle (email→Next→password→Login = 2 steps)
        step_delay: seconds to wait between steps for page transitions
        memory: optional FormMemory instance for database-assisted matching
        visit_id: optional visit ID for memory recording

    Returns:
        dict with 'success' (bool), 'steps' (int), 'url' (final URL),
        'fields_filled' (list of field labels filled)
    """
    from fantoma.browser.actions import type_into
    from fantoma.browser.consent import dismiss_consent

    page = browser.get_page()
    fields_filled = []
    filled_purposes = set()  # track what purposes have been filled (email, password, etc.)
    prev_url = page.url
    prev_tree = ""
    domain = urlparse(page.url).netloc

    for step in range(max_steps):
        dismiss_consent(page)
        time.sleep(1)

        # Read the page
        tree = dom_extractor.extract(page)
        elements = dom_extractor._last_interactive

        # If page hasn't changed since last step, stop — submit didn't work
        current_url = page.url
        if step > 0 and current_url == prev_url and tree == prev_tree:
            log.info("Step %d: page unchanged after submit — stopping", step + 1)
            break
        prev_url = current_url
        prev_tree = tree

        # Retry-on-empty: if step > 0 and no fillable fields, wait for SPA render
        if step > 0 and not _has_fillable_fields(elements):
            retried = False
            for retry in range(_EMPTY_RETRY_COUNT):
                log.info("Step %d: no fillable fields (retry %d/%d) — waiting %.0fs",
                         step + 1, retry + 1, _EMPTY_RETRY_COUNT, _EMPTY_RETRY_DELAY)
                time.sleep(_EMPTY_RETRY_DELAY)
                tree = dom_extractor.extract(page)
                elements = dom_extractor._last_interactive
                if _has_fillable_fields(elements):
                    retried = True
                    break
            if not retried and not _has_fillable_fields(elements):
                log.warning("Step %d: no fillable fields after %d retries — stopping",
                            step + 1, _EMPTY_RETRY_COUNT)
                break

        if not elements:
            log.warning("Step %d: no interactive elements found", step + 1)
            break

        # Classify fields — try ARIA first, fall back to raw DOM if needed
        elements, email_field, username_field, password_field, \
            first_name_field, last_name_field, submit_button = \
            _classify_fields(page, elements, step, first_name, last_name, llm=llm)

        # If first_name provided but no first_name_field, check for generic "name"
        if first_name and not first_name_field and not username_field:
            first_name_field = _find_field(elements, GENERIC_NAME_LABELS)
            if first_name_field:
                log.info("Step %d: matched generic 'name' field as first name", step + 1)

        # Heuristic: if we found password but no username/email, the other
        # text input next to it is probably the username field
        if password_field and not email_field and not username_field:
            text_inputs = [e for e in elements
                           if e.get("role") == "textbox" and e is not password_field]
            if len(text_inputs) == 1:
                username_field = text_inputs[0]
                log.info("Step %d: inferred '%s' as username (only text input beside password)",
                         step + 1, username_field["name"])

        # Memory fallback: if hardcoded labels didn't match, check database
        if memory and not email_field and not username_field and not password_field:
            mem_elements = [{"label": e.get("name", ""), "role": e.get("role", "")}
                           for e in elements]
            hints = memory.lookup(domain, step, mem_elements)
            if hints:
                log.info("Step %d: memory provided %d field hints", step + 1, len(hints))
                for el in elements:
                    if el["role"] not in ("textbox", "input"):
                        continue
                    purpose = hints.get(el["name"])
                    if purpose == "email":
                        email_field = el
                    elif purpose == "username":
                        username_field = el
                    elif purpose == "password":
                        password_field = el

        filled_this_step = False
        filled_labels = []  # track what we filled for memory recording

        # Fill name fields (signup forms) — skip if already filled in a previous step
        if first_name_field and first_name and "first_name" not in filled_purposes:
            el = _get_element(page, dom_extractor, first_name_field)
            if el:
                if type_into(browser, el, first_name):
                    log.info("Step %d: filled '%s' with first name", step + 1, first_name_field["name"])
                    fields_filled.append(first_name_field["name"])
                    filled_labels.append(("first_name", first_name_field["name"]))
                    filled_purposes.add("first_name")

        if last_name_field and last_name and "last_name" not in filled_purposes:
            el = _get_element(page, dom_extractor, last_name_field)
            if el:
                if type_into(browser, el, last_name):
                    log.info("Step %d: filled '%s' with last name", step + 1, last_name_field["name"])
                    fields_filled.append(last_name_field["name"])
                    filled_labels.append(("last_name", last_name_field["name"]))
                    filled_purposes.add("last_name")

        # Fill email field
        if email_field and email and "email" not in filled_purposes:
            el = _get_element(page, dom_extractor, email_field)
            if el:
                if type_into(browser, el, email):
                    log.info("Step %d: filled '%s' with email", step + 1, email_field["name"])
                    fields_filled.append(email_field["name"])
                    filled_labels.append(("email", email_field["name"]))
                    filled_purposes.add("email")
                    filled_this_step = True

        # Fill username field — on signup forms both email AND username can exist
        is_signup = bool(email_field and username_field and email_field is not username_field)
        if username_field and username and "username" not in filled_purposes and (is_signup or not filled_this_step):
            el = _get_element(page, dom_extractor, username_field)
            if el:
                if type_into(browser, el, username):
                    log.info("Step %d: filled '%s' with username", step + 1, username_field["name"])
                    fields_filled.append(username_field["name"])
                    filled_labels.append(("username", username_field["name"]))
                    filled_purposes.add("username")
                    filled_this_step = True

        # Fill password field (if visible on this page)
        if password_field and password and "password" not in filled_purposes:
            el = _get_element(page, dom_extractor, password_field)
            if el:
                if type_into(browser, el, password):
                    log.info("Step %d: filled '%s' with password", step + 1, password_field["name"])
                    fields_filled.append(password_field["name"])
                    filled_labels.append(("password", password_field["name"]))
                    filled_purposes.add("password")
                    filled_this_step = True

            # Fill confirm password if present (signup forms)
            confirm_field = _find_field(elements, CONFIRM_PASSWORD_LABELS)
            if confirm_field:
                cel = _get_element(page, dom_extractor, confirm_field)
                if cel:
                    if type_into(browser, cel, password):
                        log.info("Step %d: filled '%s' with confirm password", step + 1, confirm_field["name"])
                        fields_filled.append(confirm_field["name"])
                        filled_labels.append(("confirm_password", confirm_field["name"]))

        # If there's a verification/challenge field and we have a username
        if not filled_this_step and username:
            challenge_field = _find_challenge(elements)
            if challenge_field:
                el = _get_element(page, dom_extractor, challenge_field)
                if el:
                    if type_into(browser, el, username):
                        log.info("Step %d: filled verification challenge with username", step + 1)
                        fields_filled.append(challenge_field["name"])
                        filled_labels.append(("challenge", challenge_field["name"]))
                        filled_this_step = True

        if not filled_this_step and step > 0:
            # Nothing to fill — we might be done or on an unrecognised page
            log.info("Step %d: no fillable fields found — stopping", step + 1)
            break

        # Handle CAPTCHA before submitting
        if captcha_config and filled_this_step:
            try:
                from fantoma.captcha.orchestrator import CaptchaOrchestrator
                captcha = CaptchaOrchestrator(captcha_config)
                captcha.handle(page, lambda: browser.screenshot())
            except Exception as e:
                log.debug("CAPTCHA handling: %s", e)

        # Click submit/next
        if submit_button:
            el = _get_element(page, dom_extractor, submit_button)
            if el:
                clicked = False
                # Try direct click first
                try:
                    el.click(timeout=5000)
                    clicked = True
                except Exception:
                    pass
                # Try dismissing consent then clicking
                if not clicked:
                    dismiss_consent(page)
                    time.sleep(1)
                    try:
                        el.click(timeout=5000)
                        clicked = True
                    except Exception:
                        pass
                # Try JS click
                if not clicked:
                    try:
                        page.evaluate("el => el.click()", el)
                        clicked = True
                    except Exception:
                        pass
                # Last resort: Enter key
                if not clicked:
                    page.keyboard.press("Enter")
                    log.info("Step %d: pressed Enter (all click methods failed on '%s')", step + 1, submit_button["name"])
                else:
                    log.info("Step %d: clicked '%s'", step + 1, submit_button["name"])
            else:
                page.keyboard.press("Enter")
                log.info("Step %d: pressed Enter (button not found by index)", step + 1)
        elif filled_this_step:
            page.keyboard.press("Enter")
            log.info("Step %d: pressed Enter (no submit button found)", step + 1)

        # Record step to memory if provided
        if memory and visit_id and filled_this_step:
            elements_json = json.dumps([{"label": e.get("name", ""), "role": e.get("role", "")}
                                        for e in elements])
            submit_label = submit_button["name"] if submit_button else ""
            for purpose, label in filled_labels:
                try:
                    field_el = next(e for e in elements if e["name"] == label)
                    memory.record_step(
                        domain=domain, visit_id=visit_id, step_number=step,
                        field_label=label, field_role=field_el.get("role", ""),
                        field_purpose=purpose, submit_label=submit_label,
                        success=True, tree_text=tree, elements_json=elements_json,
                        url=current_url, action="filled", result="ok"
                    )
                except (StopIteration, Exception) as exc:
                    log.debug("Failed to record step to memory: %s", exc)

        # Wait for page to settle
        time.sleep(step_delay)

        # Check if we've left the login page
        new_url = page.url
        if _looks_logged_in(page, new_url):
            log.info("Login complete — landed on: %s", new_url)
            return {
                "success": True,
                "steps": step + 1,
                "url": new_url,
                "fields_filled": fields_filled,
            }

    final_url = page.url
    return {
        "success": _looks_logged_in(page, final_url),
        "steps": max_steps,
        "url": final_url,
        "fields_filled": fields_filled,
    }


def _build_element_html(el):
    """Build an HTML-like representation of an element for the LLM."""
    tag = "input" if el.get("role") in ("textbox", "input") else "button"
    attrs = ""
    if el.get("type"):
        attrs += f' type="{el["type"]}"'
    if el.get("name"):
        attrs += f' name="{el["name"]}"'
    if el.get("_selector"):
        sel = el["_selector"]
        if "#" in sel:
            attrs += f' id="{sel.split("#")[1].split("]")[0]}"'
    if tag == "button":
        return f'[{el.get("index", "?")}] <button{attrs}>{el.get("name", "")}</button>'
    return f'[{el.get("index", "?")}] <input{attrs}>'


def _parse_llm_labels(raw):
    """Parse LLM response '[3]=email, [5]=password' into {3: 'email', 5: 'password'}."""
    if not raw:
        return {}
    result = {}
    for match in re.finditer(r'\[(\d+)\]\s*=\s*(\w+)', raw):
        idx = int(match.group(1))
        label = match.group(2).lower()
        result[idx] = label
    return result


def _ask_llm_to_label(llm, elements, url):
    """Ask LLM to label unmatched form elements. Returns {index: label}."""
    if not llm or not elements:
        return {}

    from fantoma.llm.prompts import FIELD_LABELLER_SYSTEM

    lines = [f"URL: {url}", "", "Elements:"]
    for el in elements:
        lines.append(_build_element_html(el))

    user_msg = "\n".join(lines)

    try:
        raw = llm.chat(
            [{"role": "system", "content": FIELD_LABELLER_SYSTEM},
             {"role": "user", "content": user_msg}],
            max_tokens=100,
            temperature=0.1,
        )
        labels = _parse_llm_labels(raw or "")
        if labels:
            log.info("LLM labelled %d elements: %s", len(labels), labels)
        return labels
    except Exception as e:
        log.warning("LLM labelling failed: %s", e)
        return {}


def _apply_llm_labels(labels, elements, page, dom_extractor):
    """Apply LLM-provided labels to elements. Returns field mapping + clicks checkboxes."""
    fields = {}
    checkboxes_clicked = 0

    elements_by_idx = {el.get("index"): el for el in elements if el.get("index") is not None}

    for idx, label in labels.items():
        el = elements_by_idx.get(idx)
        if not el:
            continue

        if label in ("email", "username", "password", "confirm_password",
                     "first_name", "last_name", "full_name", "phone", "address",
                     "submit", "2fa_code"):
            fields[label] = el
        elif label == "checkbox_terms":
            handle = _get_element(page, dom_extractor, el)
            if handle:
                try:
                    handle.click()
                    checkboxes_clicked += 1
                    log.info("Clicked terms checkbox: '%s'", el.get("name", ""))
                except Exception as e:
                    log.debug("Failed to click checkbox: %s", e)
        elif label == "captcha":
            fields["captcha"] = el

    fields["checkboxes_clicked"] = checkboxes_clicked
    return fields


def _classify_fields(page, elements, step, first_name, last_name, llm=None):
    """Match elements to field types. Falls back to raw DOM if ARIA misses inputs."""

    def _do_classify(elements):
        email = _find_field(elements, EMAIL_LABELS)
        username = _find_field(elements, USERNAME_LABELS)
        password = _find_field(elements, PASSWORD_LABELS)
        submit = _find_submit(elements)
        fn = _find_field(elements, FIRST_NAME_LABELS) if first_name else None
        ln = _find_field(elements, LAST_NAME_LABELS) if last_name else None
        return email, username, password, fn, ln, submit

    # First pass: try ARIA elements as-is
    email_f, user_f, pass_f, fn_f, ln_f, submit_f = _do_classify(elements)

    # Always supplement with raw DOM — ARIA often misses some fields
    # (e.g. password visible in raw DOM but not ARIA, or vice versa)
    raw_inputs = _find_raw_inputs(page)
    if raw_inputs:
        # Only add inputs not already represented in ARIA
        aria_labels = {e.get("name", "").lower() for e in elements if e.get("role") in ("textbox", "input")}
        new_inputs = [r for r in raw_inputs if r["name"].lower() not in aria_labels]
        if new_inputs:
            log.info("Step %d: raw DOM found %d additional inputs",
                     step + 1, len(new_inputs))
            elements = new_inputs + elements
            email_f, user_f, pass_f, fn_f, ln_f, submit_f = _do_classify(elements)

    # Also find submit button from raw DOM if still missing
    if not submit_f:
        raw_buttons = _find_raw_buttons(page)
        if raw_buttons:
            elements = elements + raw_buttons
            submit_f = _find_submit(elements)

    # LLM fallback: if fields are still unmatched, ask the LLM to label them
    matched_any = any([email_f, user_f, pass_f, fn_f, ln_f])
    has_fillable = _has_fillable_fields(elements)
    if not matched_any and has_fillable and llm:
        url = ""
        try:
            url = page.url
        except Exception:
            pass
        labels = _ask_llm_to_label(llm, elements, url)
        if labels:
            applied = _apply_llm_labels(labels, elements, page, None)
            email_f = applied.get("email", email_f)
            user_f = applied.get("username", user_f)
            pass_f = applied.get("password", pass_f)
            fn_f = applied.get("first_name") or applied.get("full_name") or fn_f
            ln_f = applied.get("last_name", ln_f)
            if not submit_f and applied.get("submit"):
                submit_f = applied["submit"]

    return elements, email_f, user_f, pass_f, fn_f, ln_f, submit_f


def _get_element(page, dom_extractor, field):
    """Get element handle — via ARIA index or CSS selector for raw inputs."""
    if field.get("index", -1) >= 0:
        return dom_extractor.get_element_by_index(page, field["index"])
    # Raw input fallback — use CSS selector
    selector = field.get("_selector")
    if selector:
        try:
            return page.query_selector(selector)
        except Exception:
            pass
    return None


def _find_raw_inputs(page):
    """Fallback: find <input> elements via JS when ARIA tree misses them.

    Some sites (HN, old-style HTML) use bare <input> without ARIA labels.
    This queries the DOM directly and builds element dicts compatible with
    the rest of form_login's matching logic.
    """
    try:
        inputs = page.evaluate("""() => {
            const inputs = document.querySelectorAll(
                'input[type="text"], input[type="email"], input[type="password"], ' +
                'input[type="tel"], input:not([type])'
            );
            return Array.from(inputs)
                .filter(el => el.offsetParent !== null)  // visible only
                .map((el, i) => ({
                    name: el.getAttribute('aria-label')
                        || el.getAttribute('placeholder')
                        || el.getAttribute('name')
                        || el.getAttribute('id')
                        || el.type
                        || 'input',
                    role: el.type === 'password' ? 'input' : 'textbox',
                    type: el.type || 'text',
                    index: -1,  // marker for raw input
                    _selector: el.name
                        ? `input[name="${el.name}"]`
                        : el.id
                            ? `#${el.id}`
                            : `input[type="${el.type || 'text'}"]:nth-of-type(${i + 1})`
                }));
        }""")
        return inputs or []
    except Exception as e:
        log.debug("Raw input fallback failed: %s", e)
        return []


def _find_raw_buttons(page):
    """Find <button> and input[type=submit] via JS when ARIA missed them."""
    try:
        buttons = page.evaluate("""() => {
            const btns = document.querySelectorAll(
                'button[type="submit"], input[type="submit"], button:not([type])'
            );
            return Array.from(btns)
                .filter(el => el.offsetParent !== null)
                .map(el => ({
                    name: el.textContent?.trim() || el.value || el.getAttribute('id') || 'submit',
                    role: 'button',
                    index: -1,
                    _selector: el.id ? `#${el.id}`
                        : el.name ? `button[name="${el.name}"]`
                        : `button:has-text("${(el.textContent?.trim() || '').slice(0, 30)}")`
                }));
        }""")
        return buttons or []
    except Exception:
        return []


def _has_fillable_fields(elements):
    """Check if elements list contains any textbox or input fields."""
    if not elements:
        return False
    for el in elements:
        if el.get("role") in ("textbox", "input"):
            return True
    return False


def _find_field(elements, labels):
    """Find the first textbox/input matching any of the given labels."""
    for el in elements:
        if el.get("role") not in ("textbox", "input"):
            continue
        name = el.get("name", "").lower()
        # Match by label
        for label in labels:
            if label in name:
                return el
        # Match password inputs by type attribute
        if el.get("type") == "password" and labels is PASSWORD_LABELS:
            return el
    return None


def _find_challenge(elements):
    """Find a verification/challenge input (X's username step, 2FA code, etc)."""
    for el in elements:
        if el["role"] not in ("textbox", "input"):
            continue
        name = el["name"].lower()
        if any(w in name for w in ["verify", "confirm", "code", "challenge",
                                    "phone", "enter your"]):
            return el
    # If there's exactly one textbox and it's not email/password, it's probably a challenge
    textboxes = [el for el in elements if el["role"] in ("textbox", "input")]
    if len(textboxes) == 1:
        name = textboxes[0]["name"].lower()
        if not any(w in name for w in ["email", "password", "search"]):
            return textboxes[0]
    return None


def _find_submit(elements):
    """Find the most likely submit/next button. Skips OAuth, forgot password, etc."""
    for el in elements:
        if el["role"] != "button":
            continue
        name = el["name"].lower()

        # Skip non-submit buttons
        if any(skip in name for skip in SKIP_LABELS):
            continue

        # Match submit patterns
        for pattern in SUBMIT_LABELS:
            if pattern in name:
                return el

    return None


def _looks_logged_in(page, url):
    """Quick heuristic: are we past the login page?"""
    url_lower = url.lower()
    login_indicators = ["/login", "/signin", "/sign-in", "/sign_in",
                        "/flow/login", "/authenticate", "/auth",
                        "/signup", "/register", "/join"]

    # If URL still contains login paths, probably not logged in
    if any(ind in url_lower for ind in login_indicators):
        return False

    # Check for common logged-in indicators
    try:
        body = page.inner_text("body")[:500].lower()
        logged_out_indicators = ["sign in", "log in", "create account",
                                  "forgot password", "register"]
        logged_in_indicators = ["dashboard", "feed", "home", "welcome",
                                 "account", "profile", "settings", "logout",
                                 "sign out", "log out"]

        out_score = sum(1 for ind in logged_out_indicators if ind in body)
        in_score = sum(1 for ind in logged_in_indicators if ind in body)

        return in_score > out_score
    except Exception:
        return False
