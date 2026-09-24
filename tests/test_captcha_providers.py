"""Paid CAPTCHA services: each gets the task names it actually accepts.

The four services share a protocol but not a vocabulary. Sending CapSolver's
"ReCaptchaV2TaskProxyLess" to Anti-Captcha or 2Captcha is rejected, so every
provider but one silently failed before names became per-provider.
"""

from unittest.mock import patch, MagicMock

import pytest

from fantoma.captcha.api_solver import APICaptchaSolver, PROVIDERS


def _sent_type(provider, method, *args):
    solver = APICaptchaSolver(provider, "key")
    created = MagicMock()
    created.json.return_value = {"errorId": 0, "taskId": 7}
    ready = MagicMock()
    ready.json.return_value = {"errorId": 0, "status": "ready",
                               "solution": {"gRecaptchaResponse": "tok", "token": "tok"}}
    with patch("fantoma.captcha.api_solver.httpx.post", side_effect=[created, ready]) as post, \
            patch("fantoma.captcha.api_solver.time.sleep"):
        assert getattr(solver, method)(*args) == "tok"
    body = post.call_args_list[0].kwargs["json"]
    assert post.call_args_list[0].args[0] == PROVIDERS[solver.provider]["create_url"]
    return body["task"]["type"]


@pytest.mark.parametrize("provider,expected", [
    ("capsolver", "ReCaptchaV2TaskProxyLess"),
    ("2captcha", "RecaptchaV2TaskProxyless"),
    ("anticaptcha", "RecaptchaV2TaskProxyless"),
    ("capmonster", "RecaptchaV2Task"),
])
def test_recaptcha_v2_name_per_provider(provider, expected):
    assert _sent_type(provider, "solve_recaptcha_v2", "sk", "https://x") == expected


@pytest.mark.parametrize("provider,expected", [
    ("capsolver", "AntiTurnstileTaskProxyLess"),
    ("2captcha", "TurnstileTaskProxyless"),
    ("anticaptcha", "TurnstileTaskProxyless"),
    ("capmonster", "TurnstileTask"),
])
def test_turnstile_name_per_provider(provider, expected):
    assert _sent_type(provider, "solve_turnstile", "sk", "https://x") == expected


def test_common_spellings_are_accepted():
    assert APICaptchaSolver("Anti-Captcha", "k").provider == "anticaptcha"
    assert APICaptchaSolver("CapMonster", "k").provider == "capmonster"


def test_an_error_stops_polling():
    solver = APICaptchaSolver("anticaptcha", "bad")
    created = MagicMock()
    created.json.return_value = {"errorId": 0, "taskId": 7}
    failed = MagicMock()
    failed.json.return_value = {"errorId": 1, "errorCode": "ERROR_KEY_DOES_NOT_EXIST"}
    with patch("fantoma.captcha.api_solver.httpx.post", side_effect=[created, failed]) as post, \
            patch("fantoma.captcha.api_solver.time.sleep"):
        assert solver.solve_hcaptcha("sk", "https://x") is None
    assert post.call_count == 2


class TestKeyFromEnvironment:
    def test_key_alone_means_capsolver(self, monkeypatch):
        from fantoma.browser_tool import Fantoma
        for var in ("FANTOMA_CAPTCHA_API", "CAPTCHA_API"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("FANTOMA_CAPTCHA_KEY", "CAP-123")
        f = Fantoma()
        assert (f.config.captcha.api, f.config.captcha.key) == ("capsolver", "CAP-123")

    def test_provider_from_environment(self, monkeypatch):
        from fantoma.browser_tool import Fantoma
        monkeypatch.setenv("FANTOMA_CAPTCHA_API", "anti-captcha")
        monkeypatch.setenv("FANTOMA_CAPTCHA_KEY", "k")
        assert Fantoma().config.captcha.api == "anticaptcha"

    def test_arguments_win(self, monkeypatch):
        from fantoma.browser_tool import Fantoma
        monkeypatch.setenv("FANTOMA_CAPTCHA_API", "2captcha")
        monkeypatch.setenv("FANTOMA_CAPTCHA_KEY", "env")
        f = Fantoma(captcha_api="capmonster", captcha_key="arg")
        assert (f.config.captcha.api, f.config.captcha.key) == ("capmonster", "arg")

    def test_nothing_set_means_no_paid_solver(self, monkeypatch):
        from fantoma.browser_tool import Fantoma
        for var in ("FANTOMA_CAPTCHA_API", "CAPTCHA_API", "FANTOMA_CAPTCHA_KEY", "CAPTCHA_KEY"):
            monkeypatch.delenv(var, raising=False)
        f = Fantoma()
        assert not f.config.captcha.key
