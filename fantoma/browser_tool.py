"""Fantoma — the browser tool. Drive a browser step by step.

Accessibility-first: interacts through the ARIA tree, same channel as screen readers.
No mouse movements, no pixel coordinates, no visual layer signals for anti-bot to detect.
"""
import logging
import time

from fantoma.browser.actions import click_element, type_into, scroll_page
from fantoma.browser.engine import BrowserEngine
from fantoma.browser.consent import dismiss_consent
from fantoma.browser.observer import inject_observer, collect_mutations, wait_for_dom_stable
from fantoma.browser.page_state import verify_action, detect_errors
from fantoma.dom.accessibility import AccessibilityExtractor
from fantoma.config import FantomaConfig

log = logging.getLogger("fantoma")


class Fantoma:
    """The Fantoma browser tool.

    Usage:
        browser = Fantoma()
        state = browser.start("https://example.com")
        # state["aria_tree"] — feed this to your LLM
        result = browser.click(3)
        # result["state"]["aria_tree"] — updated page
        browser.stop()
    """

    def __init__(
        self,
        llm_url: str = None,
        api_key: str = "",
        model: str = "auto",
        headless: bool = True,
        proxy: str = None,
        browser: str = "camoufox",
        captcha_api: str = None,
        captcha_key: str = None,
        email_imap: dict = None,
        verification_callback: callable = None,
        timeout: int = 300,
        trace: bool = False,
    ):
        self.config = FantomaConfig()
        self.config.browser.headless = headless
        self.config.browser.browser_engine = browser
        self.config.browser.timeout = timeout
        self.config.browser.trace = trace
        if captcha_api:
            self.config.captcha.api = captcha_api
        if captcha_key:
            self.config.captcha.key = captcha_key
        if email_imap:
            from fantoma.config import EmailConfig
            self.config.email = EmailConfig(
                host=email_imap.get("host", ""),
                port=email_imap.get("port", 993),
                user=email_imap.get("user", ""),
                password=email_imap.get("password", ""),
                security=email_imap.get("security", "ssl"),
            )

        self._proxy = proxy
        self._llm = None
        if llm_url:
            from fantoma.llm.client import LLMClient
            self._llm = LLMClient(base_url=llm_url, api_key=api_key, model=model)

        self._verification_callback = verification_callback
        self._engine = None
        self._dom = AccessibilityExtractor(
            max_elements=self.config.extraction.max_elements,
            max_headings=self.config.extraction.max_headings,
        )

    # ── Lifecycle ────────────────────────────────────────────

    def start(self, url: str = None) -> dict:
        """Start the browser, optionally navigate to url. Returns initial state."""
        self._engine = BrowserEngine(
            headless=self.config.browser.headless,
            proxy=self._proxy,
            trace=self.config.browser.trace,
            browser_engine=self.config.browser.browser_engine,
        )
        self._engine.start()
        if url:
            self._engine.navigate(url)
            time.sleep(2)
            dismiss_consent(self._engine.get_page())
        return self.get_state()

    def stop(self) -> None:
        """Close the browser and clean up."""
        if self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass
            self._engine = None

    def restart(self) -> dict:
        """Restart with a fresh fingerprint. Returns new state."""
        url = None
        if self._engine:
            try:
                url = self._engine.get_url()
            except Exception:
                pass
            self.stop()
        return self.start(url)

    # ── State ────────────────────────────────────────────────

    def get_state(self) -> dict:
        """Get current page state: URL, title, ARIA tree, errors, tab count."""
        page = self._engine.get_page()
        aria_tree = self._dom.extract(page)
        errors = detect_errors(page)
        ctx = getattr(self._engine, '_context', None)
        tab_count = len(ctx.pages) if ctx else 1
        return {
            "url": page.url,
            "title": page.title(),
            "aria_tree": aria_tree,
            "errors": errors,
            "tab_count": tab_count,
        }

    def screenshot(self) -> bytes:
        """Take a PNG screenshot of the current viewport."""
        return self._engine.screenshot()

    # ── Actions ──────────────────────────────────────────────

    def _action_result(self, success: bool, pre_url: str = None) -> dict:
        """Build a standard action result with fresh state."""
        state = self.get_state()
        return {
            "success": success,
            "changed": pre_url is not None and state["url"] != pre_url,
            "url_changed": pre_url is not None and state["url"] != pre_url,
            "errors": state["errors"],
            "state": state,
        }

    def click(self, element_id: int) -> dict:
        """Click an element by its ARIA tree index."""
        page = self._engine.get_page()
        pre_url = page.url
        element = self._dom.get_element_by_index(page, element_id)
        if not element:
            log.warning("Element [%d] not found", element_id)
            return self._action_result(False, pre_url)
        try:
            inject_observer(page)
            click_element(self._engine, element)
            wait_for_dom_stable(page)
        except Exception as e:
            log.warning("Click [%d] failed: %s", element_id, e)
            return self._action_result(False, pre_url)
        return self._action_result(True, pre_url)

    def type_text(self, element_id: int, text: str) -> dict:
        """Type text into an element by its ARIA tree index."""
        page = self._engine.get_page()
        pre_url = page.url
        element = self._dom.get_element_by_index(page, element_id)
        if not element:
            log.warning("Element [%d] not found", element_id)
            return self._action_result(False, pre_url)
        try:
            inject_observer(page)
            type_into(self._engine, element, text)
            wait_for_dom_stable(page)
        except Exception as e:
            log.warning("Type [%d] failed: %s", element_id, e)
            return self._action_result(False, pre_url)
        return self._action_result(True, pre_url)

    def select(self, element_id: int, value: str) -> dict:
        """Select an option from a dropdown by its ARIA tree index."""
        page = self._engine.get_page()
        pre_url = page.url
        element = self._dom.get_element_by_index(page, element_id)
        if not element:
            log.warning("Element [%d] not found", element_id)
            return self._action_result(False, pre_url)
        try:
            inject_observer(page)
            element.select_option(label=value)
            wait_for_dom_stable(page)
        except Exception as e:
            log.warning("Select [%d] failed: %s", element_id, e)
            return self._action_result(False, pre_url)
        return self._action_result(True, pre_url)

    def scroll(self, direction: str = "down") -> dict:
        """Scroll the page. Direction: 'up', 'down', 'left', 'right'."""
        pre_url = self._engine.get_page().url
        try:
            scroll_page(self._engine, direction)
        except Exception as e:
            log.warning("Scroll failed: %s", e)
            return self._action_result(False, pre_url)
        return self._action_result(True, pre_url)

    def press_key(self, key: str) -> dict:
        """Press a keyboard key (Enter, Tab, Escape, etc.)."""
        page = self._engine.get_page()
        pre_url = page.url
        try:
            page.keyboard.press(key)
        except Exception as e:
            log.warning("Press %s failed: %s", key, e)
            return self._action_result(False, pre_url)
        return self._action_result(True, pre_url)

    def navigate(self, url: str) -> dict:
        """Navigate to a URL."""
        pre_url = self._engine.get_page().url
        try:
            self._engine.navigate(url)
            time.sleep(2)
            dismiss_consent(self._engine.get_page())
            wait_for_dom_stable(self._engine.get_page())
        except Exception as e:
            log.warning("Navigate failed: %s", e)
            return self._action_result(False, pre_url)
        return self._action_result(True, pre_url)
