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
                 trace=False, trace_dir=None):
        self.headless = headless
        self.profile_dir = profile_dir
        self.accessibility = accessibility
        self.proxy = proxy  # {"server": "http://host:port"} or "http://host:port" string
        self.humanizer = Humanizer() if humanize else None
        self._trace = trace
        self._trace_dir = trace_dir or self.DEFAULT_TRACE_DIR
        self._trace_active = False
        self._browser = None
        self._context = None
        self._page = None
        self._camoufox_cm = None
        self._persistent = False

    def _proxy_dict(self) -> dict | None:
        """Convert any proxy config to Playwright format. Supports rotation."""
        from fantoma.browser.proxy import resolve_proxy
        return resolve_proxy(self.proxy)

    def start(self):
        """Launch Camoufox browser. Uses persistent profile if profile_dir is set."""
        proxy = self._proxy_dict()

        if self.profile_dir:
            self._persistent = True
            self._camoufox_cm = Camoufox(
                persistent_context=True,
                user_data_dir=self.profile_dir,
                headless=self.headless,
                humanize=True,
                os=["linux"],
                proxy=proxy,
            )
            # persistent_context returns a BrowserContext directly
            self._context = self._camoufox_cm.__enter__()
            self._browser = None
            # Use existing page or create one
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
        else:
            self._persistent = False
            self._camoufox_cm = Camoufox(
                headless=self.headless,
                humanize=True,
                os=["linux"],
                proxy=proxy,
            )
            self._browser = self._camoufox_cm.__enter__()
            self._context = self._browser.new_context()
            self._page = self._context.new_page()

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
            import logging
            logging.getLogger("fantoma.browser").warning("Navigation failed: %s — trying recovery", e)
            try:
                if self._context:
                    self._page = self._context.new_page()
                    self._page.goto(url, wait_until=wait_until, timeout=timeout)
                else:
                    raise
            except Exception:
                raise
        if self.humanizer:
            self.humanizer.reading_pause()

    def click(self, selector_or_element, delay_after: bool = True):
        """Click an element with optional human delay after."""
        page = self._page

        if isinstance(selector_or_element, str):
            element = page.query_selector(selector_or_element)
        else:
            element = selector_or_element

        if not element:
            return False

        # Move mouse to element first for human-like behaviour
        if self.humanizer:
            if self.humanizer.should_move_mouse():
                self.humanizer.random_mouse_move(page)
            self.humanizer.move_to_element(page, element)

        element.click()

        if delay_after and self.humanizer:
            self.humanizer.action_pause()

        return True

    def type_text(self, selector_or_element, text: str, clear_first: bool = True):
        """Type text character-by-character with human-like delays.

        Waits for the element to have focus before typing, which prevents
        keystrokes being lost on React controlled inputs during re-renders.
        """
        page = self._page

        if isinstance(selector_or_element, str):
            element = page.query_selector(selector_or_element)
        else:
            element = selector_or_element

        if not element:
            return False

        element.click()

        # Wait for element to actually receive focus — React controlled inputs
        # re-render on focus which can briefly unmount/remount the DOM node
        try:
            page.wait_for_function(
                "(el) => document.activeElement === el",
                element,
                timeout=3000,
            )
        except Exception:
            # Fallback: element may have been replaced by React re-render,
            # re-query and click again
            element.click()
            time.sleep(0.3)

        if clear_first:
            page.keyboard.press("Control+a")
            page.keyboard.press("Backspace")

        for char in text:
            page.keyboard.type(char)
            if self.humanizer:
                time.sleep(self.humanizer.type_char_delay())

        if self.humanizer:
            self.humanizer.action_pause()

        return True

    def scroll(self, direction: str = "down", amount: int = 300):
        """Scroll with human-like speed variation."""
        if self.humanizer:
            amount = self.humanizer.scroll_distance()

        if direction == "up":
            amount = -amount

        self._page.mouse.wheel(0, amount)

        if self.humanizer:
            time.sleep(
                __import__("random").uniform(*self.humanizer.scroll_delay)
            )

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

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
