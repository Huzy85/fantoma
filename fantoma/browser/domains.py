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

WebSockets opened from pages and frames are checked too, by a constructor
guard that is locked in place (window.WebSocket cannot be reassigned or
deleted, and WebSocket.prototype.constructor is the guard). The check is on
the URL text only: a name that resolves to a loopback or private address
(127.0.0.1.nip.io) passes, because page JavaScript cannot resolve names.
Ones opened from inside a web worker are not checked at all.

Redirects, strict mode, in detail. The browser is never allowed to follow a
3xx itself. A navigation that is answered with a permitted redirect gets a
tiny page (data-fantoma-hop, no-store, locked-down CSP) that starts a fresh
navigation to the Location; that is a new request, so it comes back through
the handler and is checked again, at the moment the browser really asks. A
POST answered 307 or 308 on a navigation is refused. Subresources are
walked by the handler (every hop checked, redirects off) and the final
answer is returned in place of the first. Fantoma's own navigation helpers
wait for the extra hop with settle_hops(); code driving the page directly
sees the hop page first (page.goto resolves on it) and must wait for the
URL to change.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import ipaddress
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from urllib.parse import urljoin, urlparse

from fantoma.netutil import is_private_address, is_private_name

log = logging.getLogger("fantoma.domains")

_NETWORK_SCHEMES = {"http", "https", "ws", "wss", "ftp"}

_WS_GUARD = r"""
(() => {
  const allowed = __ALLOWED__, blocked = __BLOCKED__, blockPrivate = __BLOCK_PRIVATE__;
  const install = (window) => {
  const Native = window.WebSocket;
  if (!Native) return;
  const matches = (host, p) => p.startsWith('*.') ? host.endsWith(p.slice(1))
      : host === p || host.endsWith('.' + p);
  const NAMES = ['localhost', 'ip6-localhost', 'ip6-loopback', 'metadata',
                 'metadata.google.internal', 'instance-data', 'host.docker.internal'];
  const SUFFIXES = ['.localhost', '.internal', '.local', '.lan', '.home', '.home.arpa'];
  const v4Private = (a, b) => a === 0 || a === 10 || a === 127 || (a === 100 && b >= 64 && b <= 127)
      || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)
      || a >= 224;
  const v6Private = (h) => {
    let t = h.toLowerCase();
    if (t.includes('.')) {  // trailing dotted quad: turn it into two hex groups
      const i = t.lastIndexOf(':'), q = t.slice(i + 1).split('.').map(Number);
      if (q.length !== 4 || q.some(n => !(n >= 0 && n <= 255))) return true;
      t = t.slice(0, i + 1) + ((q[0] << 8) | q[1]).toString(16) + ':' + ((q[2] << 8) | q[3]).toString(16);
    }
    const halves = t.split('::');
    if (halves.length > 2) return true;
    const head = halves[0] ? halves[0].split(':') : [];
    const tail = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
    const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
    if (fill < 0) return true;
    const g = head.concat(Array(fill).fill('0'), tail).map(x => parseInt(x || '0', 16));
    if (g.length !== 8 || g.some(n => isNaN(n))) return true;
    const zero5 = g.slice(0, 5).every(n => n === 0);
    if (zero5 && g[5] === 0 && g[6] === 0 && g[7] <= 1) return true;           // :: and ::1
    if (zero5 && (g[5] === 0xffff || g[5] === 0)) return v4Private(g[6] >> 8, g[6] & 255); // mapped, compatible
    if (g[0] === 0x64 && g[1] === 0xff9b && g.slice(2, 6).every(n => n === 0))
      return v4Private(g[6] >> 8, g[6] & 255);                                // NAT64
    if ((g[0] & 0xfe00) === 0xfc00 || (g[0] & 0xffc0) === 0xfe80) return true; // unique-local, link-local
    return false;
  };
  const isPrivate = (host) => {
    if (host.startsWith('[') && host.endsWith(']')) return v6Private(host.slice(1, -1));
    // The URL parser has already turned 127.1, 0x7f000001 and 2130706433 into dotted decimal.
    const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(host);
    if (m) return v4Private(+m[1], +m[2]);
    return NAMES.includes(host) || SUFFIXES.some(x => host.endsWith(x));
  };
  const permits = (url) => {
    let host;
    try { host = new URL(url, window.location.href).hostname.toLowerCase().replace(/\.$/, ''); }
    catch (e) { return false; }
    if (blocked.some(p => matches(host, p))) return false;
    if (blockPrivate && isPrivate(host) && !allowed.some(p => matches(host, p))) return false;
    return !allowed.length || allowed.some(p => matches(host, p));
  };
  const Guarded = function WebSocket(url, protocols) {
    if (!permits(url)) throw new DOMException('Blocked by domain policy', 'SecurityError');
    return protocols === undefined ? new Native(url) : new Native(url, protocols);
  };
  Guarded.prototype = Native.prototype;
  // Without this, new (WebSocket.prototype.constructor)(url) is the native one.
  Object.defineProperty(Native.prototype, 'constructor',
      {value: Guarded, writable: true, configurable: true, enumerable: false});
  for (const k of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'])
    Object.defineProperty(Guarded, k, {value: Native[k]});
  Guarded.toString = Native.toString.bind(Native);
  // Locked: a page cannot delete or reassign window.WebSocket to get Native back.
  Object.defineProperty(window, 'WebSocket',
      {value: Guarded, writable: false, configurable: false, enumerable: false});
  };
  install(window);
  // Init scripts run in every frame, but Firefox-based browsers hand a script
  // a blank same-origin iframe before the init script has run in it, so
  // frame.contentWindow.WebSocket is native for a moment. Guard such a window
  // when the page first reaches for it.
  const fix = (w) => {
    try {
      const d = Object.getOwnPropertyDescriptor(w, 'WebSocket');
      if (d && d.configurable) install(w);
    } catch (e) { /* cross-origin: its own init script covers it */ }
    return w;
  };
  for (const C of [window.HTMLIFrameElement, window.HTMLFrameElement, window.HTMLObjectElement]) {
    if (!C) continue;
    const win = Object.getOwnPropertyDescriptor(C.prototype, 'contentWindow');
    const doc = Object.getOwnPropertyDescriptor(C.prototype, 'contentDocument');
    if (win && win.get) Object.defineProperty(C.prototype, 'contentWindow', {
      configurable: true, enumerable: win.enumerable, get() { const w = win.get.call(this); return w ? fix(w) : w; }});
    if (doc && doc.get) Object.defineProperty(C.prototype, 'contentDocument', {
      configurable: true, enumerable: doc.enumerable,
      get() { const d = doc.get.call(this); if (d) fix(d.defaultView); return d; }});
  }
})();
"""


def _ws_guard_script(allowed, blocked, block_private) -> str:
    return (_WS_GUARD.replace("__ALLOWED__", json.dumps(allowed))
            .replace("__BLOCKED__", json.dumps(blocked))
            .replace("__BLOCK_PRIVATE__", "true" if block_private else "false"))


def settle_hops(page, timeout_ms: int = 15000) -> bool:
    """Wait out redirect hop pages (see DomainPolicy._hop_page).

    A strict-mode redirect on a navigation is answered with a page that
    navigates on to the real target. page.goto and click-then-wait resolve on
    that page, not on the target. Call this after them to get to the end of the
    chain. Returns True when the page is not on a hop page (nothing to do, or
    settled), False when it was still waiting at the timeout. Cheap: one
    evaluate when there is nothing to wait for.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        try:
            hopping = page.evaluate("() => document.documentElement.hasAttribute('data-fantoma-hop')")
        except Exception:
            hopping = True  # context destroyed: a navigation is under way
            try:
                page.wait_for_load_state("domcontentloaded", timeout=max(1, int((deadline - time.monotonic()) * 1000)))
            except Exception:
                pass
            try:
                hopping = page.evaluate("() => document.documentElement.hasAttribute('data-fantoma-hop')")
            except Exception:
                hopping = False
        if not hopping:
            return True
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(40)


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


# Lookups run on a small shared pool. A resolver that hangs ties up one worker
# until it answers; it can never add threads, so a stream of dead names costs
# sixteen threads at most. Lookups for the same host in flight at the same time
# share one future, so a page that asks for one slow name a hundred times uses
# one worker, not the whole pool. When all sixteen are busy a new lookup is
# refused (the caller treats that as a failed lookup and fails closed).
_RESOLVE_WORKERS = 16
_resolve_pool = ThreadPoolExecutor(max_workers=_RESOLVE_WORKERS, thread_name_prefix="fantoma-dns")
_resolve_slots = threading.BoundedSemaphore(_RESOLVE_WORKERS)
_resolve_inflight: dict = {}
_resolve_lock = threading.Lock()


def _lookup(host: str):
    try:
        return socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    finally:
        _resolve_slots.release()


def _lookup_future(host: str):
    """The in-flight lookup for `host`, started if there is none."""
    with _resolve_lock:
        future = _resolve_inflight.get(host)
        if future is not None:
            return future
        if not _resolve_slots.acquire(blocking=False):
            raise OSError(f"DNS lookup for {host} refused: resolver pool is busy")
        try:
            future = _resolve_pool.submit(_lookup, host)
        except Exception:
            _resolve_slots.release()
            raise
        _resolve_inflight[host] = future

    def _done(_f, host=host, future=future):
        with _resolve_lock:
            if _resolve_inflight.get(host) is future:
                del _resolve_inflight[host]
    future.add_done_callback(_done)
    return future


def _resolve_all(host: str, timeout: float = _RESOLVE_TIMEOUT):
    """Every address `host` resolves to, as ipaddress objects. Raises OSError
    on failure, on timeout, or when the lookup pool is saturated."""
    future = _lookup_future(host)
    try:
        infos = future.result(timeout=timeout)
    except FutureTimeout:
        raise OSError(f"DNS lookup for {host} timed out") from None
    except Exception as e:  # noqa: BLE001
        raise OSError(str(e)) from None
    out = []
    for info in infos or []:
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
        # Final status of recent top-level navigations, by URL: the browser only
        # sees a 200 hop page for a redirect, so the real status is kept here.
        self.nav_status: dict[str, int] = {}

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
    _BODY_HEADERS = {"content-type", "content-encoding", "content-language", "content-length",
                     "transfer-encoding", "origin"}
    _FOLLOWED = (301, 302, 303, 307, 308)  # the codes a browser follows on its own

    @staticmethod
    def _location(response) -> str:
        if response.status not in DomainPolicy._FOLLOWED:
            return ""
        return response.headers.get("location", "") or ""

    @staticmethod
    def _origin(url: str):
        p = urlparse(url)
        return (p.scheme.lower(), (p.hostname or "").lower(), p.port or {"http": 80, "https": 443}.get(p.scheme.lower()))

    def _remember_status(self, url: str, status: int) -> None:
        with self._dns_lock:
            if len(self.nav_status) > 256:
                self.nav_status.clear()
            self.nav_status[url] = status

    def _hop_page(self, route, location: str) -> None:
        """Answer a navigation redirect with a page that makes the browser
        start a fresh top-level navigation to `location`.

        The browser never follows a 3xx here: it is handed a 200, and the next
        request is a new one that goes through the route handler again, so
        every hop is checked at the moment the browser actually asks (a server
        that answers the checker one way and the browser another gains nothing).
        location.replace keeps history clean; <noscript> covers a context with
        JavaScript off. The page carries data-fantoma-hop so Fantoma can tell it
        is waiting for the next hop (see settle_hops)."""
        nonce = base64.b64encode(os.urandom(12)).decode()
        target = json.dumps(location).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        body = ('<!doctype html><html data-fantoma-hop="1"><head><meta charset="utf-8">'
                '<title>Redirecting</title><noscript><meta http-equiv="refresh" content="0;url='
                + html.escape(location, quote=True) + '"></noscript></head><body>'
                '<script nonce="' + nonce + '">location.replace(' + target + ')</script></body></html>')
        route.fulfill(status=200, body=body, headers={
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-store",
            "x-fantoma-hop": "1",
            "content-security-policy": f"default-src 'none'; script-src 'nonce-{nonce}'",
        })

    def _refuse(self, route, why: str, src: str, dst: str = "") -> None:
        log.info("domain policy: %s %s%s", why, src[:200], f" -> {dst[:200]}" if dst else "")
        route.abort("blockedbyclient")

    def _strict_fetch(self, route, url: str) -> None:
        """Fetch with redirects off. The browser is never handed a 3xx.

        Playwright runs the route handler only for the first request of a
        chain, so a redirect the browser follows itself is not checked. Two
        cases, because they need different answers.

        Navigations (a page or frame loading): a permitted redirect is
        answered with a small page that starts a fresh navigation to the
        Location (_hop_page). That is a new request, routed and checked again,
        and the address bar ends on the right URL. A POST answered 307 or 308
        is refused: the browser would have to replay the body, which a
        follow-up page cannot do, and following it unrouted is the hole.

        Subresources (images, scripts, fetch, XHR): the chain is walked here,
        every hop checked, up to _MAX_HOPS redirects, and the final answer is
        fulfilled in place of the first. 307 and 308 replay the body only to
        the same origin; cross-origin POST replays are refused.
        """
        request = route.request
        response = route.fetch(max_redirects=0)
        location = self._location(response)
        nav = False
        try:
            nav = bool(request.is_navigation_request())
        except Exception:
            nav = False
        if not location:
            if 300 <= response.status < 400 and response.headers.get("location"):
                # A 3xx the browser would not follow (300, 304, 305...): hand it
                # back without its Location so nothing can act on it.
                headers = {k: v for k, v in response.headers.items() if k.lower() != "location"}
                route.fulfill(response=response, headers=headers)
                return
            if nav and request.frame is not None and request.frame.parent_frame is None:
                self._remember_status(url, response.status)
            route.fulfill(response=response)
            return
        method = (request.method or "GET").upper()
        nxt = urljoin(url, location)
        if urlparse(nxt).scheme.lower() not in ("http", "https") or not self.permits(nxt):
            self._refuse(route, "blocked redirect", url, location)
            return
        if nav:
            if response.status in (307, 308) and method not in ("GET", "HEAD"):
                self._refuse(route, f"refused {response.status} redirect of a {method} navigation "
                             "(the body cannot be replayed through a checked hop)", url, location)
                return
            self._hop_page(route, nxt)
            return
        self._walk_subresource(route, url, nxt, response, method)

    def _walk_subresource(self, route, url: str, nxt: str, response, method: str) -> None:
        request = route.request
        body = request.post_data_buffer if method not in ("GET", "HEAD") else None
        base = {k: v for k, v in request.headers.items() if k.lower() not in self._DROP_ON_HOP}
        origin = self._origin(url)
        current, hops = url, 1
        while True:
            status = response.status
            if status in (301, 302) and method == "POST" or status == 303 and method != "HEAD":
                method, body = "GET", None
            elif status in (307, 308) and body is not None and self._origin(nxt) != origin:
                self._refuse(route, f"refused cross-origin {status} redirect of a {method} request",
                             current, nxt)
                return
            if hops > self._MAX_HOPS:
                log.info("domain policy: too many redirects from %s", url[:200])
                route.abort("failed")
                return
            headers = base if body is not None else {
                k: v for k, v in base.items() if k.lower() not in self._BODY_HEADERS}
            # post_data is passed on every hop: Playwright reuses the original
            # request's body when it is left out, even for a GET.
            response = route.fetch(url=nxt, method=method, headers=headers, max_redirects=0,
                                   post_data=body if body is not None else b"")
            current = nxt
            location = self._location(response)
            if not location:
                route.fulfill(response=response)
                return
            hops += 1
            nxt = urljoin(current, location)
            if urlparse(nxt).scheme.lower() not in ("http", "https") or not self.permits(nxt):
                self._refuse(route, "blocked redirect", current, location)
                return

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
            context.add_init_script(_ws_guard_script(self.allowed, self.blocked, self.block_private))
        except Exception as e:
            log.warning("domain policy: could not guard websockets: %s", e)
