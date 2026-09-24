"""Which sites the browser may talk to.

The one prompt-injection defence that holds even when the model is fooled:
if a page talks the agent into visiting attacker.example, or into loading an
image whose URL carries a stolen value, the request never leaves the
browser. It is enforced on every request (navigations, frames, scripts,
images, fetch/XHR), not only on the pages the agent chooses to open.

Patterns are hostnames. "example.com" matches example.com and every
subdomain of it; "*.example.com" matches subdomains only. Non-network
schemes (data:, blob:, about:) are always allowed: they fetch nothing.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

_NETWORK_SCHEMES = {"http", "https", "ws", "wss", "ftp"}


class DomainBlocked(Exception):
    """Raised when navigation targets a site the policy does not permit."""


def _split(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [v.strip().lower().rstrip(".") for v in value if v and v.strip()]


def _matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        base = pattern[2:]
        return host.endswith("." + base)
    return host == pattern or host.endswith("." + pattern)


class DomainPolicy:
    """Allow-list and block-list of hostnames. Empty lists allow everything."""

    def __init__(self, allowed=None, blocked=None):
        self.allowed = _split(allowed)
        self.blocked = _split(blocked)

    @classmethod
    def from_env(cls, allowed=None, blocked=None) -> "DomainPolicy":
        """Explicit arguments win; otherwise FANTOMA_ALLOWED_DOMAINS and
        FANTOMA_BLOCKED_DOMAINS (comma-separated) are used."""
        return cls(
            allowed if allowed is not None else os.environ.get("FANTOMA_ALLOWED_DOMAINS", ""),
            blocked if blocked is not None else os.environ.get("FANTOMA_BLOCKED_DOMAINS", ""),
        )

    @property
    def active(self) -> bool:
        return bool(self.allowed or self.blocked)

    def permits(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme.lower() not in _NETWORK_SCHEMES:
            return True
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return False
        if any(_matches(host, p) for p in self.blocked):
            return False
        if self.allowed and not any(_matches(host, p) for p in self.allowed):
            return False
        return True

    def check(self, url: str) -> None:
        if not self.permits(url):
            raise DomainBlocked(f"{urlparse(url).hostname or url} is not permitted by the "
                                "domain policy (allowed_domains / blocked_domains)")

    def install(self, context) -> None:
        """Abort every request from `context` that the policy does not permit."""
        if not self.active or context is None:
            return

        def _route(route):
            try:
                if self.permits(route.request.url):
                    route.continue_()
                else:
                    route.abort("blockedbyclient")
            except Exception:
                pass

        context.route("**/*", _route)
