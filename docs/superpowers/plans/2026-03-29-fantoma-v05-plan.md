# Fantoma v0.5.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session persistence (encrypted cookies), a unified signup→verify→login pipeline, multi-action steps, paint-order DOM filtering, free search tools, message compaction, and sensitive data handling.

**Architecture:** Seven independent features built in dependency order. Session manager first (no dependencies), then login pipeline (depends on sessions), then the five agent upgrades (independent of each other). Each feature is test-driven.

**Tech Stack:** Python 3.10+, Playwright, Camoufox, cryptography (Fernet), pytest

**Spec:** `docs/superpowers/specs/2026-03-29-session-persistence-design.md`

---

### Task 1: Session Manager — `fantoma/session.py`

**Files:**
- Create: `fantoma/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Write failing tests for SessionManager**

```python
# tests/test_session.py
"""Tests for session persistence — save/load/delete/list encrypted browser state."""
import json
import os
import tempfile
import pytest
from fantoma.session import SessionManager


@pytest.fixture
def session_dir(tmp_path):
    return str(tmp_path / "sessions")


@pytest.fixture
def mgr(session_dir):
    return SessionManager(base_dir=session_dir)


def _fake_state():
    return {
        "cookies": [{"name": "sid", "value": "abc123", "domain": ".github.com", "path": "/"}],
        "origins": [{"origin": "https://github.com", "localStorage": [{"name": "token", "value": "xyz"}]}],
    }


class TestSaveAndLoad:
    def test_save_then_load_returns_state(self, mgr):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        loaded = mgr.load("github.com", "user@test.com")
        assert loaded is not None
        assert loaded["storage_state"]["cookies"][0]["value"] == "abc123"
        assert loaded["login_url"] == "https://github.com/login"

    def test_load_nonexistent_returns_none(self, mgr):
        assert mgr.load("github.com", "nobody@test.com") is None

    def test_save_overwrites_existing(self, mgr):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        new_state = {"cookies": [{"name": "sid", "value": "NEW", "domain": ".github.com", "path": "/"}], "origins": []}
        mgr.save("github.com", "user@test.com", new_state, "https://github.com/login")
        loaded = mgr.load("github.com", "user@test.com")
        assert loaded["storage_state"]["cookies"][0]["value"] == "NEW"

    def test_separate_accounts_separate_sessions(self, mgr):
        mgr.save("github.com", "a@test.com", _fake_state(), "https://github.com/login")
        mgr.save("github.com", "b@test.com", {"cookies": [], "origins": []}, "https://github.com/login")
        a = mgr.load("github.com", "a@test.com")
        b = mgr.load("github.com", "b@test.com")
        assert len(a["storage_state"]["cookies"]) == 1
        assert len(b["storage_state"]["cookies"]) == 0


class TestDelete:
    def test_delete_removes_session(self, mgr):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        mgr.delete("github.com", "user@test.com")
        assert mgr.load("github.com", "user@test.com") is None

    def test_delete_nonexistent_no_error(self, mgr):
        mgr.delete("github.com", "nobody@test.com")  # should not raise


class TestList:
    def test_list_all(self, mgr):
        mgr.save("github.com", "a@test.com", _fake_state(), "https://github.com/login")
        mgr.save("render.com", "a@test.com", _fake_state(), "https://render.com/login")
        sessions = mgr.list()
        assert len(sessions) == 2

    def test_list_filtered_by_domain(self, mgr):
        mgr.save("github.com", "a@test.com", _fake_state(), "https://github.com/login")
        mgr.save("render.com", "a@test.com", _fake_state(), "https://render.com/login")
        sessions = mgr.list(domain="github.com")
        assert len(sessions) == 1
        assert sessions[0]["domain"] == "github.com"


class TestEncryption:
    def test_file_is_not_plaintext_json(self, mgr, session_dir):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        # Find the session file
        files = [f for f in os.listdir(session_dir) if f.endswith(".enc")]
        assert len(files) == 1
        raw = open(os.path.join(session_dir, files[0]), "rb").read()
        # Should not be valid JSON (it's encrypted)
        with pytest.raises(Exception):
            json.loads(raw)

    def test_corrupted_file_returns_none(self, mgr, session_dir):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        files = [f for f in os.listdir(session_dir) if f.endswith(".enc")]
        # Corrupt the file
        with open(os.path.join(session_dir, files[0]), "wb") as f:
            f.write(b"corrupted data")
        assert mgr.load("github.com", "user@test.com") is None

    def test_key_file_created_with_restricted_perms(self, mgr, session_dir):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        key_path = os.path.join(session_dir, ".key")
        assert os.path.exists(key_path)
        stat = os.stat(key_path)
        assert oct(stat.st_mode)[-3:] == "600"


class TestAtomicWrite:
    def test_no_tmp_files_left_after_save(self, mgr, session_dir):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        files = os.listdir(session_dir)
        assert not any(f.endswith(".tmp") for f in files)


class TestPlaintextFallback:
    def test_plaintext_when_no_cryptography(self, session_dir, monkeypatch):
        mgr = SessionManager(base_dir=session_dir)
        monkeypatch.setattr("fantoma.session._has_cryptography", False)
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        loaded = mgr.load("github.com", "user@test.com")
        assert loaded is not None
        assert loaded["storage_state"]["cookies"][0]["value"] == "abc123"
        # File should be plaintext JSON
        files = [f for f in os.listdir(session_dir) if not f.startswith(".")]
        raw = open(os.path.join(session_dir, files[0]), "r").read()
        data = json.loads(raw)
        assert "storage_state" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fantoma.session'`

- [ ] **Step 3: Implement SessionManager**

```python
# fantoma/session.py
"""Session persistence — saves browser state (cookies + localStorage) to encrypted files."""
import json
import logging
import os
import tempfile
import time

log = logging.getLogger("fantoma.session")

try:
    from cryptography.fernet import Fernet
    _has_cryptography = True
except ImportError:
    _has_cryptography = False

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "fantoma", "sessions")


class SessionManager:
    """Persist browser sessions per domain + account. Encrypted at rest."""

    def __init__(self, base_dir: str = DEFAULT_DIR):
        self._dir = base_dir
        self._fernet = None

    def _ensure_dir(self):
        os.makedirs(self._dir, exist_ok=True)

    def _key_path(self) -> str:
        return os.path.join(self._dir, ".key")

    def _get_fernet(self):
        """Load or create encryption key. Returns Fernet instance or None."""
        if not _has_cryptography:
            return None
        if self._fernet:
            return self._fernet
        self._ensure_dir()
        key_path = self._key_path()
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
            # Ensure permissions even if file already existed (O_TRUNC doesn't reset perms)
            os.chmod(key_path, 0o600)
        self._fernet = Fernet(key)
        return self._fernet

    def _filename(self, domain: str, account: str) -> str:
        safe_domain = domain.replace("/", "_").replace(":", "_")
        safe_account = account.replace("/", "_").replace(":", "_")
        ext = ".enc" if _has_cryptography else ".json"
        return f"{safe_domain}--{safe_account}{ext}"

    def _filepath(self, domain: str, account: str) -> str:
        return os.path.join(self._dir, self._filename(domain, account))

    def save(self, domain: str, account: str, storage_state: dict, login_url: str):
        """Save browser state to disk. Atomic write (temp file → rename)."""
        self._ensure_dir()
        data = {
            "domain": domain,
            "account": account,
            "storage_state": storage_state,
            "login_url": login_url,
            "saved_at": time.time(),
        }
        payload = json.dumps(data).encode()

        fernet = self._get_fernet()
        if fernet:
            payload = fernet.encrypt(payload)

        filepath = self._filepath(domain, account)
        tmp_path = filepath + ".tmp"
        try:
            with open(tmp_path, "wb" if fernet else "w") as f:
                if fernet:
                    f.write(payload)
                else:
                    f.write(payload.decode())
            os.replace(tmp_path, filepath)
            log.info("Session saved: %s (%s)", domain, account[:20])
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def load(self, domain: str, account: str) -> dict | None:
        """Load saved session. Returns None if missing, corrupted, or decryption fails."""
        filepath = self._filepath(domain, account)
        if not os.path.exists(filepath):
            return None
        try:
            fernet = self._get_fernet()
            if fernet:
                with open(filepath, "rb") as f:
                    encrypted = f.read()
                decrypted = fernet.decrypt(encrypted)
                return json.loads(decrypted)
            else:
                with open(filepath, "r") as f:
                    return json.loads(f.read())
        except Exception as e:
            log.warning("Failed to load session %s/%s: %s", domain, account, e)
            return None

    def delete(self, domain: str, account: str):
        """Remove a saved session."""
        filepath = self._filepath(domain, account)
        if os.path.exists(filepath):
            os.unlink(filepath)
            log.info("Session deleted: %s (%s)", domain, account[:20])

    def list(self, domain: str = None) -> list[dict]:
        """List saved sessions. Filter by domain if provided."""
        if not os.path.exists(self._dir):
            return []
        results = []
        for fname in os.listdir(self._dir):
            if fname.startswith(".") or fname.endswith(".tmp"):
                continue
            if not (fname.endswith(".enc") or fname.endswith(".json")):
                continue
            parts = fname.rsplit(".", 1)[0].split("--", 1)
            if len(parts) != 2:
                continue
            d, a = parts
            if domain and d != domain.replace("/", "_").replace(":", "_"):
                continue
            results.append({"domain": d, "account": a, "file": fname})
        return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_session.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/session.py tests/test_session.py
git commit -m "feat: session manager — encrypted cookie/localStorage persistence"
```

---

### Task 2: BrowserEngine — Storage State Support

**Files:**
- Modify: `fantoma/browser/engine.py:316-332` (get_cookies, inject_cookies)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_engine_storage.py
"""Tests for BrowserEngine storage state save/load."""
import pytest


def test_get_storage_state_returns_dict_with_cookies_and_origins():
    """Verify get_storage_state returns Playwright-format dict."""
    from fantoma.browser.engine import BrowserEngine
    assert hasattr(BrowserEngine, "get_storage_state")


def test_load_storage_state_accepts_dict():
    """Verify load_storage_state method exists and accepts a dict."""
    from fantoma.browser.engine import BrowserEngine
    assert hasattr(BrowserEngine, "load_storage_state")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_engine_storage.py -v`
Expected: FAIL — `AssertionError` (methods don't exist yet)

- [ ] **Step 3: Add `get_storage_state()` and `load_storage_state()` to BrowserEngine**

Add these methods to `fantoma/browser/engine.py` after the existing `inject_cookies` method (after line 332):

```python
def get_storage_state(self) -> dict:
    """Get full browser state — cookies + localStorage + sessionStorage.

    Returns Playwright's storageState format:
    {"cookies": [...], "origins": [{"origin": "https://...", "localStorage": [...]}]}
    """
    ctx = self._context if self._context else (
        self._page.context if self._page else None
    )
    if not ctx:
        return {"cookies": [], "origins": []}

    cookies = ctx.cookies()

    # Extract localStorage per origin
    origins = []
    if self._page:
        try:
            storage = self._page.evaluate("""() => {
                const items = [];
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    items.push({name: key, value: localStorage.getItem(key)});
                }
                return {origin: window.location.origin, localStorage: items};
            }""")
            if storage and storage["localStorage"]:
                origins.append(storage)
        except Exception:
            pass

    return {"cookies": cookies, "origins": origins}

def load_storage_state(self, state: dict):
    """Load full browser state — cookies + localStorage.

    Accepts Playwright's storageState format. Cookies are injected via
    context API. localStorage is restored via page.evaluate, scoped
    per origin to prevent cross-site pollution.
    """
    ctx = self._context if self._context else (
        self._page.context if self._page else None
    )
    if not ctx:
        return

    cookies = state.get("cookies", [])
    if cookies:
        ctx.add_cookies(cookies)

    # Restore localStorage per origin
    origins = state.get("origins", [])
    if origins and self._page:
        for origin_data in origins:
            items = origin_data.get("localStorage", [])
            if not items:
                continue
            # Only inject localStorage for the current page's origin
            try:
                current_origin = self._page.evaluate("window.location.origin")
                if current_origin == origin_data.get("origin"):
                    for item in items:
                        self._page.evaluate(
                            "(args) => localStorage.setItem(args[0], args[1])",
                            [item["name"], item["value"]]
                        )
            except Exception:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_engine_storage.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ --tb=short -q`
Expected: 155+ passed, 0 failed

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/engine.py tests/test_engine_storage.py
git commit -m "feat: BrowserEngine storage state — get/load cookies + localStorage"
```

---

### Task 3: Unified Login Pipeline — Rewrite `agent.login()`

**Files:**
- Modify: `fantoma/agent.py:183-331` (login method + _get_verification)
- Modify: `fantoma/browser/form_login.py:743-774` (_looks_logged_in)

- [ ] **Step 1: Update `_looks_logged_in` with session expired signals**

In `fantoma/browser/form_login.py`, replace the `_looks_logged_in` function (lines 743-774):

```python
def _looks_logged_in(page, url, start_url=""):
    """Quick heuristic: are we past the login page?"""
    url_lower = url.lower()

    # Strong signal: URL changed away from the login page
    if start_url and url_lower != start_url.lower():
        login_indicators = ["/login", "/signin", "/sign-in", "/sign_in",
                            "/flow/login", "/authenticate", "/auth/login",
                            "/signup", "/register", "/join"]
        start_lower = start_url.lower()
        was_on_login = any(ind in start_lower for ind in login_indicators)
        still_on_login = any(ind in url_lower for ind in login_indicators)
        if was_on_login and not still_on_login:
            return True

    # Check for common logged-in indicators in body text
    try:
        body = page.inner_text("body")[:500].lower()
        logged_out_indicators = ["sign in", "log in", "create account",
                                  "forgot password", "register"]
        logged_in_indicators = ["dashboard", "feed", "home", "welcome",
                                 "account", "profile", "settings", "logout",
                                 "sign out", "log out", "products",
                                 "inventory", "my account"]
        session_expired_indicators = ["session expired", "session has expired",
                                       "please log in again", "please sign in again",
                                       "been logged out", "been signed out",
                                       "login again", "sign in again"]

        # Session expired is a clear "not logged in" signal
        if any(ind in body for ind in session_expired_indicators):
            return False

        out_score = sum(1 for ind in logged_out_indicators if ind in body)
        in_score = sum(1 for ind in logged_in_indicators if ind in body)

        return in_score > out_score
    except Exception:
        return False
```

- [ ] **Step 2: Rewrite `agent.login()` with unified pipeline**

Replace the `login` method and `_get_verification` method in `fantoma/agent.py` (lines 183-360):

```python
def login(self, url: str, email: str = "", username: str = "", password: str = "",
          first_name: str = "", last_name: str = "") -> AgentResult:
    """Log into a site. Tries saved session first, falls back to full login.

    Full pipeline: saved cookies → form fill → CAPTCHA → submit →
    email verification → post-verify login-back → save session.

    Args:
        url: Login/signup page URL
        email: Email address
        username: Username
        password: Password
        first_name: First name (signup forms)
        last_name: Last name (signup forms)

    Returns:
        AgentResult with success status and login details.
    """
    from fantoma.browser.form_login import login as form_login, _looks_logged_in
    from fantoma.browser.form_memory import FormMemory
    from fantoma.dom.accessibility import AccessibilityExtractor
    from fantoma.session import SessionManager
    from urllib.parse import urlparse
    from uuid import uuid4

    account = email or username or "default"
    domain = urlparse(url).netloc
    log.info("Login: %s (account=%s)", url, account[:20])

    sessions = SessionManager()
    memory = FormMemory()
    visit_id = uuid4().hex

    # ── Step 1: Try saved session ──────────────────────────
    saved = sessions.load(domain, account)
    if saved:
        log.info("Found saved session for %s — validating", domain)
        try:
            browser = self._make_browser()
            browser.start()
            browser.load_storage_state(saved["storage_state"])
            browser.navigate(saved.get("login_url", url))
            time.sleep(3)
            page = browser.get_page()
            if _looks_logged_in(page, page.url, url):
                log.info("Saved session valid — already logged in")
                browser.stop()
                memory.close()
                return AgentResult(success=True, data={"url": page.url, "from_session": True}, steps_taken=0)
            log.info("Saved session expired — proceeding with full login")
            sessions.delete(domain, account)
            browser.stop()
        except Exception as e:
            log.warning("Session validation failed: %s", e)
            sessions.delete(domain, account)
            try:
                browser.stop()
            except Exception:
                pass

    # ── Step 2-4: Full login flow ──────────────────────────
    try:
        browser = self._make_browser()
        browser.start()
    except Exception as e:
        log.error("Browser start failed: %s", e)
        memory.close()
        return AgentResult(success=False, error=f"Browser start failed: {e}")

    try:
        browser.navigate(url)
        time.sleep(3)

        dom = AccessibilityExtractor(
            max_elements=self.config.extraction.max_elements,
            max_headings=self.config.extraction.max_headings,
        )

        result = form_login(
            browser=browser,
            dom_extractor=dom,
            email=email,
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            memory=memory,
            visit_id=visit_id,
            config=self.config,
            llm=self._llm,
        )

        page = browser.get_page()

        # ── Step 4: Check if already logged in ─────────────
        if result.get("success") and _looks_logged_in(page, page.url, url):
            log.info("Login successful after form fill")
            self._save_session(sessions, browser, domain, account, url)
            memory.record_visit(domain, True)
            memory.close()
            browser.stop()
            return AgentResult(success=True, data=result, steps_taken=result.get("steps", 0))

        # ── Step 5: Email verification ─────────────────────
        if result.get("verification_needed"):
            vtype = result["verification_needed"]
            log.info("Verification needed: %s", vtype)
            value = self._get_verification(vtype, domain)

            if value and vtype == "code":
                self._enter_verification_code(browser, dom, value)
            elif value and vtype == "link":
                browser.navigate(value)
                time.sleep(5)

            # ── Step 5b: Check verification accepted ───────
            if value:
                time.sleep(3)
                page = browser.get_page()
                post_tree = dom.extract(page)
                try:
                    post_body = page.inner_text("body")[:2000].lower()
                except Exception:
                    post_body = ""

                # Check for error signals
                error_signals = ["invalid code", "incorrect code", "expired",
                                 "try again", "wrong code", "invalid link"]
                if any(s in post_body for s in error_signals):
                    log.warning("Verification rejected — error detected on page")
                    memory.close()
                    browser.stop()
                    return AgentResult(success=False, data=result,
                                       steps_taken=result.get("steps", 0),
                                       error="Verification code/link rejected")

        # ── Step 6: Post-verification check ────────────────
        page = browser.get_page()
        if _looks_logged_in(page, page.url, url):
            log.info("Logged in after verification")
            self._save_session(sessions, browser, domain, account, url)
            memory.record_visit(domain, True)
            memory.close()
            browser.stop()
            return AgentResult(success=True, data=result, steps_taken=result.get("steps", 0))

        # Not logged in after verification — try logging in with same credentials
        log.info("Not logged in after verification — attempting login-back")
        browser.navigate(url)
        time.sleep(3)

        login_result = form_login(
            browser=browser,
            dom_extractor=dom,
            email=email,
            username=username,
            password=password,
            memory=memory,
            visit_id=visit_id + "_loginback",
            config=self.config,
            llm=self._llm,
        )

        # ── Step 7: Final check ────────────────────────────
        page = browser.get_page()
        if _looks_logged_in(page, page.url, url):
            log.info("Login-back successful")
            self._save_session(sessions, browser, domain, account, url)
            memory.record_visit(domain, True)
            memory.close()
            browser.stop()
            return AgentResult(success=True, data=login_result, steps_taken=login_result.get("steps", 0))

        log.warning("Login failed — not logged in after all attempts")
        memory.record_visit(domain, False)
        memory.close()
        browser.stop()
        return AgentResult(success=False, data=login_result,
                           steps_taken=login_result.get("steps", 0),
                           error="Login not confirmed after verification + login-back")

    except Exception as e:
        log.error("Login failed: %s", e)
        return AgentResult(success=False, error=str(e))
    finally:
        try:
            browser.stop()
        except Exception:
            pass
        memory.close()

def _make_browser(self) -> BrowserEngine:
    """Create a BrowserEngine with current config."""
    return BrowserEngine(
        headless=self.config.browser.headless,
        profile_dir=self.config.browser.profile_dir,
        proxy=self._proxy,
        trace=self.config.browser.trace,
        browser_engine=self.config.browser.browser_engine,
    )

def _save_session(self, sessions, browser, domain, account, login_url):
    """Save browser state to session manager."""
    try:
        state = browser.get_storage_state()
        sessions.save(domain, account, state, login_url)
    except Exception as e:
        log.warning("Failed to save session: %s", e)

def _enter_verification_code(self, browser, dom, code):
    """Find the verification code input on the page and type the code."""
    from fantoma.browser.actions import type_into
    page = browser.get_page()

    # Strategy 1: ARIA tree textbox
    dom.extract(page)
    for el in dom._last_interactive:
        if el.get("role") in ("textbox", "input"):
            handle = dom.get_element_by_index(page, el["index"])
            if handle:
                type_into(browser, handle, code)
                page.keyboard.press("Enter")
                log.info("Entered verification code via ARIA textbox")
                return

    # Strategy 2: raw DOM selectors for code/OTP inputs
    selectors = [
        'input[name*="code"]', 'input[name*="otp"]', 'input[name*="token"]',
        'input[name*="verify"]', 'input[placeholder*="code"]',
        'input[placeholder*="Code"]', 'input[autocomplete*="one-time"]',
        'input[type="text"]', 'input[type="number"]', 'input[type="tel"]',
    ]
    for sel in selectors:
        try:
            handle = page.query_selector(sel)
            if handle and handle.is_visible():
                type_into(browser, handle, code)
                page.keyboard.press("Enter")
                log.info("Entered verification code via selector: %s", sel)
                return
        except Exception:
            continue

    # Strategy 3: any visible empty input
    try:
        inputs = page.query_selector_all('input[type="text"], input[type="number"], input[type="tel"], input:not([type])')
        for inp in inputs:
            if inp.is_visible() and inp.get_attribute("value") in ("", None):
                type_into(browser, inp, code)
                page.keyboard.press("Enter")
                log.info("Entered verification code via fallback empty input")
                return
    except Exception:
        pass

    log.warning("Could not find verification code input on page")

def _get_verification(self, vtype, domain):
    """Get verification code/link. Priority: IMAP → callback → terminal → None."""
    # Tier 1: IMAP
    if self.config.email.host:
        from fantoma.browser.email_verify import check_inbox
        result = check_inbox(self.config.email, domain, prefer=vtype)
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
        log.info("No interactive terminal — verification cannot be completed")
        return None
```

- [ ] **Step 3: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ --tb=short -q`
Expected: 155+ passed, 0 failed (no existing tests call agent.login() with a real browser)

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py fantoma/browser/form_login.py
git commit -m "feat: unified login pipeline — session-first, verify, login-back"
```

---

### Task 4: Multi-Action Steps

**Files:**
- Modify: `fantoma/llm/prompts.py:52-72` (REACTIVE_SYSTEM)
- Modify: `fantoma/action_parser.py` (add parse_actions)
- Modify: `fantoma/executor.py:186-350` (execute_reactive)
- Create: `tests/test_multi_action.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_multi_action.py
"""Tests for multi-action parsing and page-change guards."""
from fantoma.action_parser import parse_actions, normalize_action


class TestParseActions:
    def test_single_action(self):
        actions = parse_actions('CLICK [3]')
        assert actions == ["CLICK [3]"]

    def test_multiple_actions_newline(self):
        actions = parse_actions('CLICK [3]\nTYPE [5] "hello"\nPRESS Enter')
        assert actions == ["CLICK [3]", 'TYPE [5] "hello"', "PRESS Enter"]

    def test_max_five_actions(self):
        raw = "\n".join([f"CLICK [{i}]" for i in range(10)])
        actions = parse_actions(raw)
        assert len(actions) == 5

    def test_max_actions_override(self):
        raw = "CLICK [1]\nCLICK [2]\nCLICK [3]"
        actions = parse_actions(raw, max_actions=1)
        assert len(actions) == 1

    def test_done_terminates(self):
        actions = parse_actions('CLICK [3]\nDONE')
        assert actions == ["CLICK [3]", "DONE"]

    def test_navigate_terminates_sequence(self):
        actions = parse_actions('NAVIGATE https://example.com\nCLICK [3]')
        # NAVIGATE should be kept, but CLICK after it should be dropped
        assert actions == ["NAVIGATE https://example.com"]

    def test_empty_string(self):
        actions = parse_actions("")
        assert actions == []

    def test_strips_thinking_text(self):
        raw = "I should click the button\nCLICK [3]\nThen type\nTYPE [5] \"hello\""
        actions = parse_actions(raw)
        assert actions == ["CLICK [3]", 'TYPE [5] "hello"']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_multi_action.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_actions'`

- [ ] **Step 3: Add `parse_actions` to action_parser.py**

Add at the end of `fantoma/action_parser.py`:

```python
# Actions that terminate the sequence — no further actions should run after these
SEQUENCE_TERMINATORS = {"NAVIGATE", "DONE"}

MAX_ACTIONS_PER_STEP = 5  # Override to 1 for small/local models


def parse_actions(raw_response: str, max_actions: int = MAX_ACTIONS_PER_STEP) -> list[str]:
    """Parse multiple actions from one LLM response.

    Returns up to MAX_ACTIONS_PER_STEP normalized actions.
    Strips non-action text (thinking, explanations).
    Terminates early on NAVIGATE or DONE.
    """
    if not raw_response or not raw_response.strip():
        return []

    actions = []
    for line in raw_response.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Try to extract a valid action from this line
        normalized = normalize_action(line)
        if not normalized or normalized == line:
            # normalize_action returns the raw text if it can't parse it
            # Check if it matches any known action pattern
            matched = False
            for pattern in EXTRACT_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    matched = True
                    break
            if not matched:
                continue
        actions.append(normalized)

        # Check for sequence terminators
        verb = normalized.strip().split()[0].upper() if normalized.strip() else ""
        if verb in SEQUENCE_TERMINATORS:
            break

        if len(actions) >= max_actions:
            break

    return actions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_multi_action.py -v`
Expected: All PASS

- [ ] **Step 5: Update REACTIVE_SYSTEM prompt**

Replace REACTIVE_SYSTEM in `fantoma/llm/prompts.py` (lines 52-72):

```python
REACTIVE_SYSTEM = """\
You control a browser. Your job is to COMPLETE the task, not just observe the page.

Pick 1-5 actions from this list (one per line):
CLICK [number]
TYPE [number] "text"
SELECT [number] "option"
SCROLL down
SCROLL up
NAVIGATE https://example.com
PRESS Enter
SEARCH_PAGE "text to find"
FIND "css selector"
DONE

Rules:
- Match [number] to the element list shown after the task.
- You may return multiple actions (one per line) to execute in sequence.
- To fill a form: TYPE each field, then CLICK submit — all in one response.
- After typing in a search field, add PRESS Enter.
- NAVIGATE and DONE end the sequence — any actions after them are ignored.
- Only say DONE when the task is fully COMPLETED (form submitted, data extracted, action taken).
- Do NOT say DONE just because you can see a form or page — you must interact with it first.
- SEARCH_PAGE finds text on the current page (like Ctrl+F). Free, no scrolling needed.
- FIND runs a CSS selector query and returns matching elements. Free.
- Reply with ONLY action lines, nothing else.\
"""
```

- [ ] **Step 6: Update `execute_reactive` in executor.py to handle multi-action**

In `fantoma/executor.py`, update the `execute_reactive` method. Replace the LLM call and action execution section (lines 254-342) with:

```python
            # Ask LLM for next action(s)
            failed = self.memory.get_failed_actions(dom_hash)
            user_msg = f"Task: {task}\n\n{dom_text}"
            if failed:
                user_msg += f"\n\nFailed (don't repeat): {', '.join(failed)}"

            raw = self.llm.chat(
                [{"role": "system", "content": REACTIVE_SYSTEM},
                 {"role": "user", "content": user_msg}],
                max_tokens=300,
            )
            raw = (raw or "").strip()
            if not raw:
                log.warning("Step %d: LLM returned empty action", step_num)
                continue

            from fantoma.action_parser import parse_actions
            actions = parse_actions(raw)
            if not actions:
                log.warning("Step %d: could not parse actions from: %s", step_num, raw[:80])
                continue

            # Execute actions sequentially with page-change guards
            pre_url = self.browser.get_url()
            for action_idx, action in enumerate(actions):
                log.info("Step %d.%d: %s", step_num, action_idx + 1, action[:80])

                # DONE signal
                if action.upper().startswith("DONE"):
                    log.info("LLM signalled DONE after %d steps", step_num)
                    return AgentResult(
                        success=True, data=self._extract_result(task, dom_text),
                        steps_taken=step_num, steps_detail=steps_detail,
                        escalations=self.escalation.total_escalations,
                    )

                # Execute the action
                before = self.diff.snapshot(page)
                self._total_actions += 1
                executed = execute_action(action, self.browser, self.dom)
                action_verb = action.strip().split()[0].upper() if action.strip() else ""

                # SCROLL/WAIT: auto-succeed
                if action_verb in ("SCROLL", "WAIT"):
                    self.memory.record(action, dom_hash, "success", True, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                    self._consecutive_failures = 0
                    continue

                # TYPE: check if worked, handle form assist
                if action_verb == "TYPE":
                    if executed:
                        type_re = re.match(r'TYPE\s*\[?\d+\]?\s*["\'](.+?)["\']', action, re.IGNORECASE)
                        typed_text = type_re.group(1) if type_re else ""
                        assist = form_after_type(page, typed_text, timeout=self.config.timeouts.autocomplete)
                        if assist:
                            log.info("Step %d: form assist — %s", step_num, assist)
                        self.memory.record(action, dom_hash, "typed", True, step_num)
                        steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                        self._consecutive_failures = 0
                    else:
                        self.memory.record(action, dom_hash, dom_hash, False, step_num)
                        steps_detail.append({"step": step_num, "action": action, "success": False, "url": self.browser.get_url()})
                        self._consecutive_failures += 1
                        self._maybe_escalate()
                    continue

                # NAVIGATE: wait for load
                if action_verb == "NAVIGATE":
                    wait_for_navigation(self.browser, timeout=10000)
                    self.memory.record(action, dom_hash, "navigated", True, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                    self._consecutive_failures = 0
                    break  # NAVIGATE terminates the sequence

                # SEARCH_PAGE / FIND: free tools, no page change
                if action_verb in ("SEARCH_PAGE", "FIND"):
                    self.memory.record(action, dom_hash, "success", True, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": True, "url": self.browser.get_url()})
                    continue

                if not executed:
                    self.memory.record(action, dom_hash, dom_hash, False, step_num)
                    steps_detail.append({"step": step_num, "action": action, "success": False, "url": self.browser.get_url()})
                    self._consecutive_failures += 1
                    self._maybe_escalate()
                    continue

                # CLICK and others: check page change
                changed = self._check_page_change(page, before, dom_hash, action, step_num)
                steps_detail.append({"step": step_num, "action": action, "success": changed, "url": self.browser.get_url()})

                if changed:
                    self._consecutive_failures = 0
                    # Page changed — abort remaining actions in this batch
                    if self.browser.get_url() != pre_url:
                        log.info("Step %d: URL changed — aborting remaining actions", step_num)
                        break
                else:
                    self._consecutive_failures += 1
                    self._maybe_escalate()
```

- [ ] **Step 7: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ --tb=short -q`
Expected: All passed

- [ ] **Step 8: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/action_parser.py fantoma/executor.py fantoma/llm/prompts.py tests/test_multi_action.py
git commit -m "feat: multi-action steps — up to 5 actions per LLM call with page-change guards"
```

---

### Task 5: Paint-Order DOM Filtering

**Files:**
- Modify: `fantoma/dom/accessibility.py:268-292` (AccessibilityExtractor.extract)
- Create: `tests/test_paint_order.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_paint_order.py
"""Tests for paint-order filtering — removes elements hidden behind overlays."""
from fantoma.dom.accessibility import AccessibilityExtractor


def test_filter_occluded_method_exists():
    ext = AccessibilityExtractor()
    assert hasattr(ext, "_filter_occluded")


def test_filter_occluded_removes_hidden_elements():
    """Unit test with mock page — occluded elements get removed."""
    ext = AccessibilityExtractor()
    # Mock elements: index 0 visible, index 1 occluded
    elements = [
        {"index": 0, "role": "button", "name": "Submit"},
        {"index": 1, "role": "button", "name": "Hidden Behind Modal"},
    ]
    # Mock page that says element 1 is occluded
    class MockPage:
        def evaluate(self, js, arg=None):
            if arg is not None:
                # _filter_occluded passes element info
                idx = arg.get("index", 0) if isinstance(arg, dict) else 0
                return idx != 1  # element 1 is occluded
            return None
    filtered = ext._filter_occluded(MockPage(), elements)
    assert len(filtered) == 1
    assert filtered[0]["name"] == "Submit"


def test_filter_occluded_keeps_all_when_no_occlusion():
    ext = AccessibilityExtractor()
    elements = [
        {"index": 0, "role": "button", "name": "A"},
        {"index": 1, "role": "link", "name": "B"},
    ]
    class MockPage:
        def evaluate(self, js, arg=None):
            return True  # all visible
    filtered = ext._filter_occluded(MockPage(), elements)
    assert len(filtered) == 2


def test_filter_occluded_handles_js_error():
    ext = AccessibilityExtractor()
    elements = [
        {"index": 0, "role": "button", "name": "A"},
    ]
    class MockPage:
        def evaluate(self, js, arg=None):
            raise Exception("JS error")
    # Should return elements unchanged on error
    filtered = ext._filter_occluded(MockPage(), elements)
    assert len(filtered) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_paint_order.py -v`
Expected: FAIL — `AttributeError: 'AccessibilityExtractor' object has no attribute '_filter_occluded'`

- [ ] **Step 3: Implement paint-order filtering**

Add `_filter_occluded` method to `AccessibilityExtractor` in `fantoma/dom/accessibility.py`, after the `extract` method (after line 292):

```python
def _filter_occluded(self, page, elements: list[dict]) -> list[dict]:
    """Remove elements that are visually hidden behind other elements.

    For each element, gets its bounding box centre and calls
    document.elementFromPoint(). If the topmost element at that point
    is not the target (or a child of it), the element is occluded.
    """
    if not elements:
        return elements

    try:
        # Run a single JS call that checks all elements at once
        visible_indices = page.evaluate("""(indices) => {
            const visible = [];
            for (const idx of indices) {
                const selector = `[data-fantoma-idx="${idx}"]`;
                // Use get_by_role results — find by ARIA role + name
                // Fall back to checking all interactive elements
                const allInteractive = document.querySelectorAll(
                    'button, a, input, select, textarea, [role="button"], ' +
                    '[role="link"], [role="textbox"], [role="combobox"], ' +
                    '[role="checkbox"], [role="radio"], [role="switch"], ' +
                    '[role="tab"], [role="menuitem"], [role="searchbox"]'
                );
                if (idx >= allInteractive.length) {
                    visible.push(idx);  // can't check, assume visible
                    continue;
                }
                const el = allInteractive[idx];
                if (!el) { visible.push(idx); continue; }
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;  // zero-size = hidden
                const cx = rect.left + rect.width / 2;
                const cy = rect.top + rect.height / 2;
                // Skip if centre is outside viewport
                if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) {
                    visible.push(idx);  // outside viewport — can't check, assume visible
                    continue;
                }
                const topEl = document.elementFromPoint(cx, cy);
                if (!topEl) { visible.push(idx); continue; }  // null = can't determine, keep it
                // Element is visible if it's the top element or contains it
                if (el === topEl || el.contains(topEl) || topEl.contains(el)) {
                    visible.push(idx);
                }
            }
            return visible;
        }""", [el.get("index", i) for i, el in enumerate(elements)])

        if visible_indices is None:
            return elements

        visible_set = set(visible_indices)
        filtered = [el for i, el in enumerate(elements)
                     if el.get("index", i) in visible_set]

        removed = len(elements) - len(filtered)
        if removed > 0:
            log.info("Paint-order filter: removed %d occluded elements (kept %d)",
                     removed, len(filtered))
        return filtered

    except Exception as e:
        log.debug("Paint-order filtering failed: %s — keeping all elements", e)
        return elements
```

Then update the `extract` method to call `_filter_occluded`. In the `extract` method, after line 291 (`self._last_interactive = self._parse_interactive_from_output(result)`), add:

```python
        # Filter out elements hidden behind overlays/modals
        if self._last_interactive:
            self._last_interactive = self._filter_occluded(page, self._last_interactive)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_paint_order.py -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ --tb=short -q`
Expected: All passed

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/dom/accessibility.py tests/test_paint_order.py
git commit -m "feat: paint-order DOM filtering — removes occluded elements"
```

---

### Task 6: Free Search Tools — SEARCH_PAGE and FIND

**Files:**
- Modify: `fantoma/browser/actions.py` (add search_page, find_elements)
- Modify: `fantoma/action_parser.py` (register new actions)
- Create: `tests/test_search_tools.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_search_tools.py
"""Tests for SEARCH_PAGE and FIND actions — free JS-based search tools."""
import re
from fantoma.action_parser import PATTERNS, execute_action


def test_search_page_pattern_exists():
    assert "search_page" in PATTERNS


def test_find_pattern_exists():
    assert "find" in PATTERNS


def test_search_page_pattern_matches():
    m = PATTERNS["search_page"].match('SEARCH_PAGE "login button"')
    assert m is not None
    assert m.group(1) == "login button"


def test_find_pattern_matches():
    m = PATTERNS["find"].match('FIND "input[type=email]"')
    assert m is not None
    assert m.group(1) == "input[type=email]"


class TestSearchPageAction:
    def test_search_page_returns_results(self):
        """Mock page with text content — search should find matches."""
        from fantoma.browser.actions import search_page

        class MockPage:
            def evaluate(self, js, *args):
                # Simulate finding 2 matches for "hello"
                return [
                    {"text": "hello world", "index": 0},
                    {"text": "say hello", "index": 1},
                ]
        results = search_page(MockPage(), "hello")
        assert len(results) == 2

    def test_search_page_no_results(self):
        from fantoma.browser.actions import search_page

        class MockPage:
            def evaluate(self, js, *args):
                return []
        results = search_page(MockPage(), "nonexistent")
        assert results == []


class TestFindElements:
    def test_find_elements_returns_matches(self):
        from fantoma.browser.actions import find_elements

        class MockPage:
            def evaluate(self, js, *args):
                return [
                    {"tag": "input", "type": "email", "name": "user_email", "text": ""},
                ]
        results = find_elements(MockPage(), "input[type=email]")
        assert len(results) == 1
        assert results[0]["type"] == "email"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_search_tools.py -v`
Expected: FAIL — KeyError or ImportError

- [ ] **Step 3: Implement search_page and find_elements in actions.py**

Add to the end of `fantoma/browser/actions.py`:

```python
def search_page(page, query: str) -> list[dict]:
    """Find all visible text matches on the page. Like Ctrl+F.

    Returns list of dicts with 'text' (surrounding context) and 'index' (match number).
    Free — no LLM cost.
    """
    try:
        results = page.evaluate("""(query) => {
            const matches = [];
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null
            );
            let idx = 0;
            const queryLower = query.toLowerCase();
            while (walker.nextNode()) {
                const text = walker.currentNode.textContent;
                if (text.toLowerCase().includes(queryLower)) {
                    const el = walker.currentNode.parentElement;
                    if (el && el.offsetParent !== null) {
                        // Get surrounding context (up to 100 chars)
                        const full = el.innerText || text;
                        const pos = full.toLowerCase().indexOf(queryLower);
                        const start = Math.max(0, pos - 30);
                        const end = Math.min(full.length, pos + query.length + 30);
                        matches.push({
                            text: full.substring(start, end).trim(),
                            index: idx,
                        });
                        idx++;
                    }
                }
            }
            return matches.slice(0, 20);  // cap at 20 results
        }""", query)
        return results or []
    except Exception as e:
        log.warning("search_page failed: %s", e)
        return []


def find_elements(page, selector: str) -> list[dict]:
    """Query elements by CSS selector. Like browser DevTools.

    Returns list of dicts with 'tag', 'type', 'name', 'text', 'id', 'href'.
    Free — no LLM cost.
    """
    try:
        results = page.evaluate("""(selector) => {
            const els = document.querySelectorAll(selector);
            return Array.from(els)
                .filter(el => el.offsetParent !== null)  // visible only
                .slice(0, 20)
                .map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    name: el.getAttribute('name') || '',
                    id: el.getAttribute('id') || '',
                    text: (el.innerText || el.value || '').substring(0, 100).trim(),
                    href: el.getAttribute('href') || '',
                }));
        }""", selector)
        return results or []
    except Exception as e:
        log.warning("find_elements failed: %s", e)
        return []
```

- [ ] **Step 4: Register SEARCH_PAGE and FIND patterns in action_parser.py**

Add to the `PATTERNS` dict in `fantoma/action_parser.py` (after line 19):

```python
    "search_page": re.compile(r'SEARCH_PAGE\s*["\'](.+?)["\']', re.IGNORECASE),
    "find": re.compile(r'FIND\s*["\'](.+?)["\']', re.IGNORECASE),
```

Add to `EXTRACT_PATTERNS` list (after line 30):

```python
    r'(SEARCH_PAGE\s*"[^"]*")',
    r'(FIND\s*"[^"]*")',
```

Add handling in `execute_action` function, before the free-form fallback (before line 136 `return _handle_freeform`):

```python
        # SEARCH_PAGE "query"
        m = PATTERNS["search_page"].match(action_raw)
        if m:
            from fantoma.browser.actions import search_page
            results = search_page(page, m.group(1))
            if results:
                log.info("SEARCH_PAGE found %d matches for '%s'", len(results), m.group(1))
                for r in results[:5]:
                    log.info("  [%d] ...%s...", r["index"], r["text"][:60])
            else:
                log.info("SEARCH_PAGE: no matches for '%s'", m.group(1))
            return True

        # FIND "selector"
        m = PATTERNS["find"].match(action_raw)
        if m:
            from fantoma.browser.actions import find_elements
            results = find_elements(page, m.group(1))
            if results:
                log.info("FIND found %d elements for '%s'", len(results), m.group(1))
                for r in results[:5]:
                    log.info("  <%s> %s", r["tag"], r["text"][:60] or r["name"] or r["id"])
            else:
                log.info("FIND: no elements for '%s'", m.group(1))
            return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_search_tools.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ --tb=short -q`
Expected: All passed

- [ ] **Step 7: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/actions.py fantoma/action_parser.py tests/test_search_tools.py
git commit -m "feat: free search tools — SEARCH_PAGE and FIND actions (zero LLM cost)"
```

---

### Task 7: Message Compaction

**Files:**
- Modify: `fantoma/executor.py` (add _compact_history method)
- Modify: `fantoma/llm/prompts.py` (add COMPACTION_SYSTEM)

- [ ] **Step 1: Add COMPACTION_SYSTEM prompt**

Add to `fantoma/llm/prompts.py` after EXTRACTION_SYSTEM:

```python
COMPACTION_SYSTEM = """\
Summarize what has been accomplished so far in this browser automation task.
Include: pages visited, forms filled, buttons clicked, data found, errors encountered.
Be specific about what succeeded and what failed.
Keep it under 200 words. No speculation about what to do next.\
"""
```

- [ ] **Step 2: Add `_compact_history` and history tracking to Executor**

Add these attributes to `Executor.__init__` in `fantoma/executor.py` (after line 49 `self._env_level = 1`):

```python
        self._step_history: list[str] = []  # text summary of each step for LLM context
        self._compacted_memory: str = ""  # summarized old history
        self._compact_threshold = 30  # compact after this many steps
        self._compact_keep_recent = 6  # keep this many recent steps verbatim
```

Add the `_compact_history` method after `_maybe_escalate` (after line 596):

```python
def _compact_history(self):
    """Summarize old step history when it gets too long.

    Keeps the last N steps verbatim, summarizes the rest via one LLM call.
    Prevents context window overflow on long tasks (50+ steps).
    """
    if len(self._step_history) < self._compact_threshold:
        return

    from fantoma.llm.prompts import COMPACTION_SYSTEM

    old_steps = self._step_history[:-self._compact_keep_recent]
    recent_steps = self._step_history[-self._compact_keep_recent:]

    history_text = "\n".join(f"Step {i+1}: {s}" for i, s in enumerate(old_steps))
    task_summary = f"Steps completed so far:\n{history_text}"

    try:
        summary = self.llm.chat(
            [{"role": "system", "content": COMPACTION_SYSTEM},
             {"role": "user", "content": task_summary}],
            max_tokens=300,
        )
        if summary:
            self._compacted_memory = summary.strip()
            self._step_history = recent_steps
            log.info("History compacted: %d steps → %d words summary + %d recent steps",
                     len(old_steps), len(self._compacted_memory.split()), len(recent_steps))
    except Exception as e:
        log.warning("History compaction failed: %s", e)
```

Update the LLM prompt construction in `execute_reactive` to include compacted history. In the user_msg construction (around line 256), change to:

```python
            user_msg = f"Task: {task}\n\n{dom_text}"
            if self._compacted_memory:
                user_msg += f"\n\n[Previous progress (unverified summary):\n{self._compacted_memory}]"
            if self._step_history:
                recent = "\n".join(f"  {s}" for s in self._step_history[-self._compact_keep_recent:])
                user_msg += f"\n\nRecent steps:\n{recent}"
            if failed:
                user_msg += f"\n\nFailed (don't repeat): {', '.join(failed)}"
```

At the end of each step's action execution, record the step to history:

```python
            # Record step for history tracking (add after the action execution loop)
            step_summary = f"{actions[0][:60]}" if actions else "no action"
            self._step_history.append(step_summary)
            self._compact_history()
```

- [ ] **Step 3: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ --tb=short -q`
Expected: All passed

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/executor.py fantoma/llm/prompts.py
git commit -m "feat: message compaction — summarizes old history for long tasks"
```

---

### Task 8: Sensitive Data Handling

**Files:**
- Modify: `fantoma/agent.py` (add sensitive_data param)
- Modify: `fantoma/executor.py` (placeholder injection/filtering)
- Modify: `fantoma/llm/prompts.py` (update REACTIVE_SYSTEM)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sensitive_data.py
"""Tests for sensitive data placeholder injection."""
from fantoma.executor import Executor


def test_replace_secrets_in_action():
    """Placeholders in actions get replaced with real values at execution time."""
    secrets = {"email": "real@test.com", "password": "Secret123"}
    action = 'TYPE [3] "<secret:email>"'
    result = Executor._inject_secrets(action, secrets)
    assert result == 'TYPE [3] "real@test.com"'


def test_replace_multiple_secrets():
    secrets = {"email": "real@test.com", "password": "Secret123"}
    action = 'TYPE [5] "<secret:password>"'
    result = Executor._inject_secrets(action, secrets)
    assert result == 'TYPE [5] "Secret123"'


def test_no_secrets_returns_unchanged():
    action = 'CLICK [3]'
    result = Executor._inject_secrets(action, {})
    assert result == 'CLICK [3]'


def test_filter_secrets_from_text():
    """Real values in text get replaced with placeholders for logging."""
    secrets = {"password": "Secret123"}
    text = 'Step 5: TYPE [3] "Secret123"'
    result = Executor._filter_secrets(text, secrets)
    assert "Secret123" not in result
    assert "<secret:password>" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_sensitive_data.py -v`
Expected: FAIL — `AttributeError: type object 'Executor' has no attribute '_inject_secrets'`

- [ ] **Step 3: Add sensitive_data to Agent constructor**

In `fantoma/agent.py`, add `sensitive_data` parameter to `__init__` (after `verification_callback` parameter):

```python
        sensitive_data: dict = None,
```

And store it:

```python
        self._sensitive_data = sensitive_data or {}
```

Pass it to Executor in `run()` — update the Executor construction (around line 147):

```python
            executor = Executor(
                browser=browser,
                llm=self._llm,
                config=self.config,
                escalation=self.escalation,
                sensitive_data=self._sensitive_data,
            )
```

- [ ] **Step 4: Add secret injection/filtering to Executor**

In `fantoma/executor.py`, update `__init__` to accept `sensitive_data`:

```python
    def __init__(self, browser: BrowserEngine, llm: LLMClient,
                 config: FantomaConfig, escalation: EscalationChain = None,
                 sensitive_data: dict = None):
```

Add after `self._env_level = 1`:

```python
        self._secrets = sensitive_data or {}
```

Add static methods to Executor class:

```python
@staticmethod
def _inject_secrets(action: str, secrets: dict) -> str:
    """Replace <secret:key> placeholders with real values in an action string."""
    for key, value in secrets.items():
        action = action.replace(f"<secret:{key}>", value)
    return action

@staticmethod
def _filter_secrets(text: str, secrets: dict) -> str:
    """Replace real secret values with placeholders in text (for logging/history)."""
    for key, value in secrets.items():
        if value and value in text:
            text = text.replace(value, f"<secret:{key}>")
    return text
```

In `execute_reactive`, after parsing actions and before executing them, inject secrets:

```python
            # Inject secrets into actions before execution
            if self._secrets:
                actions = [self._inject_secrets(a, self._secrets) for a in actions]
```

In the step history recording, filter secrets:

```python
            step_summary = f"{actions[0][:60]}" if actions else "no action"
            if self._secrets:
                step_summary = self._filter_secrets(step_summary, self._secrets)
            self._step_history.append(step_summary)
```

Update REACTIVE_SYSTEM in prompts.py — add after the rules:

```python
- If secrets are available, use them with <secret:name> syntax (e.g., TYPE [3] "<secret:email>").
```

When building the LLM prompt in `execute_reactive`, add secret hints:

```python
            if self._secrets:
                secret_list = ", ".join(f"<secret:{k}>" for k in self._secrets.keys())
                user_msg += f"\n\nAvailable secrets: {secret_list}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_sensitive_data.py -v`
Expected: All PASS

- [ ] **Step 6: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ --tb=short -q`
Expected: All passed

- [ ] **Step 7: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py fantoma/executor.py fantoma/llm/prompts.py tests/test_sensitive_data.py
git commit -m "feat: sensitive data — placeholder injection, filtered from logs/history"
```

---

### Task 9: Version Bump + pyproject.toml Update

**Files:**
- Modify: `fantoma/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump version in `__init__.py`**

Change `fantoma/__init__.py`:

```python
"""Fantoma — The undetectable AI browser agent."""
__version__ = "0.5.0"
from fantoma.agent import Agent, AgentResult
```

- [ ] **Step 2: Add sessions optional dependency in pyproject.toml**

In `pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
sessions = ["cryptography>=41.0"]
```

- [ ] **Step 3: Run full test suite one final time**

Run: `cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ --tb=short -q`
Expected: All passed (155+ existing + new tests)

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/__init__.py pyproject.toml
git commit -m "release: v0.5.0 — sessions, unified login, multi-action, paint-order, search tools, compaction, secrets"
```

---

### Task 10: Update PROGRESS.md

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: Add v0.5.0 session entry to PROGRESS.md**

Add a new session entry at the top of PROGRESS.md documenting all seven features, bugs fixed, files changed, and test count. Follow the existing format (see Session 6 as reference).

- [ ] **Step 2: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add PROGRESS.md
git commit -m "docs: v0.5.0 progress notes"
```
