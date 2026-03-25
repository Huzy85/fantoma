"""Edge case and integration tests for CAPTCHA sitekey extraction, token injection, and API solver."""

from unittest.mock import MagicMock, patch


class FakePage:
    """Minimal mock of a Playwright page for testing."""

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
            if isinstance(result, Exception):
                raise result
            return result
        return None


class FakePageCallable:
    """Mock page where evaluate dispatches to a callable for fine-grained control."""

    def __init__(self, handler, url="https://example.com"):
        self._handler = handler
        self.url = url
        self._evaluate_calls = []
        self._call_index = 0

    def evaluate(self, script, arg=None):
        self._evaluate_calls.append(script)
        idx = self._call_index
        self._call_index += 1
        return self._handler(idx, script)


# ── 1. Token with special characters ────────────────────────────


def test_inject_token_with_single_quotes():
    """Token containing single quotes should not crash inject_token."""
    from fantoma.captcha.orchestrator import inject_token

    # Token has single quotes — safe because we use page.evaluate(script, arg)
    # not f-string interpolation. Token should NOT appear in the JS script itself.
    token_with_quotes = "abc'def'ghi"
    page = FakePage([True])
    result = inject_token(page, token_with_quotes, "recaptcha")
    assert result is True
    # Token must NOT be interpolated into JS (security: passed as arg instead)
    assert token_with_quotes not in page._evaluate_calls[0]


def test_inject_token_with_backslashes():
    """Token containing backslashes should not crash inject_token."""
    from fantoma.captcha.orchestrator import inject_token

    token_with_backslash = "abc\\def\\\\ghi"
    page = FakePage([True])
    result = inject_token(page, token_with_backslash, "recaptcha")
    assert result is True


def test_inject_token_with_newlines():
    """Token containing newlines should not crash inject_token."""
    from fantoma.captcha.orchestrator import inject_token

    token_with_newlines = "abc\ndef\nghi"
    page = FakePage([True])
    result = inject_token(page, token_with_newlines, "recaptcha")
    assert result is True


def test_inject_token_special_chars_evaluate_raises():
    """If special chars in token cause JS syntax error, inject_token returns False."""
    from fantoma.captcha.orchestrator import inject_token

    # Simulate page.evaluate raising an exception (as would happen with broken JS)
    page = FakePage([Exception("SyntaxError: Unexpected token")])
    result = inject_token(page, "token'; alert('xss'); //", "recaptcha")
    assert result is False


# ── 2. page.evaluate throws exception ───────────────────────────


def test_extract_sitekey_evaluate_raises_all_strategies():
    """If page.evaluate raises on every call, extract_sitekey returns None."""
    from fantoma.captcha.sitekey import extract_sitekey

    page = FakePage([
        Exception("evaluate failed"),  # data-sitekey strategy
        Exception("evaluate failed"),  # iframe strategy
        Exception("evaluate failed"),  # JS globals strategy
    ])
    result = extract_sitekey(page, "recaptcha")
    assert result is None


def test_extract_sitekey_evaluate_raises_first_succeeds_second():
    """If first strategy throws, second strategy can still succeed."""
    from fantoma.captcha.sitekey import extract_sitekey

    page = FakePage([
        Exception("evaluate failed"),  # data-sitekey fails
        "key_from_iframe",             # iframe succeeds
    ])
    result = extract_sitekey(page, "recaptcha")
    assert result == "key_from_iframe"


def test_inject_token_evaluate_raises():
    """If page.evaluate throws during injection, inject_token returns False."""
    from fantoma.captcha.orchestrator import inject_token

    page = FakePage([Exception("page crashed")])
    result = inject_token(page, "some_token", "recaptcha")
    assert result is False


def test_inject_token_turnstile_evaluate_raises():
    """If page.evaluate throws during turnstile injection, returns False."""
    from fantoma.captcha.orchestrator import inject_token

    page = FakePage([Exception("page crashed")])
    result = inject_token(page, "some_token", "turnstile")
    assert result is False


# ── 3. Multiple iframes on page ─────────────────────────────────


def test_extract_sitekey_multiple_iframes_returns_first():
    """With multiple recaptcha iframes, extract_sitekey returns the first valid key.

    querySelector always returns the first match, so the first iframe's key wins.
    """
    from fantoma.captcha.sitekey import extract_sitekey

    # data-sitekey returns None, iframe returns first key
    page = FakePage([None, "first_iframe_key"])
    key = extract_sitekey(page, "recaptcha")
    assert key == "first_iframe_key"


def test_extract_sitekey_first_iframe_no_key_falls_to_globals():
    """If iframe strategy returns None, falls through to JS globals."""
    from fantoma.captcha.sitekey import extract_sitekey

    page = FakePage([None, None, "key_from_globals"])
    key = extract_sitekey(page, "recaptcha")
    assert key == "key_from_globals"


# ── 4. Empty sitekey ────────────────────────────────────────────


def test_extract_sitekey_empty_string_treated_as_not_found():
    """page.evaluate returns "" instead of None — should be treated as not found."""
    from fantoma.captcha.sitekey import extract_sitekey

    # Empty string is falsy in Python, so `if key:` should skip it
    page = FakePage(["", "", ""])
    key = extract_sitekey(page, "recaptcha")
    assert key is None


def test_extract_sitekey_empty_string_first_real_second():
    """First strategy returns empty string, second returns real key."""
    from fantoma.captcha.sitekey import extract_sitekey

    page = FakePage(["", "real_key_123"])
    key = extract_sitekey(page, "recaptcha")
    assert key == "real_key_123"


# ── 5. Orchestrator integration ─────────────────────────────────


def test_orchestrator_solve_with_api_full_flow():
    """Test _solve_with_api: detect sitekey → solve via API → inject token."""
    from fantoma.captcha.orchestrator import CaptchaOrchestrator
    from fantoma.config import FantomaConfig, CaptchaConfig

    config = FantomaConfig(captcha=CaptchaConfig(api="capsolver", key="fake-key"))
    orch = CaptchaOrchestrator(config)

    # Page: extract_sitekey needs 3 calls (data-sitekey=None, iframe="site_key_abc", ...),
    # detect_version needs 1 call, inject_token needs 1 call.
    # But _solve_with_api calls: extract_sitekey, detect_version, detect_invisible, solver, inject_token.
    # Let's use a callable page for precise control.

    call_map = {
        0: None,              # extract_sitekey: data-sitekey → None
        1: "site_key_abc",    # extract_sitekey: iframe → key found
        # detect_version is called next
        2: "v2",              # detect_version → v2 (not None, so returns "v2" ... actually returns the string)
        # detect_invisible is called next
        3: False,             # detect_invisible → not invisible
        # inject_token: first evaluate for textarea
        4: True,              # inject_token: textarea found → True
    }

    page = FakePageCallable(lambda idx, script: call_map.get(idx), url="https://example.com")

    with patch("fantoma.captcha.api_solver.APICaptchaSolver") as MockSolver:
        mock_solver_instance = MockSolver.return_value
        mock_solver_instance.solve_recaptcha_v2.return_value = "solved_token_xyz"

        result = orch._solve_with_api(page, "recaptcha")

    assert result is True
    mock_solver_instance.solve_recaptcha_v2.assert_called_once_with(
        "site_key_abc", "https://example.com", is_invisible=False
    )


def test_orchestrator_solve_with_api_no_sitekey():
    """_solve_with_api returns False when sitekey cannot be extracted."""
    from fantoma.captcha.orchestrator import CaptchaOrchestrator
    from fantoma.config import FantomaConfig, CaptchaConfig

    config = FantomaConfig(captcha=CaptchaConfig(api="capsolver", key="fake-key"))
    orch = CaptchaOrchestrator(config)

    # All strategies return None
    page = FakePage([None, None, None])
    result = orch._solve_with_api(page, "recaptcha")
    assert result is False


def test_orchestrator_solve_with_api_solver_returns_none():
    """_solve_with_api returns False when API solver fails (returns None)."""
    from fantoma.captcha.orchestrator import CaptchaOrchestrator
    from fantoma.config import FantomaConfig, CaptchaConfig

    config = FantomaConfig(captcha=CaptchaConfig(api="capsolver", key="fake-key"))
    orch = CaptchaOrchestrator(config)

    # Sitekey found on first call, then detect_version, detect_invisible
    page = FakePage(["site_key", "v2", False])

    with patch("fantoma.captcha.api_solver.APICaptchaSolver") as MockSolver:
        mock_solver_instance = MockSolver.return_value
        mock_solver_instance.solve_recaptcha_v2.return_value = None

        result = orch._solve_with_api(page, "recaptcha")

    assert result is False


# ── 6. detect_version with no globals ───────────────────────────


def test_detect_version_no_globals_returns_v2():
    """When ___grecaptcha_cfg doesn't exist, evaluate throws ReferenceError → returns v2."""
    from fantoma.captcha.sitekey import detect_version

    page = FakePage([Exception("ReferenceError: ___grecaptcha_cfg is not defined")])
    result = detect_version(page)
    assert result == "v2"


def test_detect_version_evaluate_returns_none():
    """When evaluate returns None (no v3 signals), returns v2."""
    from fantoma.captcha.sitekey import detect_version

    page = FakePage([None])
    result = detect_version(page)
    assert result == "v2"


# ── 7. detect_invisible on non-recaptcha ────────────────────────


def test_detect_invisible_hcaptcha_always_false():
    """detect_invisible should always return False for hcaptcha."""
    from fantoma.captcha.sitekey import detect_invisible

    page = FakePage([True])  # Would return True if it checked, but shouldn't
    assert detect_invisible(page, "hcaptcha") is False
    # Verify evaluate was never called
    assert len(page._evaluate_calls) == 0


def test_detect_invisible_turnstile_always_false():
    """detect_invisible should always return False for turnstile."""
    from fantoma.captcha.sitekey import detect_invisible

    page = FakePage([True])
    assert detect_invisible(page, "turnstile") is False
    assert len(page._evaluate_calls) == 0


def test_detect_invisible_altcha_always_false():
    """detect_invisible should always return False for altcha."""
    from fantoma.captcha.sitekey import detect_invisible

    page = FakePage([True])
    assert detect_invisible(page, "altcha") is False
    assert len(page._evaluate_calls) == 0


# ── 8. inject_token turnstile with no form ──────────────────────


def test_inject_turnstile_no_form_no_input_no_callback():
    """Turnstile injection when page has no form, no hidden input, no callback → False."""
    from fantoma.captcha.orchestrator import inject_token

    page = FakePage([False])  # evaluate returns false (nothing found)
    result = inject_token(page, "turnstile_token", "turnstile")
    assert result is False


def test_inject_turnstile_no_form_evaluate_returns_none():
    """Turnstile injection when evaluate returns None → False."""
    from fantoma.captcha.orchestrator import inject_token

    page = FakePage([None])
    result = inject_token(page, "turnstile_token", "turnstile")
    assert result is False


# ── 9. api_solver v2 with is_invisible=True ─────────────────────


def test_api_solver_v2_invisible_task_dict():
    """solve_recaptcha_v2 with is_invisible=True includes isInvisible in the task."""
    from fantoma.captcha.api_solver import APICaptchaSolver

    solver = APICaptchaSolver("capsolver", "fake-key")

    with patch("fantoma.captcha.api_solver.httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"taskId": "task123"}
        mock_post.return_value = mock_resp

        # Will time out after first poll, but we only need to check the create call
        with patch("fantoma.captcha.api_solver.time.sleep"):
            with patch("fantoma.captcha.api_solver.time.time", side_effect=[0, 0, 999]):
                solver.solve_recaptcha_v2("sitekey", "https://example.com", is_invisible=True)

        # Check the first call (createTask)
        create_call = mock_post.call_args_list[0]
        task = create_call.kwargs.get("json", create_call[1].get("json", {})).get("task", {})
        assert task["isInvisible"] is True
        assert task["type"] == "ReCaptchaV2TaskProxyLess"
        assert task["websiteKey"] == "sitekey"


def test_api_solver_v2_visible_no_invisible_key():
    """solve_recaptcha_v2 with is_invisible=False should NOT include isInvisible."""
    from fantoma.captcha.api_solver import APICaptchaSolver

    solver = APICaptchaSolver("capsolver", "fake-key")

    with patch("fantoma.captcha.api_solver.httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"taskId": "task123"}
        mock_post.return_value = mock_resp

        with patch("fantoma.captcha.api_solver.time.sleep"):
            with patch("fantoma.captcha.api_solver.time.time", side_effect=[0, 0, 999]):
                solver.solve_recaptcha_v2("sitekey", "https://example.com", is_invisible=False)

        create_call = mock_post.call_args_list[0]
        task = create_call.kwargs.get("json", create_call[1].get("json", {})).get("task", {})
        assert "isInvisible" not in task


# ── 10. api_solver v3 with page_action ──────────────────────────


def test_api_solver_v3_with_page_action():
    """solve_recaptcha_v3 with page_action includes pageAction in the task."""
    from fantoma.captcha.api_solver import APICaptchaSolver

    solver = APICaptchaSolver("capsolver", "fake-key")

    with patch("fantoma.captcha.api_solver.httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"taskId": "task456"}
        mock_post.return_value = mock_resp

        with patch("fantoma.captcha.api_solver.time.sleep"):
            with patch("fantoma.captcha.api_solver.time.time", side_effect=[0, 0, 999]):
                solver.solve_recaptcha_v3("sitekey", "https://example.com", page_action="submit")

        create_call = mock_post.call_args_list[0]
        task = create_call.kwargs.get("json", create_call[1].get("json", {})).get("task", {})
        assert task["pageAction"] == "submit"
        assert task["type"] == "ReCaptchaV3TaskProxyLess"


def test_api_solver_v3_without_page_action():
    """solve_recaptcha_v3 without page_action should NOT include pageAction."""
    from fantoma.captcha.api_solver import APICaptchaSolver

    solver = APICaptchaSolver("capsolver", "fake-key")

    with patch("fantoma.captcha.api_solver.httpx.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"taskId": "task789"}
        mock_post.return_value = mock_resp

        with patch("fantoma.captcha.api_solver.time.sleep"):
            with patch("fantoma.captcha.api_solver.time.time", side_effect=[0, 0, 999]):
                solver.solve_recaptcha_v3("sitekey", "https://example.com")

        create_call = mock_post.call_args_list[0]
        task = create_call.kwargs.get("json", create_call[1].get("json", {})).get("task", {})
        assert "pageAction" not in task
