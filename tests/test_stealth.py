# tests/test_stealth.py
"""Phase 3 — opt-in WebRTC/WebGL stealth kill-switches."""

import pytest

from fantoma.browser.stealth import get_camoufox_config


def test_switches_off_by_default(monkeypatch):
    monkeypatch.delenv("FANTOMA_BLOCK_WEBRTC", raising=False)
    monkeypatch.delenv("FANTOMA_DISABLE_WEBGL", raising=False)
    cfg = get_camoufox_config()
    assert "block_webrtc" not in cfg
    assert "webgl.disabled" not in cfg["firefox_user_prefs"]


def test_block_webrtc_env(monkeypatch):
    monkeypatch.setenv("FANTOMA_BLOCK_WEBRTC", "1")
    cfg = get_camoufox_config()
    assert cfg["block_webrtc"] is True


def test_disable_webgl_env(monkeypatch):
    monkeypatch.setenv("FANTOMA_DISABLE_WEBGL", "true")
    cfg = get_camoufox_config()
    assert cfg["firefox_user_prefs"]["webgl.disabled"] is True


def test_base_config_intact(monkeypatch):
    monkeypatch.delenv("FANTOMA_BLOCK_WEBRTC", raising=False)
    cfg = get_camoufox_config()
    assert "navigator.hardwareConcurrency" in cfg["config"]
    assert cfg["i_know_what_im_doing"] is True
