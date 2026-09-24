"""Domain policy: which hosts the browser may contact."""

from unittest.mock import MagicMock

import pytest

from fantoma.browser.domains import DomainBlocked, DomainPolicy


class TestPermits:
    def test_empty_policy_allows_everything(self):
        p = DomainPolicy()
        assert not p.active and p.permits("https://anything.example/x")

    def test_allow_list_includes_subdomains(self):
        p = DomainPolicy(allowed=["example.com"])
        assert p.permits("https://example.com/")
        assert p.permits("https://www.example.com/a")
        assert not p.permits("https://example.com.evil.net/")
        assert not p.permits("https://notexample.com/")

    def test_wildcard_means_subdomains_only(self):
        p = DomainPolicy(allowed=["*.example.com"])
        assert p.permits("https://api.example.com/")
        assert not p.permits("https://example.com/")

    def test_block_list_wins_over_allow_list(self):
        p = DomainPolicy(allowed=["example.com"], blocked=["ads.example.com"])
        assert not p.permits("https://ads.example.com/x.png")
        assert p.permits("https://shop.example.com/")

    def test_non_network_schemes_are_always_allowed(self):
        p = DomainPolicy(allowed=["example.com"])
        for url in ("data:text/html,hi", "about:blank", "blob:https://x/1"):
            assert p.permits(url)

    def test_comma_separated_string_and_case(self):
        p = DomainPolicy(allowed="Example.com, other.org")
        assert p.permits("https://EXAMPLE.com/") and p.permits("https://other.org/")

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("FANTOMA_ALLOWED_DOMAINS", "example.com")
        assert DomainPolicy.from_env().allowed == ["example.com"]
        assert DomainPolicy.from_env(allowed=[]).allowed == []

    def test_check_raises(self):
        with pytest.raises(DomainBlocked, match="evil.example"):
            DomainPolicy(blocked=["evil.example"]).check("https://evil.example/steal?x=1")


class TestInstall:
    def test_routes_abort_disallowed_requests(self):
        ctx = MagicMock()
        DomainPolicy(allowed=["example.com"]).install(ctx)
        handler = ctx.route.call_args[0][1]

        ok, bad = MagicMock(), MagicMock()
        ok.request.url = "https://example.com/app.js"
        bad.request.url = "https://tracker.evil/pixel.gif?c=secret"
        handler(ok)
        handler(bad)
        ok.continue_.assert_called_once()
        bad.abort.assert_called_once_with("blockedbyclient")

    def test_inactive_policy_installs_nothing(self):
        ctx = MagicMock()
        DomainPolicy().install(ctx)
        ctx.route.assert_not_called()


class TestFantomaNavigate:
    def test_blocked_navigation_reports_why(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._engine.navigate.side_effect = DomainBlocked("evil.example is not permitted")
        f._action_result = lambda ok, pre=None: {"success": ok}
        r = f.navigate("https://evil.example/")
        assert r["success"] is False and "not permitted" in r["error"]

    def test_read_refuses_to_describe_the_old_page(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f.navigate = lambda url: {"success": False, "error": "evil.example is not permitted"}
        with pytest.raises(RuntimeError, match="not permitted"):
            f.read("https://evil.example/")
