"""Small address helpers shared by the domain policy and the LLM client.

One definition of "private" so the two never drift: loopback, RFC1918,
link-local, reserved and unspecified addresses, plus the carrier-grade NAT
range 100.64.0.0/10 that Tailscale and similar overlays use.
"""

from __future__ import annotations

import ipaddress

# Carrier-grade NAT range, used by Tailscale and similar overlays.
CGNAT = ipaddress.ip_network("100.64.0.0/10")

# Names that mean "this machine or this network" whatever DNS says.
PRIVATE_NAMES = ("localhost", "ip6-localhost", "ip6-loopback",
                 "metadata", "metadata.google.internal", "instance-data",
                 "host.docker.internal")
PRIVATE_SUFFIXES = (".localhost", ".internal", ".local", ".lan", ".home", ".home.arpa")


def is_private_address(addr) -> bool:
    """True when an ipaddress object is loopback, private, link-local,
    reserved, unspecified or in the CGNAT range. IPv4-mapped IPv6 is unwrapped."""
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.version == 4 and addr in CGNAT:
        return True
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_unspecified)


def is_private_name(host: str) -> bool:
    """True for host names that are local by convention (.lan, .home, .local,
    .internal, localhost, cloud metadata names)."""
    host = host.strip().rstrip(".").lower()
    return host in PRIVATE_NAMES or host.endswith(PRIVATE_SUFFIXES)
