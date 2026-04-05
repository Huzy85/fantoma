"""Camoufox browser engine wrapper with anti-detection and human-like behaviour."""

import logging
import os
import time
from datetime import datetime

from camoufox.sync_api import Camoufox

from fantoma.browser.humanize import Humanizer

_log = logging.getLogger("fantoma.browser")


class BrowserEngine:
    """Manages a Camoufox browser session with anti-detection and human-like behaviour."""

    DEFAULT_TRACE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "fantoma", "traces")

    def __init__(self, headless=True, profile_dir=None, humanize=True, accessibility=True, proxy=None,
                 trace=False, trace_dir=None, browser_engine="camoufox"):
        self.headless = headless
        self.profile_dir = profile_dir
        self.accessibility = accessibility
        self.proxy = proxy  # {"server": "http://host:port"} or "http://host:port" string
        self._humanize = humanize
        self.humanizer = Humanizer() if humanize else None
        self._trace = trace
        self._trace_dir = trace_dir or self.DEFAULT_TRACE_DIR
        self._trace_active = False
        self._browser_engine = browser_engine
        self._browser = None
        self._context = None
        self._page = None
        self._camoufox_cm = None
        self._playwright = None
        self._persistent = False

    def _proxy_dict(self) -> dict | None:
        """Convert any proxy config to Playwright format. Supports rotation."""
        from fantoma.browser.proxy import resolve_proxy
        return resolve_proxy(self.proxy)

    def start(self):
        """Launch browser. Dispatches to Camoufox (default) or Chromium (Patchright)."""
        if self._browser_engine == "chromium":
            self._start_chromium()
        else:
            self._start_camoufox()

    def _start_camoufox(self):
        """Launch Camoufox browser. Uses persistent profile if profile_dir is set."""
        # glibc 2.42+ (Fedora 43 / Linux 6.7+): madvise(MADV_GUARD_INSTALL) is
        # blocked by Camoufox's seccomp filter, causing SIGSEGV on browser start.
        # An LD_PRELOAD shim fixes this by intercepting the seccomp install paths.
        # Place the compiled shim at ~/.local/share/fantoma/madvise_shim.so —
        # Fantoma will detect and load it automatically.
        # LIBGL_ALWAYS_SOFTWARE: Mesa software renderer for glxtest child process.
        # DISPLAY: required for glxtest on headless machines running Xvfb.
        import pathlib
        shim = pathlib.Path.home() / ".local/share/fantoma/madvise_shim.so"
        if shim.exists():
            existing = os.environ.get("LD_PRELOAD", "")
            os.environ["LD_PRELOAD"] = f"{shim}:{existing}".rstrip(":")
            os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
            os.environ.setdefault("DISPLAY", ":99")
        proxy = self._proxy_dict()

        # When using virtual display, sync Xvfb resolution with Camoufox's
        # spoofed screen size to prevent layout mismatches (dialogs off-screen)
        screen = None
        if self.headless == "virtual":
            from browserforge.fingerprints import Screen
            screen = Screen(min_width=1920, max_width=1920, min_height=1080, max_height=1080)

        # Native stealth patches (hardwareConcurrency, buildID, timer precision)
        from fantoma.browser.stealth import get_camoufox_config
        stealth_config = get_camoufox_config()

        if self.profile_dir:
            self._persistent = True
            cm_kwargs = dict(
                persistent_context=True,
                user_data_dir=self.profile_dir,
                headless=self.headless,
                humanize=True,
                os=["linux"],
                proxy=proxy,
                **stealth_config,
            )
            if screen:
                cm_kwargs["screen"] = screen
            self._camoufox_cm = Camoufox(**cm_kwargs)
            # persistent_context returns a BrowserContext directly
            self._context = self._camoufox_cm.__enter__()
            self._browser = None
            # Use existing page or create one
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
        else:
            self._persistent = False
            cm_kwargs = dict(
                headless=self.headless,
                humanize=True,
                os=["linux"],
                proxy=proxy,
                **stealth_config,
            )
            if screen:
                cm_kwargs["screen"] = screen
            self._camoufox_cm = Camoufox(**cm_kwargs)
            self._browser = self._camoufox_cm.__enter__()
            self._context = self._browser.new_context(ignore_https_errors=True)
            self._page = self._context.new_page()

        # Sync viewport with screen size for virtual display
        if screen and self._page:
            try:
                self._page.set_viewport_size({"width": 1920, "height": 1080})
            except Exception:
                pass

        # Apply stealth patches before any page JS runs
        if self._context:
            from fantoma.browser.stealth import apply_stealth
            apply_stealth(self._context)

        # Set accessibility preferences — present as assistive technology user
        if self.accessibility and self._page:
            try:
                self._page.emulate_media(reduced_motion="reduce")
                # Set screen reader flags via JavaScript
                self._page.evaluate("""() => {
                    // Signal that assistive technology is active
                    Object.defineProperty(navigator, 'userActivation', {
                        get: () => ({ hasBeenActive: true, isActive: true })
                    });
                    // Prefer reduced motion (accessibility setting)
                    if (window.matchMedia) {
                        const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
                        Object.defineProperty(mql, 'matches', { get: () => true });
                    }
                }""")
            except Exception:
                pass  # Non-critical — works without these

        # Start trace recording if enabled
        if self._trace and self._context:
            try:
                self._context.tracing.start(screenshots=True, snapshots=True)
                self._trace_active = True
                _log.info("Trace recording started")
            except Exception as e:
                _log.warning("Tracing not available (Camoufox may not support it): %s", e)
                self._trace_active = False

    def _start_chromium(self):
        """Start Patchright Chromium browser."""
        try:
            from patchright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "Patchright not installed. Install with: pip install fantoma[chromium]"
            )

        self._playwright = sync_playwright().start()
        proxy = self._proxy_dict()
        launch_args = {"headless": self.headless}
        if proxy:
            launch_args["proxy"] = proxy
        if self.profile_dir:
            self._context = self._playwright.chromium.launch_persistent_context(
                self.profile_dir, **launch_args
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        else:
            self._browser = self._playwright.chromium.launch(**launch_args)
            ctx_args = {"proxy": proxy} if proxy else {}
            ctx_args["ignore_https_errors"] = True
            self._context = self._browser.new_context(**ctx_args)
            self._page = self._context.new_page()

        if self._trace:
            try:
                self._context.tracing.start(screenshots=True, snapshots=True)
                self._trace_active = True
            except Exception as e:
                _log.warning("Tracing not available: %s", e)
                self._trace_active = False

        _log.info("Chromium (Patchright) started, headless=%s", self.headless)

    def stop(self):
        """Close browser gracefully."""
        # Save trace before closing browser
        if self._trace_active and self._context:
            try:
                os.makedirs(self._trace_dir, exist_ok=True)
                # Build filename from current domain + timestamp
                url = self._page.url if self._page else "unknown"
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(url).netloc or "unknown"
                except Exception:
                    domain = "unknown"
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                trace_path = os.path.join(self._trace_dir, f"{domain}-{ts}.zip")
                self._context.tracing.stop(path=trace_path)
                self._trace_active = False
                _log.info("Trace saved: %s", trace_path)
            except Exception as e:
                _log.warning("Failed to save trace: %s", e)

        if self._browser_engine == "chromium":
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._browser:
                try:
                    self._browser.close()
                except Exception:
                    pass
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
        else:
            # Close context and pages before exiting Camoufox
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._camoufox_cm is not None:
                try:
                    self._camoufox_cm.__exit__(None, None, None)
                except Exception:
                    pass
            self._page = None
            self._context = None
            self._browser = None
            self._camoufox_cm = None

    def navigate(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000):
        """Navigate to URL with human-like delay after."""
        try:
            self._page.goto(url, wait_until=wait_until, timeout=timeout)
        except Exception as e:
            # If page crashed, try to recover with a new page
            _log.warning("Navigation failed: %s — trying recovery", e)
            if not self._context:
                raise
            old_page = self._page
            self._page = self._context.new_page()
            try:
                self._page.goto(url, wait_until=wait_until, timeout=timeout)
            except Exception:
                # Recovery also failed — close the new blank page, restore old
                try:
                    self._page.close()
                except Exception:
                    pass
                self._page = old_page
                raise
        if self.humanizer:
            self.humanizer.reading_pause()

    def screenshot(self) -> bytes:
        """Take a screenshot and return bytes."""
        return self._page.screenshot()

    def new_tab(self, url: str = None) -> int:
        """Open a new tab in the same browser session. Returns the tab index.

        The new tab shares cookies, sessions, and fingerprint with existing tabs.
        Use switch_tab() to move between them.
        """
        ctx = self._context if self._context else self._page.context
        new_page = ctx.new_page()
        if url:
            new_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if self.humanizer:
                self.humanizer.reading_pause()
        self._page = new_page
        return len(ctx.pages) - 1

    def switch_tab(self, index: int):
        """Switch to a tab by index. Tab 0 is the first tab opened."""
        ctx = self._context if self._context else self._page.context
        pages = ctx.pages
        if 0 <= index < len(pages):
            self._page = pages[index]
            self._page.bring_to_front()

    def close_tab(self, index: int = None):
        """Close a tab by index. Defaults to current tab. Switches to previous tab."""
        ctx = self._context if self._context else self._page.context
        pages = ctx.pages
        if index is None:
            target = self._page
        elif 0 <= index < len(pages):
            target = pages[index]
        else:
            return
        target.close()
        # Switch to last remaining tab
        remaining = ctx.pages
        if remaining:
            self._page = remaining[-1]

    def tab_count(self) -> int:
        """Return the number of open tabs."""
        ctx = self._context if self._context else self._page.context
        return len(ctx.pages) if ctx else 1

    def get_page(self):
        """Return the Playwright page object (for DOM extractor)."""
        return self._page

    def get_url(self) -> str:
        """Current page URL."""
        return self._page.url if self._page else ""

    def get_cookies(self) -> list:
        """Get all cookies from the browser context."""
        ctx = self._context if self._context else (
            self._page.context if self._page else None
        )
        if ctx:
            return ctx.cookies()
        return []

    def inject_cookies(self, cookies: list):
        """Inject cookies into the browser context."""
        ctx = self._context if self._context else (
            self._page.context if self._page else None
        )
        if ctx:
            ctx.add_cookies(cookies)

    def get_storage_state(self) -> dict:
        """Get full browser state — cookies + localStorage + sessionStorage.
        Returns Playwright's storageState format:
        {"cookies": [...], "origins": [{"origin": "https://...", "localStorage": [...]}]}
        """
        ctx = self._context if self._context else (
            self._page.context if self._page else None
        )
        if not ctx:
            return {"cookies": [], "origins": []}

        cookies = ctx.cookies()

        origins = []
        if self._page:
            try:
                storage = self._page.evaluate("""() => {
                    const items = [];
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        items.push({name: key, value: localStorage.getItem(key)});
                    }
                    return {origin: window.location.origin, localStorage: items};
                }""")
                if storage and storage["localStorage"]:
                    origins.append(storage)
            except Exception:
                pass

        return {"cookies": cookies, "origins": origins}

    def load_storage_state(self, state: dict):
        """Load full browser state — cookies + localStorage.
        Accepts Playwright's storageState format. Cookies injected via context API.
        localStorage restored via page.evaluate, scoped per origin.
        """
        ctx = self._context if self._context else (
            self._page.context if self._page else None
        )
        if not ctx:
            return

        cookies = state.get("cookies", [])
        if cookies:
            ctx.add_cookies(cookies)

        origins = state.get("origins", [])
        if origins and self._page:
            for origin_data in origins:
                items = origin_data.get("localStorage", [])
                if not items:
                    continue
                try:
                    current_origin = self._page.evaluate("window.location.origin")
                    if current_origin == origin_data.get("origin"):
                        for item in items:
                            self._page.evaluate(
                                "(args) => localStorage.setItem(args[0], args[1])",
                                [item["name"], item["value"]]
                            )
                except Exception:
                    pass

    def clear_cookies(self):
        """Clear all cookies from the browser context."""
        if self._context:
            self._context.clear_cookies()

    def restart_with_new_fingerprint(self):
        """Stop the browser and start a fresh session with a new fingerprint."""
        self.stop()
        self.start()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
