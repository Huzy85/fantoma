"""Helpers for attaching to a Chrome that is already running (CDP).

CDP is full control of the browser: every tab, every cookie, every login.
Only loopback hosts are accepted unless the caller opts in with
allow_remote_cdp=True.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_SCHEMES = ("http", "https", "ws", "wss")


def profile_dirs() -> list[Path]:
    """Directories where Chrome writes DevToolsActivePort, most likely first."""
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        return [base / "Google" / "Chrome", base / "Chromium"]
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            return []
        root = Path(local)
        return [root / "Google" / "Chrome" / "User Data", root / "Chromium" / "User Data"]
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return [cfg / "google-chrome", cfg / "chromium"]


def parse_active_port(text: str) -> str | None:
    """Turn the contents of a DevToolsActivePort file into a ws:// URL.

    Line 1 is the port, line 2 the websocket path. Returns None if the file
    is not in that shape.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2 or not lines[0].isdigit():
        return None
    port = int(lines[0])
    path = lines[1]
    if not 0 < port < 65536 or not path.startswith("/"):
        return None
    return f"ws://127.0.0.1:{port}{path}"


def discover_cdp_url(dirs=None) -> str:
    """Find a running Chrome: DevToolsActivePort first, then port 9222."""
    for d in (profile_dirs() if dirs is None else dirs):
        f = Path(d) / "DevToolsActivePort"
        try:
            url = parse_active_port(f.read_text())
        except OSError:
            continue
        if url:
            return url
    return DEFAULT_CDP_URL


def check_cdp_url(url: str, allow_remote: bool = False) -> str:
    """Validate a CDP endpoint. Returns the URL, or raises ValueError."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
    except ValueError:
        raise ValueError(f"cdp_url is not a valid URL: {url!r}") from None
    if parsed.scheme not in _SCHEMES or not host:
        raise ValueError(
            f"cdp_url must look like http://127.0.0.1:9222 (got {url!r})")
    if host not in LOOPBACK_HOSTS and not allow_remote:
        raise ValueError(
            f"Refusing to attach to {host}: CDP gives full control of the browser "
            "and everything signed in to it. Use a loopback address, or pass "
            "allow_remote_cdp=True if you trust the network path.")
    return url


def resolve_cdp_url(value: str, allow_remote: bool = False) -> str:
    """'auto' discovers a local Chrome; anything else is validated as given."""
    if value.strip().lower() == "auto":
        return check_cdp_url(discover_cdp_url(), allow_remote)
    return check_cdp_url(value.strip(), allow_remote)
