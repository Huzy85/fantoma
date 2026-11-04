"""Attaching to a running Chrome: discovery, host policy, tab ownership."""
import pytest

from fantoma import Fantoma
from fantoma.browser import cdp
from fantoma.browser.engine import BrowserEngine


class FakePage:
    def __init__(self, ctx, opener=None):
        self.ctx, self._opener, self.closed, self.url = ctx, opener, False, "about:blank"

    def opener(self):
        return self._opener

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True
        if self in self.ctx.pages:
            self.ctx.pages.remove(self)

    def on(self, *a, **k):
        pass


class FakeContext:
    def __init__(self):
        self.pages = [FakePage(self)]  # the user's tab
        self.user_tab = self.pages[0]

    def new_page(self):
        p = FakePage(self)
        self.pages.append(p)
        return p

    def on(self, *a, **k):
        pass


class FakeBrowser:
    def __init__(self, ctx):
        self.contexts = [ctx]
        self.closed = False

    def close(self):
        self.closed = True


class FakePW:
    def __init__(self, browser):
        self.chromium = self
        self.browser, self.stopped, self.url = browser, False, None

    def connect_over_cdp(self, url):
        self.url = url
        return self.browser

    def stop(self):
        self.stopped = True


@pytest.fixture
def attached(monkeypatch):
    ctx = FakeContext()
    browser = FakeBrowser(ctx)
    pw = FakePW(browser)
    import patchright.sync_api as sa
    monkeypatch.setattr(sa, "sync_playwright", lambda: type("S", (), {"start": lambda self: pw})())
    eng = BrowserEngine(browser_engine="chromium", cdp_url="http://127.0.0.1:9222")
    eng.start()
    return eng, ctx, browser, pw


def test_opens_own_tab_and_leaves_users_tab(attached):
    eng, ctx, _, pw = attached
    assert pw.url == "http://127.0.0.1:9222"
    assert eng.get_page() is not ctx.user_tab
    assert len(ctx.pages) == 2


def test_stop_closes_only_own_tabs_and_never_the_browser(attached):
    eng, ctx, browser, pw = attached
    eng.new_tab()
    stray = ctx.new_page()  # a tab the user opened meanwhile
    assert len(ctx.pages) == 4
    eng.stop()
    assert ctx.pages == [ctx.user_tab, stray]
    assert not browser.closed
    assert pw.stopped


def test_popup_from_own_tab_is_owned_user_tab_is_not(attached):
    eng, ctx, *_ = attached
    popup = FakePage(ctx, opener=eng.get_page())
    ctx.pages.append(popup)
    eng._on_page(popup)
    other = FakePage(ctx)
    ctx.pages.append(other)
    eng._on_page(other)
    eng.stop()
    assert popup.closed and not other.closed and not ctx.user_tab.closed


def test_tabs_hide_users_tabs_and_close_tab_refuses_them(attached):
    eng, ctx, *_ = attached
    assert len(eng.tabs()) == 1 and eng.tab_count() == 1
    eng.close_tab(0)  # index 0 of OUR tabs, not the user's
    assert ctx.user_tab in ctx.pages and not ctx.user_tab.closed


def test_camoufox_refused():
    with pytest.raises(ValueError, match="chromium"):
        BrowserEngine(browser_engine="camoufox", cdp_url="http://127.0.0.1:9222")
    with pytest.raises(ValueError, match="chromium"):
        Fantoma(cdp_url="http://127.0.0.1:9222")
    with pytest.raises(ValueError, match="chromium"):
        Fantoma(cdp_url="auto", browser="camoufox")


def test_env_var_also_refused_for_camoufox(monkeypatch):
    monkeypatch.setenv("FANTOMA_CDP_URL", "http://127.0.0.1:9222")
    with pytest.raises(ValueError):
        Fantoma()


@pytest.mark.parametrize("url", ["http://192.0.2.5:9222", "http://example.com:9222",
                                 "ws://10.0.0.1:9222/devtools/browser/x"])
def test_remote_host_refused(url):
    with pytest.raises(ValueError, match="allow_remote_cdp"):
        BrowserEngine(browser_engine="chromium", cdp_url=url)
    with pytest.raises(ValueError, match="allow_remote_cdp"):
        Fantoma(browser="chromium", cdp_url=url)


def test_remote_host_allowed_when_opted_in():
    e = BrowserEngine(browser_engine="chromium", cdp_url="http://192.0.2.5:9222",
                      allow_remote_cdp=True)
    assert e.attached


@pytest.mark.parametrize("url", ["http://127.0.0.1:9222", "http://localhost:9222",
                                 "http://[::1]:9222", "ws://127.0.0.1:9222/devtools/browser/x"])
def test_loopback_accepted(url):
    assert cdp.check_cdp_url(url) == url


def test_garbage_url_refused():
    with pytest.raises(ValueError):
        cdp.check_cdp_url("not a url")
    with pytest.raises(ValueError):
        cdp.check_cdp_url("file:///etc/passwd")


def test_parse_active_port():
    assert cdp.parse_active_port("9333\n/devtools/browser/abc-123\n") == \
        "ws://127.0.0.1:9333/devtools/browser/abc-123"
    assert cdp.parse_active_port("9333\n") is None
    assert cdp.parse_active_port("x\n/p\n") is None
    assert cdp.parse_active_port("99999\n/p\n") is None
    assert cdp.parse_active_port("9333\nnopath\n") is None
    assert cdp.parse_active_port("") is None


def test_auto_discovery_reads_file_then_falls_back(tmp_path):
    empty, good = tmp_path / "a", tmp_path / "b"
    empty.mkdir(); good.mkdir()
    (good / "DevToolsActivePort").write_text("9444\n/devtools/browser/zz\n")
    assert cdp.discover_cdp_url([empty, good]) == "ws://127.0.0.1:9444/devtools/browser/zz"
    assert cdp.discover_cdp_url([empty]) == "http://127.0.0.1:9222"
    assert cdp.resolve_cdp_url("auto") in (cdp.DEFAULT_CDP_URL,) or cdp.resolve_cdp_url("auto").startswith("ws://")


def test_profile_dirs_cover_platforms(monkeypatch, tmp_path):
    monkeypatch.setattr(cdp.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    names = [str(p) for p in cdp.profile_dirs()]
    assert any(n.endswith("google-chrome") for n in names) and any(n.endswith("chromium") for n in names)
    monkeypatch.setattr(cdp.sys, "platform", "darwin")
    assert any("Application Support" in str(p) for p in cdp.profile_dirs())
    monkeypatch.setattr(cdp.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert any(str(p).endswith("User Data") for p in cdp.profile_dirs())


def test_ignored_settings_warn_once(caplog):
    import fantoma.browser.engine as eng_mod
    eng_mod._ATTACH_WARNED = False
    with caplog.at_level("WARNING", logger="fantoma.browser"):
        BrowserEngine(browser_engine="chromium", cdp_url="http://127.0.0.1:9222",
                      profile_dir="/x", proxy="http://p:1")
        BrowserEngine(browser_engine="chromium", cdp_url="http://127.0.0.1:9222",
                      profile_dir="/x")
    warns = [r for r in caplog.records if "ignoring" in r.getMessage()]
    assert len(warns) == 1 and "profile_dir" in warns[0].getMessage() and "proxy" in warns[0].getMessage()


def test_cli_picks_chromium_with_cdp_url(monkeypatch):
    from fantoma import cli
    seen = {}
    monkeypatch.setattr(cli, "cmd_read", lambda url, **kw: seen.update(kw))
    monkeypatch.setattr("sys.argv", ["fantoma", "read", "example.com", "--cdp-url", "auto"])
    cli.main()
    assert seen["cdp_url"] == "auto" and seen["browser"] == "chromium"
