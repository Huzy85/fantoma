"""Pytest configuration for Fantoma test suite.

Excludes live/integration test files from automatic collection.
These files hit real websites, require network access, API keys,
and running services. Run them manually:
    python tests/live_reddit_test.py
    python tests/real_site_test.py
    python tests/real_signup_test.py
    python tests/full_signup_test.py
    python tests/scenario_test_deepseek.py
"""

collect_ignore = [
    "live_reddit_test.py",
    "real_site_test.py",
    "real_signup_test.py",
    "full_signup_test.py",
    "scenario_test_deepseek.py",
]


import ipaddress as _ipaddress

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "real_dns: do not stub the domain policy's DNS lookup")


@pytest.fixture(autouse=True)
def _offline_dns(request, monkeypatch):
    """The unit suite must not depend on DNS: public-looking names resolve to a
    documentation address (so block_private lets them through), IP literals
    resolve to themselves. Tests that exercise the lookup itself carry the
    real_dns marker and patch socket.getaddrinfo on their own."""
    if request.node.get_closest_marker("real_dns"):
        return
    from fantoma.browser import domains

    def fake(host, timeout=domains._RESOLVE_TIMEOUT):
        try:
            return [_ipaddress.ip_address(host)]
        except ValueError:
            return [_ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(domains, "_resolve_all", fake)


@pytest.fixture(autouse=True)
def _no_block_private_leak(monkeypatch):
    """server.py turns FANTOMA_BLOCK_PRIVATE on when imported; keep that out of
    unrelated tests. Restored after each test."""
    monkeypatch.setenv("FANTOMA_BLOCK_PRIVATE", "")
