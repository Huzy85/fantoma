"""Optional font/media blocking: classification, chaining, attach mode."""

import logging
from types import SimpleNamespace

import pytest

from fantoma.browser.blocking import ResourceBlocker, env_enabled, should_block
from fantoma.browser.domains import DomainPolicy
from fantoma.browser.engine import BrowserEngine


class FakeRoute:
    def __init__(self, ctx, idx, url, rtype):
        self.request = SimpleNamespace(url=url, resource_type=rtype)
        self._ctx, self._idx = ctx, idx
        self.outcome = None

    def abort(self, reason=None):
        self.outcome = "abort"

    def continue_(self):
        self.outcome = "continue"

    def fallback(self):
        # Playwright: pass to the next handler registered earlier.
        self._idx -= 1
        if self._idx < 0:
            self.outcome = "network"
        else:
            self._ctx.handlers[self._idx](self)


class FakeContext:
    """Runs the most recently registered route first, like Playwright."""

    def __init__(self):
        self.handlers = []

    def add_init_script(self, *a, **k):
        pass

    def route(self, pattern, handler):
        self.handlers.append(handler)

    def request(self, url, rtype):
        r = FakeRoute(self, len(self.handlers) - 1, url, rtype)
        self.handlers[-1](r)
        return r.outcome


@pytest.mark.parametrize("rtype,url", [
    ("font", "https://x.test/a"), ("media", "https://x.test/v"),
    ("other", "https://x.test/f.woff2"), ("other", "https://x.test/f.TTF?v=1"),
    ("other", "https://x.test/clip.mp4"), ("other", "https://x.test/a.ogg"),
])
def test_blocked(rtype, url):
    assert should_block(rtype, url)


@pytest.mark.parametrize("rtype,url", [
    ("document", "https://x.test/"), ("document", "https://x.test/page.mp4"),
    ("image", "https://x.test/a.png"), ("image", "https://x.test/font.woff"),
    ("stylesheet", "https://x.test/a.css"), ("script", "https://x.test/a.js"),
    ("xhr", "https://x.test/api.mp3"), ("fetch", "https://x.test/api"),
    ("websocket", "wss://x.test/"), ("other", "https://x.test/a.png"), ("other", "https://x.test/app.js"),
])
def test_never_blocked(rtype, url):
    assert not should_block(rtype, url)


def test_blocker_counts_and_falls_through():
    ctx = FakeContext()
    b = ResourceBlocker()
    b.install(ctx)
    assert ctx.request("https://x.test/f.woff2", "font") == "abort"
    assert ctx.request("https://x.test/a.png", "image") == "network"
    assert b.blocked == 1


def test_domain_policy_still_runs_with_blocking_on():
    ctx = FakeContext()
    policy = DomainPolicy(blocked=["evil.test"], strict=False)
    policy.install(ctx)           # installed first, as in engine.start()
    ResourceBlocker().install(ctx)  # then blocking: runs first, falls back
    assert ctx.request("https://evil.test/page", "document") == "abort"
    assert ctx.request("https://evil.test/a.js", "script") == "abort"
    assert ctx.request("https://ok.test/page", "document") == "continue"
    assert ctx.request("https://ok.test/f.woff", "font") == "abort"


def test_env(monkeypatch):
    monkeypatch.delenv("FANTOMA_BLOCK_RESOURCES", raising=False)
    assert not env_enabled()
    monkeypatch.setenv("FANTOMA_BLOCK_RESOURCES", "1")
    assert env_enabled()
    assert BrowserEngine().block_resources
    assert not BrowserEngine(block_resources=False).block_resources


def _engine(monkeypatch, **kw):
    e = BrowserEngine(**kw)
    monkeypatch.setattr(e, "_start_camoufox", lambda: setattr(e, "_context", FakeContext()))
    monkeypatch.setattr(e, "_start_chromium", lambda: setattr(e, "_context", FakeContext()))
    monkeypatch.setattr(e, "_install_page_hooks", lambda: None)
    return e


def test_engine_default_off(monkeypatch):
    monkeypatch.delenv("FANTOMA_BLOCK_RESOURCES", raising=False)
    e = _engine(monkeypatch)
    e.start()
    assert e._context.handlers == [] and e.blocked_requests == 0


def test_engine_installs_when_on(monkeypatch):
    e = _engine(monkeypatch, block_resources=True)
    e.start()
    assert len(e._context.handlers) == 1
    e._context.request("https://x.test/f.woff", "font")
    assert e.blocked_requests == 1


def test_attach_mode_skips_with_one_warning(monkeypatch, caplog):
    e = _engine(monkeypatch, block_resources=True)
    e._cdp_url = "http://127.0.0.1:9222"
    with caplog.at_level(logging.WARNING):
        e.start()
    assert e._context.handlers == [] and e._blocker is None
    assert len([r for r in caplog.records if "block_resources ignored" in r.message]) == 1
