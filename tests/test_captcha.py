"""Tests for CAPTCHA sitekey extraction, token injection, and API solver."""


class FakePage:
    """Minimal mock of a Playwright page for testing sitekey extraction."""

    def __init__(self, evaluate_results=None, url="https://example.com"):
        self._results = list(evaluate_results or [])
        self._call_index = 0
        self.url = url
        self._evaluate_calls = []

    def evaluate(self, script, arg=None):
        self._evaluate_calls.append(script)
        if self._call_index < len(self._results):
            result = self._results[self._call_index]
            self._call_index += 1
            return result
        return None


# ── Sitekey extraction ────────────────────────────────────────


def test_extract_sitekey_from_data_attribute():
    """data-sitekey attribute on a div — simple inline case."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage(["6Lc_abc123"])
    key = extract_sitekey(page, "recaptcha")
    assert key == "6Lc_abc123"


def test_extract_sitekey_from_iframe_src():
    """reCAPTCHA via iframe — sitekey from src URL k= parameter."""
    from fantoma.captcha.sitekey import extract_sitekey
    # First call (data-sitekey) returns None, second (iframe) returns key
    page = FakePage([None, "6Lc_iframe_key"])
    key = extract_sitekey(page, "recaptcha")
    assert key == "6Lc_iframe_key"


def test_extract_sitekey_from_js_globals():
    """reCAPTCHA config in JS globals — last resort."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage([None, None, "6Lc_js_key"])
    key = extract_sitekey(page, "recaptcha")
    assert key == "6Lc_js_key"


def test_extract_sitekey_returns_none_when_not_found():
    """No sitekey anywhere — returns None gracefully."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage([None, None, None])
    key = extract_sitekey(page, "recaptcha")
    assert key is None


def test_extract_turnstile_sitekey_from_iframe():
    """Turnstile via iframe — sitekey from challenges.cloudflare.com URL."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage([None, "0x4AAAAAAA_turnstile"])
    key = extract_sitekey(page, "turnstile")
    assert key == "0x4AAAAAAA_turnstile"


def test_extract_hcaptcha_sitekey_inline():
    """hCaptcha with data-sitekey attribute."""
    from fantoma.captcha.sitekey import extract_sitekey
    page = FakePage(["hcap_key_123"])
    key = extract_sitekey(page, "hcaptcha")
    assert key == "hcap_key_123"


# ── Invisibility detection ────────────────────────────────────


def test_detect_invisible_recaptcha():
    """Detect invisible reCAPTCHA (data-size='invisible' or no checkbox)."""
    from fantoma.captcha.sitekey import detect_invisible
    # Returns True when page reports invisible
    page = FakePage([True])
    assert detect_invisible(page, "recaptcha") is True


def test_detect_visible_recaptcha():
    """Visible reCAPTCHA v2 checkbox — not invisible."""
    from fantoma.captcha.sitekey import detect_invisible
    page = FakePage([False])
    assert detect_invisible(page, "recaptcha") is False


# ── Version detection ─────────────────────────────────────────


def test_detect_recaptcha_v3():
    """Detect v3 from script render= parameter or client ID >= 10000."""
    from fantoma.captcha.sitekey import detect_version
    page = FakePage(["v3"])
    assert detect_version(page) == "v3"


def test_detect_recaptcha_v2():
    """Detect v2 (no v3 signals)."""
    from fantoma.captcha.sitekey import detect_version
    page = FakePage([None])
    assert detect_version(page) == "v2"


# ── Token injection ───────────────────────────────────────────


def test_inject_token_via_textarea():
    """Inject token into g-recaptcha-response textarea (inline case)."""
    from fantoma.captcha.orchestrator import inject_token

    page = FakePage([True])  # textarea found and set
    result = inject_token(page, "test_token_123", "recaptcha")
    assert result is True


def test_inject_token_via_callback():
    """Inject token via callback when textarea doesn't exist (iframe case)."""
    from fantoma.captcha.orchestrator import inject_token

    # First call (textarea) returns False, second (callback) returns True
    page = FakePage([False, True])
    result = inject_token(page, "token_abc", "recaptcha")
    assert result is True


def test_inject_token_turnstile():
    """Inject Turnstile token."""
    from fantoma.captcha.orchestrator import inject_token

    page = FakePage([True])
    result = inject_token(page, "turnstile_tok", "turnstile")
    assert result is True


def test_inject_token_fails_gracefully():
    """All injection methods fail — returns False, no crash."""
    from fantoma.captcha.orchestrator import inject_token

    page = FakePage([False, False])
    result = inject_token(page, "token", "recaptcha")
    assert result is False


# ── API solver v3 ─────────────────────────────────────────────


def test_api_solver_has_recaptcha_v3():
    """APICaptchaSolver has solve_recaptcha_v3 method."""
    from fantoma.captcha.api_solver import APICaptchaSolver
    solver = APICaptchaSolver("capsolver", "fake-key")
    assert hasattr(solver, "solve_recaptcha_v3")


def test_api_solver_v2_accepts_is_invisible():
    """solve_recaptcha_v2 accepts is_invisible parameter."""
    from fantoma.captcha.api_solver import APICaptchaSolver
    import inspect
    solver = APICaptchaSolver("capsolver", "fake-key")
    sig = inspect.signature(solver.solve_recaptcha_v2)
    assert "is_invisible" in sig.parameters
