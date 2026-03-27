# Fantoma v0.4 — Email Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After signup form submission, automatically complete email verification — by IMAP (autonomous), terminal prompt (interactive), or user callback.

**Architecture:** New `email_verify.py` module handles IMAP polling and code/link extraction. `form_login.py` detects verification pages. `agent.py` orchestrates the three tiers: IMAP → callback → terminal prompt. Config gets an `EmailConfig` dataclass.

**Tech Stack:** Python `imaplib` (stdlib), existing `verification.py` regex extractors

**Spec:** `docs/superpowers/specs/2026-03-27-fantoma-v04-email-verification-design.md`

---

## Files

| File | Change | Responsibility |
|------|--------|---------------|
| `fantoma/browser/email_verify.py` | **NEW** | IMAP polling, code/link extraction from raw email |
| `fantoma/config.py` | Add `EmailConfig` | IMAP connection settings |
| `fantoma/browser/form_login.py` | Detect verification page after submit | Return `verification_needed` + type |
| `fantoma/agent.py` | Add `email_imap`, `verification_callback` params, orchestrate tiers | Wire verification into login() |
| `tests/test_email_verify.py` | **NEW** | All verification tests |

---

### Task 1: EmailConfig + email_verify.py (IMAP polling and extraction)

**Files:**
- Modify: `fantoma/config.py`
- Create: `fantoma/browser/email_verify.py`
- Create: `tests/test_email_verify.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_email_verify.py

import os
import tempfile
from unittest.mock import MagicMock, patch


def test_email_config_defaults():
    from fantoma.config import EmailConfig
    cfg = EmailConfig()
    assert cfg.host == ""
    assert cfg.port == 993
    assert cfg.user == ""
    assert cfg.password == ""


def test_email_config_custom():
    from fantoma.config import EmailConfig
    cfg = EmailConfig(host="127.0.0.1", port=1143, user="me@test.com", password="pass")
    assert cfg.port == 1143


def test_extract_code_from_email_body():
    from fantoma.browser.email_verify import extract_code_from_body
    body = "Your verification code is 847291. Enter it to continue."
    assert extract_code_from_body(body) == "847291"


def test_extract_code_ignores_years():
    from fantoma.browser.email_verify import extract_code_from_body
    body = "Welcome in 2026! Your code: 938471"
    assert extract_code_from_body(body) == "938471"


def test_extract_code_returns_none():
    from fantoma.browser.email_verify import extract_code_from_body
    assert extract_code_from_body("No codes here, just text.") is None


def test_extract_link_from_email_body():
    from fantoma.browser.email_verify import extract_link_from_body
    body = '<a href="https://example.com/verify?token=abc123">Verify your email</a>'
    link = extract_link_from_body(body, "example.com")
    assert link == "https://example.com/verify?token=abc123"


def test_extract_link_from_plain_text():
    from fantoma.browser.email_verify import extract_link_from_body
    body = "Click here to verify: https://example.com/confirm/abc123 Thanks!"
    link = extract_link_from_body(body, "example.com")
    assert "confirm" in link


def test_extract_link_returns_none():
    from fantoma.browser.email_verify import extract_link_from_body
    assert extract_link_from_body("No links here.", "example.com") is None


def test_check_inbox_no_config():
    from fantoma.browser.email_verify import check_inbox
    result = check_inbox(None, "example.com")
    assert result is None


def test_check_inbox_mocked():
    from fantoma.browser.email_verify import check_inbox
    from fantoma.config import EmailConfig

    mock_imap = MagicMock()
    mock_imap.search.return_value = ("OK", [b"1 2 3"])

    # Build a fake email with a verification code
    import email.mime.text
    msg = email.mime.text.MIMEText("Your code is 123456")
    msg["From"] = "noreply@example.com"
    msg["Subject"] = "Verify your account"
    raw_bytes = msg.as_bytes()

    mock_imap.fetch.return_value = ("OK", [(b"1", raw_bytes)])

    config = EmailConfig(host="localhost", port=1143, user="test", password="test")

    with patch("fantoma.browser.email_verify.imaplib.IMAP4") as MockIMAP:
        MockIMAP.return_value = mock_imap
        mock_imap.login.return_value = ("OK", [])
        mock_imap.select.return_value = ("OK", [b"3"])

        result = check_inbox(config, "example.com", timeout=1, poll_interval=0.1)

    assert result is not None
    assert result["type"] in ("code", "link")
    if result["type"] == "code":
        assert result["value"] == "123456"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_email_verify.py -v`
Expected: FAIL — modules don't exist

- [ ] **Step 3: Add EmailConfig to config.py**

Add before `FantomaConfig`:

```python
@dataclass
class EmailConfig:
    """IMAP email settings for autonomous verification."""
    host: str = ""
    port: int = 993
    user: str = ""
    password: str = ""
    security: str = "ssl"  # "ssl" (port 993), "starttls" (port 143/1143), "none" (testing)
```

Add to `FantomaConfig`:

```python
    email: EmailConfig = field(default_factory=EmailConfig)
```

- [ ] **Step 4: Implement email_verify.py**

```python
# fantoma/browser/email_verify.py

"""Email verification — polls IMAP for verification codes and links.

Three extraction methods:
1. Code: 4-8 digit number near verification keywords
2. Link: URL containing verify/confirm/activate
3. Neither: returns None

Uses stdlib imaplib — no extra dependencies.
"""

import email
import imaplib
import logging
import re
import time
from typing import Optional

log = logging.getLogger("fantoma.email_verify")

VERIFY_URL_KEYWORDS = [
    "verify", "confirm", "activate", "validate",
    "email-verification", "account/verify", "auth/confirm",
    "registration/confirm", "signup/verify",
]


def check_inbox(email_config, site_domain, timeout=120, poll_interval=10):
    """Poll IMAP for a verification email from site_domain.

    Returns:
        {"type": "code", "value": "123456", "subject": "..."} or
        {"type": "link", "value": "https://...", "subject": "..."} or
        None if no verification email found within timeout.
    """
    if not email_config or not email_config.host:
        return None

    log.info("Checking IMAP for verification from %s (max %ds)", site_domain, timeout)
    start = time.time()
    site_lower = site_domain.lower().replace("www.", "")

    while time.time() - start < timeout:
        try:
            conn = imaplib.IMAP4(email_config.host, email_config.port)
            conn.login(email_config.user, email_config.password)
            conn.select("INBOX")

            # Search for recent unread emails first, then all
            for search_criteria in ["UNSEEN", "ALL"]:
                typ, data = conn.search(None, search_criteria)
                if not data[0]:
                    continue

                ids = data[0].split()
                # Check newest first (last 10)
                for eid in reversed(ids[-10:]):
                    typ, msg_data = conn.fetch(eid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    sender = str(msg.get("From", "")).lower()
                    subject = str(msg.get("Subject", ""))

                    # Match by domain in sender
                    if site_lower not in sender and site_lower not in subject.lower():
                        continue

                    log.info("Found email from %s: %s", sender[:40], subject[:60])

                    # Get body
                    body = _get_body(msg)

                    # Try code first, then link
                    code = extract_code_from_body(body)
                    if code:
                        conn.logout()
                        return {"type": "code", "value": code, "subject": subject}

                    link = extract_link_from_body(body, site_lower)
                    if link:
                        conn.logout()
                        return {"type": "link", "value": link, "subject": subject}

                    log.info("Email matched but no code/link found")

            conn.logout()
        except Exception as e:
            log.warning("IMAP error: %s", e)

        if time.time() - start + poll_interval < timeout:
            log.debug("No verification email yet, waiting %ds...", poll_interval)
            time.sleep(poll_interval)
        else:
            break

    return None


def extract_code_from_body(body):
    """Extract a 4-8 digit verification code from email body."""
    if not body:
        return None
    codes = re.findall(r'\b(\d{4,8})\b', body)
    filtered = []
    for code in codes:
        num = int(code)
        if 1900 <= num <= 2099:
            continue
        if num < 1000:
            continue
        filtered.append(code)
    if not filtered:
        return None
    # Prefer 6-digit codes (most common)
    six = [c for c in filtered if len(c) == 6]
    return six[0] if six else filtered[0]


def extract_link_from_body(body, site_domain=""):
    """Extract a verification link from email body."""
    if not body:
        return None
    urls = re.findall(r'https?://[^\s<>"\']+', body)
    for url in urls:
        url_lower = url.lower()
        if any(kw in url_lower for kw in VERIFY_URL_KEYWORDS):
            return url.rstrip(".,;)>]\"'")
    # Fallback: any URL matching the site domain
    if site_domain:
        for url in urls:
            if site_domain in url.lower():
                return url.rstrip(".,;)>]\"'")
    return None


def _get_body(msg):
    """Extract text body from email message."""
    if msg.is_multipart():
        html_body = ""
        text_body = ""
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded = payload.decode(errors="ignore")
            if ct == "text/html":
                html_body = decoded
            elif ct == "text/plain":
                text_body = decoded
        return html_body or text_body
    payload = msg.get_payload(decode=True)
    return payload.decode(errors="ignore") if payload else ""
```

- [ ] **Step 5: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_email_verify.py -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/config.py fantoma/browser/email_verify.py tests/test_email_verify.py
git commit -m "feat: email_verify.py — IMAP polling, code/link extraction from emails"
```

---

### Task 2: Detect verification page in form_login.py

**Files:**
- Modify: `fantoma/browser/form_login.py`
- Test: `tests/test_email_verify.py`

**Depends on:** Task 1

- [ ] **Step 1: Write failing test**

Add to `tests/test_email_verify.py`:

```python
def test_detect_verification_page_code():
    from fantoma.browser.form_login import _detect_verification_page
    # Page asking for a code
    tree = "[0] textbox 'Verification code'\n[1] button 'Verify'"
    body_text = "We sent a code to your email. Enter it below."
    result = _detect_verification_page(tree, body_text)
    assert result == "code"


def test_detect_verification_page_link():
    from fantoma.browser.form_login import _detect_verification_page
    tree = "[0] heading 'Check your email'"
    body_text = "We sent you a link. Click it to verify your account."
    result = _detect_verification_page(tree, body_text)
    assert result == "link"


def test_detect_verification_page_none():
    from fantoma.browser.form_login import _detect_verification_page
    tree = "[0] textbox 'Email'\n[1] textbox 'Password'\n[2] button 'Log in'"
    body_text = "Welcome back! Log in to your account."
    result = _detect_verification_page(tree, body_text)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_email_verify.py::test_detect_verification_page_code -v`
Expected: FAIL — `_detect_verification_page` doesn't exist

- [ ] **Step 3: Add `_detect_verification_page` to form_login.py**

Add before `_get_element`:

```python
def _detect_verification_page(tree, body_text):
    """Detect if the current page is asking for email verification.

    Returns: "code" if asking for a verification code,
             "link" if telling user to check email for a link,
             None if this is a normal page.
    """
    tree_lower = tree.lower()
    body_lower = body_text.lower()
    combined = tree_lower + " " + body_lower

    # Code indicators: page has a single short text input + verification language
    code_signals = ["verification code", "verify code", "enter code",
                    "enter the code", "confirmation code", "security code",
                    "one-time code", "otp", "6-digit", "digit code"]
    if any(s in combined for s in code_signals):
        return "code"

    # Link indicators: page says "check your email" without a code input
    link_signals = ["check your email", "sent you a link", "click the link",
                    "verify your email", "confirmation email", "sent a link",
                    "open the email", "check your inbox"]
    if any(s in combined for s in link_signals):
        return "link"

    return None
```

- [ ] **Step 4: Call it in login() after submit**

In `form_login.py`, in the `login()` function, after the submit click and step_delay wait (around the `_looks_logged_in` check near end of loop), add verification detection:

Find the section:
```python
        # Check if we've left the login page
        new_url = page.url
        if _looks_logged_in(page, new_url):
```

Add BEFORE it:

```python
        # Check if we've landed on a verification page
        post_tree = dom_extractor.extract(page)
        try:
            post_body = page.inner_text("body")[:2000]
        except Exception:
            post_body = ""
        verification_type = _detect_verification_page(post_tree, post_body)
        if verification_type:
            log.info("Step %d: verification page detected (type=%s)", step + 1, verification_type)
            return {
                "success": False,
                "steps": step + 1,
                "url": page.url,
                "fields_filled": fields_filled,
                "verification_needed": verification_type,
            }
```

- [ ] **Step 5: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_email_verify.py -v`
Expected: All 13 tests PASS

- [ ] **Step 6: Run existing tests for regressions**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_form_login.py tests/test_form_memory.py tests/test_llm_labeller.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/form_login.py tests/test_email_verify.py
git commit -m "feat: detect verification pages after form submission"
```

---

### Task 3: Wire verification into agent.py (3 tiers)

**Files:**
- Modify: `fantoma/agent.py`
- Test: `tests/test_email_verify.py`

**Depends on:** Tasks 1, 2

- [ ] **Step 1: Write failing test**

Add to `tests/test_email_verify.py`:

```python
def test_agent_accepts_email_imap():
    from fantoma.agent import Agent
    from unittest.mock import patch

    with patch("fantoma.agent.BrowserEngine"):
        with patch("fantoma.agent.LLMClient"):
            agent = Agent(
                llm_url="http://localhost:8080/v1",
                email_imap={"host": "127.0.0.1", "port": 1143,
                            "user": "me@test.com", "password": "pass"},
            )
            assert agent.config.email.host == "127.0.0.1"
            assert agent.config.email.port == 1143


def test_agent_accepts_verification_callback():
    from fantoma.agent import Agent
    from unittest.mock import patch

    callback = lambda site, msg: "123456"

    with patch("fantoma.agent.BrowserEngine"):
        with patch("fantoma.agent.LLMClient"):
            agent = Agent(
                llm_url="http://localhost:8080/v1",
                verification_callback=callback,
            )
            assert agent._verification_callback is callback
```

- [ ] **Step 2: Add params to Agent.__init__**

In `fantoma/agent.py`, add to `__init__` signature:

```python
        browser: str = "camoufox",
        email_imap: dict = None,
        verification_callback: callable = None,
    ):
```

In the constructor body, after captcha config:

```python
        # Email verification config
        if email_imap:
            from fantoma.config import EmailConfig
            self.config.email = EmailConfig(
                host=email_imap.get("host", ""),
                port=email_imap.get("port", 993),
                user=email_imap.get("user", ""),
                password=email_imap.get("password", ""),
            )
        self._verification_callback = verification_callback
```

- [ ] **Step 3: Add verification handling after form_login in login()**

In the `login()` method, after `form_login` returns, check for `verification_needed`:

```python
            # Handle email verification if needed
            if result.get("verification_needed"):
                vtype = result["verification_needed"]
                log.info("Verification needed: %s", vtype)
                code = self._get_verification_code(vtype, domain)
                if code and vtype == "code":
                    # Type the code into the verification field
                    from fantoma.browser.actions import type_into
                    dom = AccessibilityExtractor(
                        max_elements=self.config.extraction.max_elements,
                    )
                    tree = dom.extract(browser.get_page())
                    elements = dom._last_interactive
                    # Find the code input field
                    for el in elements:
                        if el.get("role") in ("textbox", "input"):
                            handle = dom.get_element_by_index(browser.get_page(), el["index"])
                            if handle:
                                type_into(browser, handle, code)
                                log.info("Entered verification code")
                                browser.get_page().keyboard.press("Enter")
                                time.sleep(3)
                                result["success"] = True
                                result["verification_completed"] = True
                                break
                elif code and vtype == "link":
                    browser.navigate(code)
                    time.sleep(3)
                    result["success"] = True
                    result["verification_completed"] = True
```

- [ ] **Step 4: Add `_get_verification_code` method to Agent**

```python
    def _get_verification_code(self, vtype, domain):
        """Get verification code/link using the configured tier.

        Priority: IMAP → callback → terminal prompt → None
        """
        # Tier 1: IMAP
        if self.config.email.host:
            from fantoma.browser.email_verify import check_inbox
            result = check_inbox(self.config.email, domain)
            if result:
                log.info("IMAP verification: %s=%s", result["type"], result["value"][:30])
                return result["value"]

        # Tier 2: Callback
        if self._verification_callback:
            try:
                msg = f"Enter verification code from {domain}" if vtype == "code" else f"Enter verification link from {domain}"
                value = self._verification_callback(domain, msg)
                if value:
                    return value.strip()
            except Exception as e:
                log.warning("Verification callback failed: %s", e)

        # Tier 3: Terminal prompt (only in interactive mode)
        try:
            if vtype == "code":
                value = input(f"\nVerification code from {domain}: ")
            else:
                value = input(f"\nVerification link from {domain}: ")
            return value.strip() if value else None
        except (EOFError, OSError):
            # Not running in interactive mode
            log.info("No interactive terminal — verification cannot be completed")
            return None
```

- [ ] **Step 5: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_email_verify.py -v`
Expected: All 15 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -q --ignore=tests/live_reddit_test.py --ignore=tests/scenario_test_deepseek.py --ignore=tests/real_site_test.py --ignore=tests/real_signup_test.py --ignore=tests/full_signup_test.py`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py tests/test_email_verify.py
git commit -m "feat: 3-tier email verification — IMAP, callback, terminal prompt"
```

---

### Task 4: Version bump + final verification

**Files:**
- Modify: `fantoma/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -q --ignore=tests/live_reddit_test.py --ignore=tests/scenario_test_deepseek.py --ignore=tests/real_site_test.py --ignore=tests/real_signup_test.py --ignore=tests/full_signup_test.py`
Expected: All PASS (should be 140+)

- [ ] **Step 2: Bump version**

`fantoma/__init__.py`: `__version__ = "0.4.0"`
`pyproject.toml`: `version = "0.4.0"`

- [ ] **Step 3: Commit and tag**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/__init__.py pyproject.toml
git commit -m "chore: bump version to 0.4.0"
git tag v0.4.0
```

---

## Task Dependency Graph

```
Task 1 (email_verify.py + EmailConfig)
  ↓
Task 2 (detect verification page in form_login)
  ↓
Task 3 (wire into agent.py — 3 tiers)
  ↓
Task 4 (version bump)
```

All sequential — each builds on the previous.
