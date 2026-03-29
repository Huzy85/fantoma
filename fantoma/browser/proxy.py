"""Proxy rotation — automatic IP rotation across sessions.

Supports:
- Single proxy (static IP)
- Proxy list rotation (round-robin or random)
- ProtonVPN SOCKS5 (multiple server locations)
- Any SOCKS5/HTTP proxy provider

Usage:
    # Single proxy (your VPN app's local proxy)
    agent = Agent(proxy="socks5://localhost:1080")

    # Auto-rotate through a list of proxies
    agent = Agent(proxy=[
        "socks5://user:pass@us-server:1080",
        "socks5://user:pass@uk-server:1080",
        "socks5://user:pass@nl-server:1080",
    ])

    # VPN provider with server rotation
    from fantoma.browser.proxy import VPNProxy
    agent = Agent(proxy=VPNProxy(
        servers=["us-server.vpn.com", "uk-server.vpn.com"],
        port=1080, protocol="socks5",
        username="vpn-user", password="vpn-pass",
    ))
"""

import logging
import random

log = logging.getLogger("fantoma.proxy")


class ProxyRotator:
    """Rotates through a list of proxies. Each call to next() returns a different one."""

    def __init__(self, proxies: list[str | dict], strategy: str = "round-robin"):
        """
        Args:
            proxies: List of proxy URLs or dicts.
            strategy: "round-robin" (sequential) or "random"
        """
        self.proxies = proxies
        self.strategy = strategy
        self._index = 0
        self._used = 0

    def next(self) -> dict | None:
        """Get the next proxy in rotation. Returns Playwright proxy dict."""
        if not self.proxies:
            return None

        if self.strategy == "random":
            proxy = random.choice(self.proxies)
        else:
            proxy = self.proxies[self._index % len(self.proxies)]
            self._index += 1

        self._used += 1

        if isinstance(proxy, str):
            result = {"server": proxy}
        else:
            result = proxy

        server = result.get("server", "")
        log.info("Proxy #%d: %s", self._used, _mask_proxy(server))
        return result

    @property
    def total_used(self) -> int:
        return self._used


class VPNProxy:
    """VPN provider SOCKS5/HTTP proxy integration.

    Works with any VPN that exposes a proxy endpoint.
    Provide the server list yourself — we rotate through them.

    Usage:
        # ProtonVPN (get SOCKS5 credentials from account dashboard)
        agent = Agent(proxy=VPNProxy(
            servers=[
                "node-us-01.protonvpn.net",
                "node-uk-01.protonvpn.net",
                "node-nl-01.protonvpn.net",
            ],
            port=1080,
            protocol="socks5",
            username="protonvpn-openvpn-username",
            password="protonvpn-openvpn-password",
        ))

        # NordVPN (SOCKS5 available on some servers)
        agent = Agent(proxy=VPNProxy(
            servers=["us5311.nordvpn.com", "uk2103.nordvpn.com"],
            port=1080,
            protocol="socks5",
            username="nordvpn-service-username",
            password="nordvpn-service-password",
        ))

        # Local VPN app running on your machine
        agent = Agent(proxy="socks5://localhost:1080")
    """

    def __init__(
        self,
        servers: list[str],
        port: int = 1080,
        protocol: str = "socks5",
        username: str = "",
        password: str = "",
        strategy: str = "round-robin",
    ):
        self.servers = servers
        self.port = port
        self.protocol = protocol
        self.username = username
        self.password = password

        urls = [self._build_url(s) for s in servers]
        self._rotator = ProxyRotator(urls, strategy=strategy)

    def _build_url(self, server: str) -> str:
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.protocol}://{auth}{server}:{self.port}"

    def next(self) -> dict | None:
        return self._rotator.next()

    @property
    def total_used(self) -> int:
        return self._rotator.total_used


def resolve_proxy(proxy_config) -> dict | None:
    """Convert any proxy configuration to a Playwright proxy dict.

    Accepts:
        - None → no proxy
        - "socks5://host:port" → single proxy
        - {"server": "..."} → Playwright dict
        - ["proxy1", "proxy2"] → creates rotator, returns next
        - ProtonVPN instance → returns next from rotation
        - ProxyRotator instance → returns next
    """
    if proxy_config is None:
        return None

    if isinstance(proxy_config, str):
        return {"server": proxy_config}

    if isinstance(proxy_config, dict):
        return proxy_config

    if isinstance(proxy_config, list):
        # Create a rotator and pick one proxy from the list
        rotator = ProxyRotator(proxy_config)
        return rotator.next()

    if hasattr(proxy_config, 'next'):
        # ProtonVPN or ProxyRotator instance
        return proxy_config.next()

    return None


def _mask_proxy(server: str) -> str:
    """Mask credentials in proxy URL for logging."""
    if "@" in server:
        parts = server.split("@")
        return f"***@{parts[-1]}"
    return server
