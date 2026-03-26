"""Login handler — fills login/signup forms without any LLM.

Reads the accessibility tree, matches fields by label, fills credentials,
clicks submit. Handles multi-step flows (email → Next → password → Login).
No tokens, no loops, works on every site with standard form labels.
"""
import logging
import re
import time

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
]

PASSWORD_LABELS = [
    "password", "passwd", "current password", "your password",
    "enter password", "sign-in password",
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
    "close", "cancel", "back", "skip",
]


def login(browser, dom_extractor, email="", username="", password="",
          max_steps=5, step_delay=3.0):
    """Fill a login/signup form using pure code. No LLM needed.

    Args:
        browser: BrowserEngine instance (already navigated to login page)
        dom_extractor: AccessibilityExtractor instance
        email: email address to fill
        username: username to fill (used for verification challenges too)
        password: password to fill
        max_steps: max form pages to handle (email→Next→password→Login = 2 steps)
        step_delay: seconds to wait between steps for page transitions

    Returns:
        dict with 'success' (bool), 'steps' (int), 'url' (final URL),
        'fields_filled' (list of field labels filled)
    """
    from fantoma.browser.actions import type_into
    from fantoma.browser.consent import dismiss_consent

    page = browser.get_page()
    fields_filled = []
    prev_url = page.url
    prev_tree = ""

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

        if not elements:
            log.warning("Step %d: no interactive elements found", step + 1)
            break

        # Classify what's on the page
        email_field = _find_field(elements, EMAIL_LABELS)
        username_field = _find_field(elements, USERNAME_LABELS)
        password_field = _find_field(elements, PASSWORD_LABELS)
        submit_button = _find_submit(elements)

        filled_this_step = False

        # Fill email/username field
        if email_field and email:
            el = dom_extractor.get_element_by_index(page, email_field["index"])
            if el:
                if type_into(browser, el, email):
                    log.info("Step %d: filled '%s' with email", step + 1, email_field["name"])
                    fields_filled.append(email_field["name"])
                    filled_this_step = True

        if username_field and username and not filled_this_step:
            el = dom_extractor.get_element_by_index(page, username_field["index"])
            if el:
                if type_into(browser, el, username):
                    log.info("Step %d: filled '%s' with username", step + 1, username_field["name"])
                    fields_filled.append(username_field["name"])
                    filled_this_step = True

        # Fill password field (if visible on this page)
        if password_field and password:
            el = dom_extractor.get_element_by_index(page, password_field["index"])
            if el:
                if type_into(browser, el, password):
                    log.info("Step %d: filled '%s' with password", step + 1, password_field["name"])
                    fields_filled.append(password_field["name"])
                    filled_this_step = True

        # If there's a verification/challenge field and we have a username
        if not filled_this_step and username:
            challenge_field = _find_challenge(elements)
            if challenge_field:
                el = dom_extractor.get_element_by_index(page, challenge_field["index"])
                if el:
                    if type_into(browser, el, username):
                        log.info("Step %d: filled verification challenge with username", step + 1)
                        fields_filled.append(challenge_field["name"])
                        filled_this_step = True

        if not filled_this_step and step > 0:
            # Nothing to fill — we might be done or on an unrecognised page
            log.info("Step %d: no fillable fields found — stopping", step + 1)
            break

        # Click submit/next
        if submit_button:
            el = dom_extractor.get_element_by_index(page, submit_button["index"])
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


def _find_field(elements, labels):
    """Find the first textbox/input matching any of the given labels."""
    for el in elements:
        if el["role"] not in ("textbox", "input"):
            continue
        name = el["name"].lower()
        for label in labels:
            if label in name:
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
