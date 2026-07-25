"""Residential proxy pools — sticky exit IPs that rotate on failure.

`ProxyRotator` in proxy.py rotates on every call, which is right for a list
of datacentre proxies but wrong for residential pools. Residential providers
give you one exit IP per *session*, and you hold it: rotating mid-flow throws
away cookies, logins, and any anti-bot clearance tied to that IP. You rotate
when the IP gets blocked, not on a timer.

Two provider idioms are covered, which is nearly all of them:

- **port** — one port per exit IP. Rotating means connecting to the next port.
- **session** — a session token embedded in the username (Bright Data,
  Oxylabs, Smartproxy). Rotating means minting a new token.

Anything with a `.next()` method already works as a Fantoma proxy, so a pool
drops straight in:

    from fantoma.browser.residential import ProxyPool

    pool = ProxyPool.port_based(
        host="gw.example-provider.com", port_base=10000, port_span=100,
        username="user", password="pass", pin_gateway_ip=True,
    )
    agent = Agent(proxy=pool)
    ...
    pool.report_failure()      # 429/403 — too many strikes and it rotates

## The one thing that will catch you out

A CAPTCHA solution is bound to the exit IP it was solved through. Cloudflare
clearance obtained on IP A is worthless from IP B. So a solver and a proxy
pool are not independent plugins: solve through the same proxy you then browse
through, and treat "rotate the IP" and "discard the clearance" as one action.
`on_rotate` exists for exactly that — register a callback that clears whatever
was bound to the old IP.
"""

from __future__ import annotations

import itertools
import logging
import socket
import threading
import uuid

log = logging.getLogger("fantoma.proxy.residential")

DEFAULT_FAILURES_BEFORE_ROTATE = 2


class ProxyPool:
    """A residential pool that holds one exit IP until it stops working.

    Use the `port_based` or `session_based` constructors rather than calling
    this directly — they encode the two provider idioms.
    """

    def __init__(
        self,
        build_server,
        identities,
        *,
        username: str = "",
        password: str = "",
        failures_before_rotate: int = DEFAULT_FAILURES_BEFORE_ROTATE,
        on_rotate=None,
    ):
        """
        Args:
            build_server: (identity, username, password) -> proxy dict.
            identities: iterable of identities, cycled. Each is one exit IP.
            failures_before_rotate: strikes against the current IP before it
                is replaced. 1 rotates on any failure; the default tolerates
                a single blip, since not every failure is the IP's fault.
            on_rotate: called with the retired identity whenever the IP
                changes. Use it to discard CAPTCHA clearance and cookies
                bound to the old IP.
        """
        self._build_server = build_server
        self._identities = itertools.cycle(list(identities))
        self._username = username
        self._password = password
        self._failures_before_rotate = max(1, failures_before_rotate)
        self._on_rotate = on_rotate

        self._lock = threading.Lock()
        self._current = next(self._identities)
        self._failures = 0
        self._rotations = 0
        self._calls = 0

    # ── Provider idioms ──────────────────────────────────────────────────

    @classmethod
    def port_based(
        cls,
        host: str,
        port_base: int,
        port_span: int,
        *,
        username: str = "",
        password: str = "",
        scheme: str = "http",
        pin_gateway_ip: bool = False,
        **kwargs,
    ) -> "ProxyPool":
        """One port per exit IP — the most common residential idiom.

        pin_gateway_ip resolves the hostname once and uses the literal IP
        thereafter. Gateways often round-robin DNS across several hosts, and
        anything that binds to your egress — a CAPTCHA solver especially —
        needs the address to stay put for the life of the session.
        """
        resolved = host
        if pin_gateway_ip:
            try:
                resolved = socket.gethostbyname(host)
                log.info("Pinned gateway %s -> %s", host, resolved)
            except OSError as e:
                log.warning("Could not pin gateway %s (%s) — using hostname", host, e)

        def build(identity, user, pw):
            creds = f"{user}:{pw}@" if user else ""
            return {"server": f"{scheme}://{creds}{resolved}:{identity}"}

        ports = range(port_base, port_base + max(1, port_span))
        return cls(build, ports, username=username, password=password, **kwargs)

    @classmethod
    def session_based(
        cls,
        host: str,
        port: int,
        *,
        username: str,
        password: str = "",
        session_template: str = "{username}-session-{session}",
        sessions: int = 100,
        scheme: str = "http",
        **kwargs,
    ) -> "ProxyPool":
        """A session token inside the username (Bright Data, Oxylabs, etc.).

        session_template is provider-specific — check their docs for the exact
        separator, it varies and a wrong one silently gives you a random IP
        every request instead of a sticky one.
        """
        def build(identity, user, pw):
            stamped = session_template.format(username=user, session=identity)
            creds = f"{stamped}:{pw}@" if pw else f"{stamped}@"
            return {"server": f"{scheme}://{creds}{host}:{port}"}

        tokens = [uuid.uuid4().hex[:8] for _ in range(max(1, sessions))]
        return cls(build, tokens, username=username, password=password, **kwargs)

    # ── Fantoma proxy interface ──────────────────────────────────────────

    def next(self) -> dict:
        """Return the current sticky proxy.

        Named `next()` for compatibility with resolve_proxy, but it does not
        advance — that is the point. Call rotate() or report_failure() to
        change IP.
        """
        with self._lock:
            self._calls += 1
            return self._build_server(self._current, self._username, self._password)

    # ── Lifecycle ────────────────────────────────────────────────────────

    def report_success(self) -> None:
        """Clear the strike count — the current IP is working."""
        with self._lock:
            self._failures = 0

    def report_failure(self, reason: str = "") -> bool:
        """Record a block against the current IP. Returns True if it rotated.

        Call on 429, 403, or a challenge page — the signals that mean this
        exit IP is burnt rather than the request being wrong.
        """
        with self._lock:
            self._failures += 1
            log.info(
                "Proxy failure %d/%d%s",
                self._failures, self._failures_before_rotate,
                f" ({reason})" if reason else "",
            )
            if self._failures < self._failures_before_rotate:
                return False
        self.rotate()
        return True

    def rotate(self) -> dict:
        """Move to a fresh exit IP and return it."""
        with self._lock:
            retired = self._current
            self._current = next(self._identities)
            self._failures = 0
            self._rotations += 1
            log.info("Rotated exit IP (rotation #%d)", self._rotations)
            current = self._build_server(self._current, self._username, self._password)

        # Outside the lock: the callback may do real work (re-solving a
        # CAPTCHA), and it must not be able to deadlock the pool.
        if self._on_rotate is not None:
            try:
                self._on_rotate(retired)
            except Exception as e:
                log.warning("on_rotate callback failed: %s", e)
        return current

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "calls": self._calls,
                "rotations": self._rotations,
                "failures_on_current": self._failures,
            }
