"""Which sites the browser may talk to.

The one prompt-injection defence that holds even when the model is fooled:
if a page talks the agent into visiting attacker.example, or into loading an
image whose URL carries a stolen value, the request never leaves the
browser. It is enforced on every request (navigations, frames, scripts,
images, fetch/XHR), not only on the pages the agent chooses to open.

Patterns are hostnames. "example.com" matches example.com and every
subdomain of it; "*.example.com" matches subdomains only. Unicode names are
converted to their punycode form. Non-network schemes (data:, blob:, about:)
are always allowed: they fetch nothing.

Redirects need care. Playwright calls a route handler only for the first URL
of a redirect chain, so an allowed URL that answers 302 to another host would
otherwise be followed straight past the policy. In strict mode (the default)
each request is therefore fetched with redirects off, the Location is checked,
and only then is the response handed to the page. The cost: those requests
go out through Playwright's own network client rather than the browser's, so
their TLS/HTTP fingerprint is not the browser's. strict=False (or
FANTOMA_DOMAIN_STRICT=0) keeps the browser's own networking and accepts that
a redirect from an allowed host to another host is not stopped.

WebSockets opened from pages and frames are checked too. Ones opened from
inside a web worker are not.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urljoin, urlparse

log = logging.getLogger("fantoma.domains")

_NETWORK_SCHEMES = {"http", "https", "ws", "wss", "ftp"}

_WS_GUARD = """
(() => {
  const allowed = %s, blocked = %s;
  const Native = window.WebSocket;
  if (!Native) return;
  const matches = (host, p) => p.startsWith('*.') ? host.endsWith(p.slice(1))
      : host === p || host.endsWith('.' + p);
  const permits = (url) => {
    let host;
    try { host = new URL(url, location.href).hostname.toLowerCase().replace(/\\.$/, ''); }
    catch (e) { return false; }
    if (blocked.some(p => matches(host, p))) return false;
    return !allowed.length || allowed.some(p => matches(host, p));
  };
  const Guarded = function WebSocket(url, protocols) {
    if (!permits(url)) throw new DOMException('Blocked by domain policy', 'SecurityError');
    return protocols === undefined ? new Native(url) : new Native(url, protocols);
  };
  Guarded.prototype = Native.prototype;
  for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'])
    Object.defineProperty(Guarded, k, {value: Native[k]});
  Guarded.toString = Native.toString.bind(Native);
  window.WebSocket = Guarded;
})();
"""


class DomainBlocked(Exception):
    """Raised when navigation targets a site the policy does not permit."""


def _split(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [_to_ascii(v.strip().lower().rstrip(".")) for v in value if v and v.strip()]


def _to_ascii(pattern: str) -> str:
    """Punycode a hostname pattern so "bücher.de" also matches xn--bcher-kva.de."""
    prefix = "*." if pattern.startswith("*.") else ""
    host = pattern[len(prefix):]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        pass
    return prefix + host


def _matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        base = pattern[2:]
        return host.endswith("." + base)
    return host == pattern or host.endswith("." + pattern)


class DomainPolicy:
    """Allow-list and block-list of hostnames. Empty lists allow everything."""

    def __init__(self, allowed=None, blocked=None, strict=None):
        self.allowed = _split(allowed)
        self.blocked = _split(blocked)
        if strict is None:
            strict = os.environ.get("FANTOMA_DOMAIN_STRICT", "1").strip().lower() not in ("0", "false", "no")
        self.strict = bool(strict)

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
        host = _to_ascii((parsed.hostname or "").lower().rstrip("."))
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
        """Enforce the policy on every request `context` makes."""
        if not self.active or context is None:
            return

        def _route(route):
            try:
                url = route.request.url
                if not self.permits(url):
                    log.info("domain policy: blocked %s", url[:200])
                    route.abort("blockedbyclient")
                    return
                if not self.strict:
                    route.continue_()
                    return
                response = route.fetch(max_redirects=0)
                if 300 <= response.status < 400:
                    location = response.headers.get("location", "")
                    if location and not self.permits(urljoin(url, location)):
                        log.info("domain policy: blocked redirect %s -> %s", url[:200], location[:200])
                        route.abort("blockedbyclient")
                        return
                route.fulfill(response=response)
            except Exception as e:
                # Never leave a request hanging: an unresolved route stalls
                # the page until it times out.
                log.debug("domain policy route error for %s: %s", route.request.url[:200], e)
                try:
                    route.abort()
                except Exception:
                    pass

        context.route("**/*", _route)

        # WebSockets are not seen by context.route, and Playwright's
        # route_web_socket deadlocked the sync API in testing, so the
        # constructor is guarded in the page instead. It throws the same
        # SecurityError a browser throws for a forbidden port.
        try:
            context.add_init_script(_WS_GUARD % (json.dumps(self.allowed), json.dumps(self.blocked)))
        except Exception as e:
            log.warning("domain policy: could not guard websockets: %s", e)
