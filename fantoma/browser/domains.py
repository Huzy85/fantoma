"""Which sites the browser may talk to.

The one prompt-injection defence that holds even when the model is fooled:
if a page talks the agent into visiting attacker.example, or into loading an
image whose URL carries a stolen value, the request never leaves the
browser. It is enforced on every request (navigations, frames, scripts,
images, fetch/XHR), not only on the pages the agent chooses to open.

Patterns are hostnames. "example.com" matches example.com and every
subdomain of it; "*.example.com" matches subdomains only. Unicode names are
converted to their punycode form. Inert schemes (data:, blob:, about:blank)
are always allowed: they fetch nothing. Every other non-network scheme
(file:, chrome:, view-source:, resource:, javascript: and so on) is always
refused, with or without a policy, because it reads the machine, not the web.

Private hosts (loopback, RFC1918, link-local, cloud metadata) are refused when
a policy is active, and also when block_private is on (FANTOMA_BLOCK_PRIVATE=1,
which the HTTP server sets unless FANTOMA_ALLOW_PRIVATE=1). Direct Python use
leaves them reachable by default so local development keeps working.

With block_private on, host names are also resolved (socket.getaddrinfo,
3 s timeout, cached 60 s per policy) and refused when any address is private
or the lookup fails. Limit: the browser does its own lookup later, so a DNS
rebinding answer that changes between our lookup and the browser's can still
get through. Closing that gap needs the browser to use our resolution, which
only a proxy can do; that is out of scope here.

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
import ipaddress
import os
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlparse

from fantoma.netutil import is_private_address, is_private_name

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


# Schemes that fetch nothing and are safe whatever the policy says.
_INERT_SCHEMES = {"data", "blob", "about", ""}

_IPV4_LOOSE = re.compile(r"^[0-9a-fx.]+$")


def _parse_ip(candidate: str):
    """IPv4/IPv6 object for a literal, including the shorthand forms
    browsers accept (127.1, 0x7f000001, 0177.0.0.1, 2130706433). None if the
    text is not an address."""
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    if candidate and _IPV4_LOOSE.match(candidate):
        try:
            return ipaddress.ip_address(socket.inet_aton(candidate))
        except (OSError, ValueError):
            return None
    return None


def _is_private_host(host: str) -> bool:
    """True for loopback, link-local, private-range, CGNAT and metadata hosts.

    Covers IP literals (IPv4, IPv6, IPv4-mapped IPv6 and the shorthand
    forms 127.1, 0x7f000001, 0177.0.0.1, 2130706433), the 100.64.0.0/10
    carrier-grade NAT range, plus the names that are local by convention
    (localhost, .local, .internal, .lan, .home) and the cloud metadata names.
    This is a purely textual check. A public-looking name that resolves to a
    private address (127.0.0.1.nip.io) is caught by _resolves_private, which
    DomainPolicy applies when block_private is on.
    """
    host = host.strip().strip("[]").rstrip(".").lower()
    if is_private_name(host):
        return True
    addr = _parse_ip(host)
    if addr is None:
        return False
    return is_private_address(addr)


_RESOLVE_TIMEOUT = 3.0
_RESOLVE_TTL = 60.0


def _resolve_all(host: str, timeout: float = _RESOLVE_TIMEOUT):
    """Every address `host` resolves to, as ipaddress objects. Raises OSError
    on failure or timeout. The lookup runs in a daemon thread so a hung
    resolver cannot stall the caller past `timeout`."""
    box: dict = {}

    def _work():
        try:
            box["infos"] = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise OSError(f"DNS lookup for {host} timed out")
    if "err" in box:
        raise OSError(str(box["err"]))
    out = []
    for info in box.get("infos", []):
        try:
            out.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except ValueError:
            continue
    if not out:
        raise OSError(f"DNS lookup for {host} returned no addresses")
    return out


def _resolves_private(host: str) -> bool:
    """True when ANY address `host` resolves to is private (same test as
    _is_private_host, CGNAT included), or when it cannot be resolved at all
    (fail closed). IP literals are decided textually and never looked up."""
    host = host.strip().strip("[]").rstrip(".").lower()
    if _parse_ip(host) is not None:
        return _is_private_host(host)
    try:
        return any(is_private_address(a) for a in _resolve_all(host))
    except OSError as e:
        log.info("domain policy: cannot resolve %s (%s), refusing", host, e)
        return True


def _matches(host: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        base = pattern[2:]
        return host.endswith("." + base)
    return host == pattern or host.endswith("." + pattern)


class DomainPolicy:
    """Allow-list and block-list of hostnames. Empty lists allow everything."""

    def __init__(self, allowed=None, blocked=None, strict=None, block_private=None):
        self.block_private = bool(block_private)
        self.allowed = _split(allowed)
        self.blocked = _split(blocked)
        if strict is None:
            strict = os.environ.get("FANTOMA_DOMAIN_STRICT", "1").strip().lower() not in ("0", "false", "no")
        self.strict = bool(strict)
        self._dns_cache: dict[str, tuple[float, bool]] = {}
        self._dns_lock = threading.Lock()

    def _dns_private(self, host: str) -> bool:
        """_resolves_private with a short per-policy cache (60 s)."""
        now = time.monotonic()
        with self._dns_lock:
            hit = self._dns_cache.get(host)
            if hit and now - hit[0] < _RESOLVE_TTL:
                return hit[1]
        verdict = _resolves_private(host)
        with self._dns_lock:
            if len(self._dns_cache) > 2048:
                self._dns_cache.clear()
            self._dns_cache[host] = (time.monotonic(), verdict)
        return verdict

    @classmethod
    def from_env(cls, allowed=None, blocked=None) -> "DomainPolicy":
        """Explicit arguments win; otherwise FANTOMA_ALLOWED_DOMAINS and
        FANTOMA_BLOCKED_DOMAINS (comma-separated) are used."""
        return cls(
            allowed if allowed is not None else os.environ.get("FANTOMA_ALLOWED_DOMAINS", ""),
            blocked if blocked is not None else os.environ.get("FANTOMA_BLOCKED_DOMAINS", ""),
            block_private=os.environ.get("FANTOMA_BLOCK_PRIVATE", "").strip().lower() in ("1", "true", "yes"),
        )

    @property
    def active(self) -> bool:
        return bool(self.allowed or self.blocked or self.block_private)

    def permits(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        scheme = parsed.scheme.lower()
        if scheme not in _NETWORK_SCHEMES:
            # data:/blob:/about:blank fetch nothing. file:, chrome:, view-source:,
            # about:config and friends read the machine, not the network, so
            # they are refused whether or not a policy is active: a page
            # cannot steer the agent into reading /etc/passwd.
            if scheme == "about":
                return url.strip().lower() == "about:blank"
            return scheme in _INERT_SCHEMES
        host = _to_ascii((parsed.hostname or "").lower().rstrip("."))
        if not host:
            return False
        if any(_matches(host, p) for p in self.blocked):
            return False
        if self.allowed and not any(_matches(host, p) for p in self.allowed):
            return False
        # With a policy on, loopback / link-local / RFC1918 / metadata
        # addresses are off limits unless named in the allow-list. A
        # block-list alone used to leave 169.254.169.254 and 127.0.0.1
        # wide open to a page that talked the model into visiting them.
        if self.active and _is_private_host(host) and not any(_matches(host, p) for p in self.allowed):
            return False
        # block_private also resolves the name: 127.0.0.1.nip.io and similar
        # look public but point at the local network. Refused if any address
        # is private, or if the lookup fails. Skipped only when the allow-list
        # itself names a private host the operator chose to open.
        if self.block_private and not any(
                _matches(host, p) and _is_private_host(p.removeprefix("*.")) for p in self.allowed):
            if self._dns_private(host):
                return False
        return True

    def check(self, url: str) -> None:
        if not self.permits(url):
            raise DomainBlocked(f"{urlparse(url).hostname or url} is not permitted by the "
                                "domain policy (allowed_domains / blocked_domains)")

    _MAX_HOPS = 10
    _DROP_ON_HOP = {"cookie", "authorization", "proxy-authorization", "host", "content-length"}

    def _strict_fetch(self, route, url: str) -> None:
        """Fetch with redirects off and walk the whole chain ourselves.

        The browser does not run the route handler again for every hop it
        follows on its own (measured in Chromium and Camoufox: a second
        redirect reached a host the policy refuses). So each Location is
        checked here, up to _MAX_HOPS hops, before anything is handed back.
        A chain that passes is returned to the browser as a single redirect
        to the last verified URL, so the page keeps the right address. Only
        GET and HEAD are followed: replaying a POST (307/308) just to check
        it would send it twice, so for those the first Location is checked
        and nothing more.
        """
        response = route.fetch(max_redirects=0)
        method = (route.request.method or "GET").upper()
        follow = method in ("GET", "HEAD")
        current, final = url, None
        for _ in range(self._MAX_HOPS):
            if not 300 <= response.status < 400:
                break
            location = response.headers.get("location", "")
            if not location:
                break
            nxt = urljoin(current, location)
            if not self.permits(nxt):
                log.info("domain policy: blocked redirect %s -> %s", current[:200], location[:200])
                route.abort("blockedbyclient")
                return
            if not follow:
                break
            headers = {k: v for k, v in route.request.headers.items()
                       if k.lower() not in self._DROP_ON_HOP}
            current = nxt
            response = route.fetch(url=nxt, headers=headers, max_redirects=0)
            final = nxt
        else:
            log.info("domain policy: too many redirects from %s", url[:200])
            route.abort("failed")
            return
        if final is None:
            route.fulfill(response=response)
            return
        # Hand the browser one redirect to the verified end of the chain.
        route.fulfill(status=302, headers={"location": final, "cache-control": "no-store"}, body="")

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
                self._strict_fetch(route, url)
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
