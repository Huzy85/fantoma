# Proxy & VPN Guide

Fantoma supports routing browser traffic through proxies and VPNs for IP rotation and anti-rate-limiting.

## Quick Start

### Option 1: Your VPN app is already running (simplest)

Most VPN apps (ExpressVPN, NordVPN, ProtonVPN, Mullvad) can expose a local SOCKS5 proxy. Check your app's settings for "SOCKS5 proxy" or "local proxy."

```python
from fantoma import Agent

# ProtonVPN app running → Settings → Advanced → SOCKS5 proxy on localhost:1080
agent = Agent(
    llm_url="http://localhost:8081/v1",
    proxy="socks5://localhost:1080",
)
```

Everything Fantoma does goes through your VPN. Your real IP is hidden.

### Option 2: Multiple servers with auto-rotation

If your VPN provider gives you SOCKS5 proxy credentials (ProtonVPN Plus, NordVPN, Mullvad), you can rotate through different servers:

```python
from fantoma import Agent
from fantoma.browser.proxy import VPNProxy

agent = Agent(
    llm_url="http://localhost:8081/v1",
    proxy=VPNProxy(
        servers=[
            "us-server.yourprovider.com",
            "uk-server.yourprovider.com",
            "nl-server.yourprovider.com",
            "de-server.yourprovider.com",
        ],
        port=1080,
        protocol="socks5",
        username="your-proxy-username",    # NOT your login — check provider dashboard
        password="your-proxy-password",
    ),
)

# Each run uses a different country
result1 = agent.run("Go to reddit.com...")  # Through US
result2 = agent.run("Go to reddit.com...")  # Through UK
result3 = agent.run("Go to reddit.com...")  # Through NL
```

### Option 3: OpenVPN config files + gluetun (advanced, best rotation)

For users with `.ovpn` config files from their VPN provider:

1. Install [gluetun](https://github.com/qdm12/gluetun) Docker container
2. Mount your `.ovpn` files
3. Point Fantoma at gluetun's HTTP proxy

```bash
# docker-compose.yml
services:
  gluetun:
    image: qmcgaw/gluetun
    cap_add: [NET_ADMIN]
    environment:
      - VPN_SERVICE_PROVIDER=custom
      - VPN_TYPE=openvpn
      - OPENVPN_CUSTOM_CONFIG=/gluetun/config.ovpn
      - HTTPPROXY=on
      - HTTPPROXY_LISTENING_ADDRESS=:8888
    volumes:
      - ./vpn-configs:/gluetun
    ports:
      - 8888:8888
```

```python
agent = Agent(
    llm_url="http://localhost:8081/v1",
    proxy="http://localhost:8888",
)
```

To rotate: restart gluetun with a different config file, or use gluetun's built-in server rotation.

### Option 4: Proxy list (residential proxies)

For scraping at scale, use a residential proxy service (Bright Data, Oxylabs, SmartProxy):

```python
agent = Agent(
    llm_url="http://localhost:8081/v1",
    proxy=[
        "http://user:pass@gate.brightdata.com:22225",
        "http://user:pass@gate.brightdata.com:22226",
        "http://user:pass@gate.brightdata.com:22227",
    ],
)
# Auto-rotates through the list
```

## Where to find your proxy credentials

| Provider | Where | Protocol | Port |
|----------|-------|----------|------|
| ProtonVPN | account.protonvpn.com → OpenVPN/IKEv2 username | SOCKS5 | 1080 |
| NordVPN | my.nordaccount.com → Services → NordVPN → Service credentials | SOCKS5 | 1080 |
| Mullvad | Account page → WireGuard/SOCKS5 | SOCKS5 | 1080 |
| ExpressVPN | No direct proxy — use Option 1 (app) or Option 3 (OpenVPN + gluetun) | — | — |
| Surfshark | my.surfshark.com → Manual setup → SOCKS5 | SOCKS5 | 1080 |

## When do you need a proxy?

- **Casual use**: Not needed. Camoufox passes fingerprint tests without a proxy.
- **Repeated access to same site**: After 2+ hours on the same IP, some sites (Reddit) start blocking. Proxy rotation fixes this.
- **Scraping at scale**: Residential proxies prevent IP-based detection.
- **Geo-restricted content**: Route through a specific country.

## What Fantoma does NOT do

- Does not manage VPN connections (use your VPN app or gluetun)
- Does not install OpenVPN or WireGuard
- Does not store your VPN credentials (passed at runtime only)
- Does not auto-discover proxy servers from your VPN provider
