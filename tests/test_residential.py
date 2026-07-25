"""Tests for residential proxy pools.

The behaviour that matters is stickiness: an exit IP must persist across
calls and change only when it is actually burnt. A pool that rotates on
every call throws away cookies, logins and CAPTCHA clearance.
"""

import pytest

from fantoma.browser.proxy import resolve_proxy
from fantoma.browser.residential import ProxyPool


def _port_pool(**kwargs):
    return ProxyPool.port_based(
        host="gw.example.test", port_base=10000, port_span=3,
        username="u", password="p", **kwargs,
    )


class TestStickiness:
    def test_same_ip_across_calls(self):
        """The whole point: repeated calls keep one exit IP."""
        pool = _port_pool()
        first = pool.next()["server"]
        for _ in range(5):
            assert pool.next()["server"] == first

    def test_rotate_changes_the_exit(self):
        pool = _port_pool()
        before = pool.next()["server"]
        pool.rotate()
        assert pool.next()["server"] != before

    def test_identities_cycle_when_exhausted(self):
        pool = _port_pool()
        seen = {pool.next()["server"]}
        for _ in range(3):
            pool.rotate()
            seen.add(pool.next()["server"])
        assert len(seen) == 3, "3 ports, so 3 distinct exits then wraparound"


class TestFailureHandling:
    def test_single_failure_does_not_rotate_by_default(self):
        """Not every failure is the IP's fault — tolerate one blip."""
        pool = _port_pool()
        before = pool.next()["server"]
        assert pool.report_failure("429") is False
        assert pool.next()["server"] == before

    def test_rotates_once_the_threshold_is_hit(self):
        pool = _port_pool()
        before = pool.next()["server"]
        pool.report_failure("429")
        assert pool.report_failure("429") is True
        assert pool.next()["server"] != before

    def test_success_clears_the_strike_count(self):
        pool = _port_pool()
        before = pool.next()["server"]
        pool.report_failure("429")
        pool.report_success()
        assert pool.report_failure("429") is False, "count was reset"
        assert pool.next()["server"] == before

    def test_threshold_of_one_rotates_immediately(self):
        pool = _port_pool(failures_before_rotate=1)
        before = pool.next()["server"]
        assert pool.report_failure("403") is True
        assert pool.next()["server"] != before


class TestRotationCallback:
    def test_callback_fires_with_the_retired_identity(self):
        """Used to discard CAPTCHA clearance bound to the dead IP."""
        retired = []
        pool = _port_pool(on_rotate=retired.append)
        pool.rotate()
        assert retired == [10000]

    def test_a_failing_callback_does_not_break_rotation(self):
        def boom(_):
            raise RuntimeError("re-solve failed")

        pool = _port_pool(on_rotate=boom)
        before = pool.next()["server"]
        pool.rotate()
        assert pool.next()["server"] != before


class TestProviderIdioms:
    def test_port_based_uses_one_port_per_exit(self):
        pool = _port_pool()
        assert ":10000" in pool.next()["server"]
        pool.rotate()
        assert ":10001" in pool.next()["server"]

    def test_port_based_embeds_credentials(self):
        assert "u:p@" in _port_pool().next()["server"]

    def test_session_based_puts_the_token_in_the_username(self):
        pool = ProxyPool.session_based(
            host="gw.example.test", port=7000,
            username="cust", password="pw", sessions=2,
        )
        first = pool.next()["server"]
        assert "cust-session-" in first
        pool.rotate()
        assert pool.next()["server"] != first

    def test_session_template_is_provider_configurable(self):
        pool = ProxyPool.session_based(
            host="gw.example.test", port=7000, username="cust", password="pw",
            session_template="{username}_sess_{session}", sessions=1,
        )
        assert "cust_sess_" in pool.next()["server"]

    def test_gateway_pinning_resolves_once(self, monkeypatch):
        """Gateways round-robin DNS; anything bound to egress needs it fixed."""
        calls = []

        def fake_resolve(host):
            calls.append(host)
            return "203.0.113.9"

        monkeypatch.setattr("socket.gethostbyname", fake_resolve)
        pool = _port_pool(pin_gateway_ip=True)
        assert "203.0.113.9" in pool.next()["server"]
        pool.rotate()
        assert "203.0.113.9" in pool.next()["server"]
        assert len(calls) == 1, "resolved once at construction, not per call"

    def test_falls_back_to_hostname_if_dns_fails(self, monkeypatch):
        def boom(host):
            raise OSError("no DNS")

        monkeypatch.setattr("socket.gethostbyname", boom)
        assert "gw.example.test" in _port_pool(pin_gateway_ip=True).next()["server"]


class TestFantomaIntegration:
    def test_resolve_proxy_accepts_a_pool(self):
        """Fantoma's proxy seam is anything with .next() — no plumbing needed."""
        pool = _port_pool()
        resolved = resolve_proxy(pool)
        assert resolved["server"] == pool.next()["server"]

    def test_stats_report_rotations(self):
        pool = _port_pool()
        pool.next()
        pool.rotate()
        stats = pool.stats
        assert stats["rotations"] == 1 and stats["calls"] == 1
