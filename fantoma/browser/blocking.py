"""Optional blocking of fonts and media (off by default).

Loading a page without web fonts and audio/video saves bandwidth, and on a
slow link some time. It is off by default because the gain on a fast
connection is small (a Wikipedia article loaded in about 2.9 s with or
without it) and blocking has a cost: Camoufox issue #170 reports that
blocking images made Cloudflare challenge the session. So only the two
kinds of request below are ever blocked:

* resource type "font" or "media"
* URL path ending in .woff .woff2 .ttf .otf .mp4 .webm .mp3 .m4a .ogg

Images, stylesheets, scripts and xhr/fetch are never blocked, whatever
the URL looks like.

The approach (a route handler that aborts by resource type and URL suffix)
is borrowed from Crawlee for Python's BlockRequestsFunction (Apache-2.0,
https://github.com/apify/crawlee-python).

Chaining: Playwright runs the most recently registered route first. The
handler is installed after the domain policy, and any request it does not
block goes on with route.fallback(), so the domain policy still decides
those. Installed on the context, so popups and new tabs are covered.
Not installed when attached to the user's own Chrome.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

log = logging.getLogger("fantoma.blocking")

BLOCKED_TYPES = frozenset({"font", "media"})
BLOCKED_SUFFIXES = (".woff", ".woff2", ".ttf", ".otf", ".mp4", ".webm", ".mp3", ".m4a", ".ogg")
# Never blocked, even if the URL ends in a blocked suffix.
NEVER_BLOCK_TYPES = frozenset({"document", "image", "stylesheet", "script", "xhr", "fetch",
                               "websocket", "eventsource", "manifest"})


def env_enabled() -> bool:
    return os.environ.get("FANTOMA_BLOCK_RESOURCES", "").strip().lower() in ("1", "true", "yes", "on")


def should_block(resource_type: str, url: str) -> bool:
    """True only for fonts and media."""
    rt = (resource_type or "").lower()
    if rt in BLOCKED_TYPES:
        return True
    if rt in NEVER_BLOCK_TYPES:
        return False
    try:
        path = urlsplit(url).path.lower()
    except Exception:
        return False
    return path.endswith(BLOCKED_SUFFIXES)


class ResourceBlocker:
    def __init__(self):
        self.blocked = 0

    def _route(self, route):
        try:
            req = route.request
            if should_block(req.resource_type, req.url):
                self.blocked += 1
                route.abort("blockedbyclient")
                return
        except Exception as e:
            log.debug("resource blocker error: %s", e)
        try:
            route.fallback()
        except Exception:
            # Fail closed: continue_() would skip the domain-policy handler.
            try:
                route.abort()
            except Exception:
                pass

    def install(self, context):
        context.route("**/*", self._route)
