"""Tests for sensitive data placeholder injection."""
from fantoma.executor import Executor


def test_replace_secrets_in_action():
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
    secrets = {"password": "Secret123"}
    text = 'Step 5: TYPE [3] "Secret123"'
    result = Executor._filter_secrets(text, secrets)
    assert "Secret123" not in result
    assert "<secret:password>" in result


def test_filter_no_secrets_unchanged():
    text = "CLICK [3]"
    result = Executor._filter_secrets(text, {})
    assert result == "CLICK [3]"
