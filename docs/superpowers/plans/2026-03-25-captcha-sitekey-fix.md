# CAPTCHA Sitekey Extraction Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Fantoma's CAPTCHA solving so it works on sites that load reCAPTCHA/hCaptcha/Turnstile via iframes (like Reddit), not just inline `data-sitekey` attributes.

**Architecture:** Extract sitekey via 3 methods (DOM attribute → iframe src URL → JS globals). Fix token injection to use callbacks for iframe-based CAPTCHAs. All changes contained in `fantoma/captcha/orchestrator.py` with a new helper `fantoma/captcha/sitekey.py`.

**Tech Stack:** Python 3.10+, Playwright (page.evaluate), pytest

---

### Task 1: Extract sitekey helper module

**Files:**
- Create: `fantoma/captcha/sitekey.py`
- Test: `tests/test_captcha.py`

- [ ] **Step 1: Write failing tests for sitekey extraction**

```python
# tests/test_captcha.py
"""Tests for CAPTCHA sitekey extraction and token injection."""


class FakePage:
    """Minimal mock of a Playwright page for testing sitekey extraction."""

    def __init__(self, evaluate_results=None, url="https://example.com"):
        self._evaluate_results = evaluate_results or {}
        self._call_index = 0
        self.url = url
        self._evaluate_calls = []

    def evaluate(self, script):
        self._evaluate_calls.append(script)
        # Return results in order they were registered
        if self._call_index < len(self._evaluate_results):
            result = list(self._evaluate_results.values())[self._call_index]
            self._call_index += 1
            return result
        return None


def test_extract_sitekey_from_data_attribute():
    """data-sitekey attribute on a div — the simple inline case."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage({"data_attr": "6Lc_abc123"})
    key = extract_sitekey(page, "recaptcha")
    assert key == "6Lc_abc123"


def test_extract_sitekey_from_iframe_src():
    """reCAPTCHA loaded via iframe — sitekey is in the src URL k= parameter."""
    from fantoma.captcha.sitekey import extract_sitekey
    # First call (data-sitekey) returns None, second (iframe) returns key
    page = FakePage({"data_attr": None, "iframe": "6Lc_iframe_key"})
    key = extract_sitekey(page, "recaptcha")
    assert key == "6Lc_iframe_key"


def test_extract_sitekey_from_js_globals():
    """reCAPTCHA config in JS globals — last resort."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage({"data_attr": None, "iframe": None, "js_global": "6Lc_js_key"})
    key = extract_sitekey(page, "recaptcha")
    assert key == "6Lc_js_key"


def test_extract_sitekey_returns_none_when_not_found():
    """No sitekey anywhere — returns None gracefully."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage({"a": None, "b": None, "c": None})
    key = extract_sitekey(page, "recaptcha")
    assert key is None


def test_extract_turnstile_sitekey_from_iframe():
    """Turnstile loaded via iframe — sitekey from challenges.cloudflare.com URL."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage({"data_attr": None, "iframe": "0x4AAAAAAA_turnstile"})
    key = extract_sitekey(page, "turnstile")
    assert key == "0x4AAAAAAA_turnstile"


def test_extract_hcaptcha_sitekey():
    """hCaptcha with data-sitekey attribute."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage({"data_attr": "hcap_key_123"})
    key = extract_sitekey(page, "hcaptcha")
    assert key == "hcap_key_123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_captcha.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fantoma.captcha.sitekey'`

- [ ] **Step 3: Implement sitekey extraction module**

```python
# fantoma/captcha/sitekey.py
"""Extract CAPTCHA sitekeys from pages using multiple strategies.

Sites load CAPTCHAs in different ways:
  1. Inline: <div data-sitekey="..."> (simple, but rare on modern sites)
  2. Iframe: <iframe src="...recaptcha...?k=SITEKEY"> (Reddit, most sites)
  3. JS globals: window.___grecaptcha_cfg or similar config objects

This module tries all three in order and returns the first key found.
"""
import logging
import re

log = logging.getLogger("fantoma.captcha")

# Iframe URL patterns per CAPTCHA type
_IFRAME_PATTERNS = {
    "recaptcha": r'iframe[src*="recaptcha"], iframe[src*="google.com/recaptcha"]',
    "hcaptcha": r'iframe[src*="hcaptcha.com"]',
    "turnstile": r'iframe[src*="challenges.cloudflare.com"]',
}

# JS global extraction scripts per CAPTCHA type
_JS_GLOBALS = {
    "recaptcha": """() => {
        // Check grecaptcha config
        if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
            for (const [, client] of Object.entries(window.___grecaptcha_cfg.clients)) {
                // Walk the client object tree looking for sitekey
                const json = JSON.stringify(client);
                const match = json.match(/"sitekey"\\s*:\\s*"([^"]+)"/);
                if (match) return match[1];
            }
        }
        // Check for recaptcha/api.js script src with render= param
        const scripts = document.querySelectorAll('script[src*="recaptcha"]');
        for (const s of scripts) {
            const m = s.src.match(/[?&]render=([^&]+)/);
            if (m && m[1] !== 'explicit') return m[1];
        }
        return null;
    }""",
    "hcaptcha": """() => {
        if (window.hcaptcha && window.hcaptcha._configs) {
            const keys = Object.keys(window.hcaptcha._configs);
            if (keys.length > 0) return keys[0];
        }
        return null;
    }""",
    "turnstile": """() => {
        if (window.turnstile && window.turnstile._configs) {
            const keys = Object.keys(window.turnstile._configs);
            if (keys.length > 0) return keys[0];
        }
        // Check for turnstile render calls
        const divs = document.querySelectorAll('[data-turnstile-callback], .cf-turnstile');
        for (const d of divs) {
            const key = d.getAttribute('data-sitekey');
            if (key) return key;
        }
        return null;
    }""",
}


def extract_sitekey(page, captcha_type: str) -> str | None:
    """Try multiple strategies to find the CAPTCHA sitekey on the page.

    Args:
        page: Playwright page object
        captcha_type: One of 'recaptcha', 'hcaptcha', 'turnstile'

    Returns:
        Sitekey string or None if not found.
    """
    # Strategy 1: data-sitekey attribute (inline)
    try:
        key = page.evaluate("""() => {
            const el = document.querySelector('[data-sitekey]');
            return el ? el.getAttribute('data-sitekey') : null;
        }""")
        if key:
            log.info("Sitekey found via data-sitekey attribute")
            return key
    except Exception as e:
        log.debug("data-sitekey extraction failed: %s", e)

    # Strategy 2: iframe src URL parameter
    iframe_selector = _IFRAME_PATTERNS.get(captcha_type)
    if iframe_selector:
        try:
            key = page.evaluate(f"""() => {{
                const iframe = document.querySelector('{iframe_selector}');
                if (!iframe || !iframe.src) return null;
                const url = new URL(iframe.src);
                // reCAPTCHA uses 'k', hCaptcha uses 'sitekey', Turnstile uses 'k'
                return url.searchParams.get('k')
                    || url.searchParams.get('sitekey')
                    || null;
            }}""")
            if key:
                log.info("Sitekey found via iframe src URL")
                return key
        except Exception as e:
            log.debug("iframe sitekey extraction failed: %s", e)

    # Strategy 3: JS globals
    js_script = _JS_GLOBALS.get(captcha_type)
    if js_script:
        try:
            key = page.evaluate(js_script)
            if key:
                log.info("Sitekey found via JS globals")
                return key
        except Exception as e:
            log.debug("JS global sitekey extraction failed: %s", e)

    log.warning("Could not extract %s sitekey from page", captcha_type)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_captcha.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/captcha/sitekey.py tests/test_captcha.py
git commit -m "feat(captcha): add multi-strategy sitekey extraction

Extracts sitekey via data-attribute, iframe src URL, and JS globals.
Fixes Reddit/Turnstile where reCAPTCHA is loaded via iframe."
```

---

### Task 2: Fix token injection in orchestrator

**Files:**
- Modify: `fantoma/captcha/orchestrator.py`
- Test: `tests/test_captcha.py` (append)

- [ ] **Step 1: Write failing tests for token injection**

Append to `tests/test_captcha.py`:

```python
def test_inject_token_recaptcha_textarea():
    """Inject token into g-recaptcha-response textarea (inline case)."""
    from fantoma.captcha.orchestrator import _inject_token

    calls = []
    class MockPage:
        url = "https://example.com"
        def evaluate(self, script):
            calls.append(script)
            # Simulate: textarea exists
            if "querySelector" in script and "g-recaptcha-response" in script:
                return True
            return None

    page = MockPage()
    result = _inject_token(page, "test_token_123", "recaptcha")
    assert result is True
    # Should have tried textarea injection
    assert any("test_token_123" in c for c in calls)


def test_inject_token_recaptcha_callback():
    """Inject token via callback when textarea doesn't exist (iframe case)."""
    from fantoma.captcha.orchestrator import _inject_token

    calls = []
    class MockPage:
        url = "https://example.com"
        def evaluate(self, script):
            calls.append(script)
            # Simulate: textarea does NOT exist, callback does
            if "querySelector" in script and "g-recaptcha-response" in script:
                return False
            if "callback" in script.lower() or "grecaptcha" in script.lower():
                return True
            return None

    page = MockPage()
    result = _inject_token(page, "token_abc", "recaptcha")
    assert result is True


def test_inject_token_turnstile():
    """Inject Turnstile token via callback."""
    from fantoma.captcha.orchestrator import _inject_token

    calls = []
    class MockPage:
        url = "https://example.com"
        def evaluate(self, script):
            calls.append(script)
            return True

    page = MockPage()
    result = _inject_token(page, "turnstile_tok", "turnstile")
    assert result is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_captcha.py::test_inject_token_recaptcha_textarea -v`
Expected: FAIL — `ImportError: cannot import name '_inject_token'`

- [ ] **Step 3: Update orchestrator — use sitekey module and fix token injection**

Replace `_solve_with_api` in `fantoma/captcha/orchestrator.py` and add `_inject_token`:

```python
# In orchestrator.py — replace _solve_with_api method and add _inject_token function

def _solve_with_api(self, page, captcha_type: str) -> bool:
    """Solve via CapSolver/2Captcha/Anti-Captcha API."""
    from fantoma.captcha.api_solver import APICaptchaSolver
    from fantoma.captcha.sitekey import extract_sitekey

    solver = APICaptchaSolver(self.config.captcha.api, self.config.captcha.key)
    site_key = extract_sitekey(page, captcha_type)

    if not site_key:
        return False

    url = page.url
    token = None
    if captcha_type == "recaptcha":
        token = solver.solve_recaptcha_v2(site_key, url)
    elif captcha_type == "hcaptcha":
        token = solver.solve_hcaptcha(site_key, url)
    elif captcha_type == "turnstile":
        token = solver.solve_turnstile(site_key, url)

    if token:
        log.info("CAPTCHA solved via %s", self.config.captcha.api)
        return _inject_token(page, token, captcha_type)
    return False


def _inject_token(page, token: str, captcha_type: str) -> bool:
    """Inject a solved CAPTCHA token into the page.

    Tries textarea/hidden input first (inline CAPTCHAs), then
    falls back to callback invocation (iframe CAPTCHAs like Reddit).
    """
    try:
        if captcha_type in ("recaptcha", "hcaptcha"):
            # Response field names per provider
            field = "g-recaptcha-response" if captcha_type == "recaptcha" else "h-captcha-response"

            # Try 1: Set textarea/hidden input value (inline CAPTCHAs)
            found = page.evaluate(f"""() => {{
                const el = document.querySelector('[name="{field}"], #{field}, textarea#{field}');
                if (el) {{ el.value = '{token}'; return true; }}
                // Also check all textareas (some sites use custom names)
                const areas = document.querySelectorAll('textarea');
                for (const a of areas) {{
                    if (a.name.includes('captcha') || a.id.includes('captcha')) {{
                        a.value = '{token}';
                        return true;
                    }}
                }}
                return false;
            }}""")

            if found:
                log.info("Token injected via form field")
                return True

            # Try 2: Invoke callback (iframe CAPTCHAs)
            if captcha_type == "recaptcha":
                invoked = page.evaluate(f"""() => {{
                    // Try grecaptcha callback
                    if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {{
                        for (const [, client] of Object.entries(window.___grecaptcha_cfg.clients)) {{
                            const json = JSON.stringify(client);
                            const match = json.match(/"callback"\\s*:\\s*"([^"]+)"/);
                            if (match && typeof window[match[1]] === 'function') {{
                                window[match[1]]('{token}');
                                return true;
                            }}
                        }}
                    }}
                    // Try generic grecaptcha enterprise callback
                    if (window.grecaptcha && window.grecaptcha.enterprise) {{
                        try {{ window.grecaptcha.enterprise.execute(); return true; }} catch(e) {{}}
                    }}
                    // Last resort: find and call any onSuccess-style callback
                    if (window.grecaptcha) {{
                        try {{
                            const resp = document.createElement('textarea');
                            resp.name = 'g-recaptcha-response';
                            resp.value = '{token}';
                            resp.style.display = 'none';
                            const form = document.querySelector('form');
                            if (form) {{ form.appendChild(resp); return true; }}
                        }} catch(e) {{}}
                    }}
                    return false;
                }}""")
                if invoked:
                    log.info("Token injected via reCAPTCHA callback")
                    return True
            else:
                # hCaptcha callback
                invoked = page.evaluate(f"""() => {{
                    if (window.hcaptcha) {{
                        try {{ window.hcaptcha.setResponse('{token}'); return true; }} catch(e) {{}}
                    }}
                    return false;
                }}""")
                if invoked:
                    log.info("Token injected via hCaptcha API")
                    return True

        elif captcha_type == "turnstile":
            invoked = page.evaluate(f"""() => {{
                // Try setting the hidden input
                const input = document.querySelector('[name="cf-turnstile-response"], input[name*="turnstile"]');
                if (input) {{ input.value = '{token}'; return true; }}
                // Try the Turnstile callback
                const widget = document.querySelector('.cf-turnstile');
                if (widget) {{
                    const cb = widget.getAttribute('data-callback');
                    if (cb && typeof window[cb] === 'function') {{
                        window[cb]('{token}');
                        return true;
                    }}
                }}
                // Create hidden input as fallback
                const form = document.querySelector('form');
                if (form) {{
                    const inp = document.createElement('input');
                    inp.type = 'hidden';
                    inp.name = 'cf-turnstile-response';
                    inp.value = '{token}';
                    form.appendChild(inp);
                    return true;
                }}
                return false;
            }}""")
            if invoked:
                log.info("Token injected for Turnstile")
                return True

        log.warning("Could not inject %s token into page", captcha_type)
        return False
    except Exception as e:
        log.error("Token injection failed: %s", e)
        return False
```

- [ ] **Step 4: Run all captcha tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_captcha.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/captcha/orchestrator.py tests/test_captcha.py
git commit -m "fix(captcha): use multi-strategy sitekey + callback token injection

Fixes CAPTCHA solving on Reddit and other sites that load reCAPTCHA
via iframe instead of inline data-sitekey attribute.

- orchestrator now uses sitekey.extract_sitekey() (3 strategies)
- token injection tries textarea first, falls back to JS callback
- handles recaptcha, hcaptcha, and turnstile iframe variants"
```

---

### Task 3: Verify end-to-end with a live Reddit test

**Files:** None (manual verification)

- [ ] **Step 1: Quick smoke test — sitekey extraction on Reddit**

Run a short Fantoma script that navigates to Reddit login and checks if it can extract the sitekey:

```python
# Test script (don't commit) — tests/manual_reddit_test.py
from fantoma import Agent

agent = Agent(
    llm_url="http://localhost:8081/v1",
    headless=True,
    max_steps=5,
    timeout=30,
)

result = agent.run(
    "Go to old.reddit.com/login and tell me what you see on the page",
    start_url="https://old.reddit.com/login",
)
print(f"Success: {result.success}")
print(f"Data: {result.data}")
print(f"Steps: {result.steps_taken}")
```

Run: `cd /home/workspace/workbench/fantoma && python tests/manual_reddit_test.py 2>&1 | tail -20`
Expected: Should detect and log reCAPTCHA sitekey extraction (check for "Sitekey found via" in logs)

- [ ] **Step 2: Confirm sitekey extraction works in logs**

Verify log output shows one of:
- "Sitekey found via data-sitekey attribute"
- "Sitekey found via iframe src URL"
- "Sitekey found via JS globals"

If none appear, the page may not show reCAPTCHA until login is attempted — adjust test accordingly.
