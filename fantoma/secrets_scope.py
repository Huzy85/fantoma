"""Secrets tied to the site they belong to.

The domain-pattern idea (scheme defaults, a single leading wildcard, refusing
patterns that would match lookalike hosts) follows browser-use's
match_url_with_domain_pattern (MIT licence, https://github.com/browser-use/browser-use).
This module is fresh code written for Fantoma.

Two input formats are accepted for ``sensitive_data``:

    flat    {"password": "value"}                          typed on any site
    scoped  {"https://*.example.com": {"password": "v"}}   typed only there

The model only ever sees ``<secret:name>`` placeholders. A scoped placeholder
is turned into its real value only if the page URL, read at the moment of
filling, matches the pattern it was registered under.
"""

import re
from urllib.parse import urlsplit

_PLACEHOLDER = re.compile(r"<secret:([^<>]+)>")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _idna(host: str) -> str:
    host = host.strip(".").lower()
    if not host:
        raise ValueError("empty host")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise ValueError(f"invalid host {host!r}: {e}") from None


def _parse_pattern(pattern: str):
    """Return (schemes, wildcard, host, port) or raise ValueError."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError(f"invalid domain pattern {pattern!r}")
    raw = pattern.strip().lower()
    if "://" in raw:
        scheme, rest = raw.split("://", 1)
    else:
        scheme, rest = "https", raw
    if scheme == "http*":
        schemes = ("http", "https")
    elif scheme in ("http", "https"):
        schemes = (scheme,)
    else:
        raise ValueError(f"invalid domain pattern {pattern!r}: scheme must be http, https or http*")
    rest = re.split(r"[/?#]", rest, maxsplit=1)[0]
    if "@" in rest:
        raise ValueError(f"invalid domain pattern {pattern!r}: no credentials allowed")
    if rest.count("*") > 1:
        raise ValueError(f"invalid domain pattern {pattern!r}: only one wildcard allowed")
    wildcard = False
    if "*" in rest:
        if not rest.startswith("*."):
            raise ValueError(
                f"invalid domain pattern {pattern!r}: a wildcard must be a whole "
                "leading label, like *.example.com")
        wildcard = True
        rest = rest[2:]
    try:
        parts = urlsplit(f"{schemes[0]}://{rest}")
        port = parts.port
        host = parts.hostname or ""
    except ValueError as e:
        raise ValueError(f"invalid domain pattern {pattern!r}: {e}") from None
    if not host:
        raise ValueError(f"invalid domain pattern {pattern!r}: no host")
    host = _idna(host)
    if wildcard and "." not in host:
        # *.com would match every .com site
        raise ValueError(f"invalid domain pattern {pattern!r}: wildcard needs a full domain")
    if wildcard and host.split(".")[-1].isdigit():
        raise ValueError(f"invalid domain pattern {pattern!r}: wildcard on an IP address")
    return schemes, wildcard, host, port


def validate_domain_pattern(pattern: str) -> None:
    """Raise ValueError if the pattern is not an acceptable one."""
    _parse_pattern(pattern)


def match_url_with_domain_pattern(url: str, pattern: str) -> bool:
    """True if `url` is on a site the pattern allows.

    Raises ValueError for an invalid pattern. A URL that cannot be parsed, or
    has no host (about:blank), simply does not match.
    """
    schemes, wildcard, host, port = _parse_pattern(pattern)
    try:
        parts = urlsplit((url or "").strip())
        scheme = parts.scheme.lower()
        url_host = parts.hostname
        url_port = parts.port
    except ValueError:
        return False
    if scheme not in schemes or not url_host:
        return False
    try:
        url_host = _idna(url_host)
    except ValueError:
        return False
    want_port = port if port is not None else _DEFAULT_PORTS[scheme]
    have_port = url_port if url_port is not None else _DEFAULT_PORTS[scheme]
    if want_port != have_port:
        return False
    if wildcard:
        return url_host == host or url_host.endswith("." + host)
    return url_host == host


class SecretStore:
    """Secrets and the sites they may be typed on."""

    def __init__(self, entries=()):
        # (pattern or None for "anywhere", name, value)
        self._entries = [e for e in entries if e[2] != ""]

    @classmethod
    def from_any(cls, data) -> "SecretStore":
        """Build from a flat dict, a scoped dict, a mix of both, or a store."""
        if isinstance(data, SecretStore):
            return data
        entries = []
        for key, val in (data or {}).items():
            if isinstance(val, dict):
                validate_domain_pattern(key)
                for name, secret in val.items():
                    entries.append((key, str(name), str(secret)))
            else:
                entries.append((None, str(key), str(val)))
        return cls(entries)

    def __bool__(self):
        return bool(self._entries)

    def _allowed(self, pattern, url) -> bool:
        return pattern is None or match_url_with_domain_pattern(url, pattern)

    def names_for(self, url: str) -> list:
        """Placeholder names usable on `url`. Others are never listed."""
        out = []
        for pattern, name, _ in self._entries:
            if self._allowed(pattern, url) and name not in out:
                out.append(name)
        return out

    def mask(self, text: str, url: str = None) -> str:
        """Replace every real value, scoped or not, with its placeholder.

        With `url` (text bound for the model), a value whose placeholder is
        not usable on that site is replaced by "[redacted]" so the name of a
        secret scoped to another site is not disclosed either.
        """
        if not text or not self._entries:
            return text
        usable = None if url is None else set(self.names_for(url))
        for _, name, value in sorted(self._entries, key=lambda e: -len(e[2])):
            label = f"<secret:{name}>" if usable is None or name in usable else "[redacted]"
            text = text.replace(value, label)
        return text

    def reveal(self, text: str, url: str):
        """Fill placeholders for `url`. Returns (text, refused_names)."""
        if not text or "<secret:" not in text:
            return text, []
        known = {name for _, name, _ in self._entries}
        refused = []

        def sub(m):
            name = m.group(1)
            if name not in known:
                return m.group(0)
            for pattern, n, value in self._entries:
                if n == name and self._allowed(pattern, url):
                    return value
            if name not in refused:
                refused.append(name)
            return m.group(0)

        return _PLACEHOLDER.sub(sub, text), refused


def refusal_message(names) -> str:
    return "; ".join(f"secret '{n}' is not allowed on this site" for n in names)
