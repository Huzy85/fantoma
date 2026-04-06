"""Fantoma — the browser tool. Drive a browser step by step.

Accessibility-first: interacts through the ARIA tree, same channel as screen readers.
No mouse movements, no pixel coordinates, no visual layer signals for anti-bot to detect.
"""
import logging
import time
from typing import Any

from fantoma.browser.actions import click_element, type_into, scroll_page
from fantoma.browser.engine import BrowserEngine
from fantoma.browser.consent import dismiss_consent
from fantoma.browser.form_login import login as form_login, _looks_logged_in
from fantoma.browser.form_memory import FormMemory
from fantoma.browser.observer import inject_observer, collect_mutations, wait_for_dom_stable
from fantoma.browser.page_state import verify_action, detect_errors
from fantoma.dom.accessibility import AccessibilityExtractor
from fantoma.config import FantomaConfig
from fantoma.session import SessionManager

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
        profile_dir: str = None,
    ):
        self.config = FantomaConfig()
        self._profile_dir = profile_dir
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
        self._task = ""
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
            profile_dir=self._profile_dir,
        )
        self._engine.start()
        if url:
            self._engine.navigate(url)
            time.sleep(2)
            dismiss_consent(self._engine.get_page())
        return self.get_state(task=self._task)

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

    def get_state(self, mode: str = "navigate", task: str = "") -> dict:
        """Get current page state: URL, title, ARIA tree, errors, tab count."""
        page = self._engine.get_page()
        aria_tree = self._dom.extract(page, task=task, mode=mode)
        errors = detect_errors(page)
        ctx = getattr(self._engine, '_context', None)
        tab_count = len(ctx.pages) if ctx else 1
        try:
            title = page.title()
        except Exception:
            time.sleep(1)
            try:
                title = page.title()
            except Exception:
                title = page.url
        return {
            "url": page.url,
            "title": title,
            "aria_tree": aria_tree,
            "errors": errors,
            "tab_count": tab_count,
        }

    def evaluate(self, script: str) -> Any:
        """Execute JavaScript on the current page and return the result."""
        page = self._engine.get_page()
        return page.evaluate(script)

    def fill_by_selector(self, selector: str, value: str) -> dict:
        """Fill an input element by CSS selector (bypasses ARIA tree limit)."""
        page = self._engine.get_page()
        pre_url = page.url
        try:
            from .browser.observer import inject_observer, wait_for_dom_stable
            inject_observer(page)
            element = page.query_selector(selector)
            if not element:
                return {"success": False, "error": f"Selector not found: {selector}"}
            element.fill(value)
            wait_for_dom_stable(page)
            return self._action_result(True, pre_url)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def screenshot(self) -> bytes:
        """Take a PNG screenshot of the current viewport."""
        return self._engine.screenshot()

    # ── Actions ──────────────────────────────────────────────

    def _action_result(self, success: bool, pre_url: str = None) -> dict:
        """Build a standard action result with fresh state."""
        state = self.get_state(task=self._task)
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

    # ── Tabs ─────────────────────────────────────────────────

    def new_tab(self, url: str) -> dict:
        """Open a new tab and navigate to url."""
        self._engine.new_tab(url)
        time.sleep(2)
        return {"state": self.get_state()}

    def switch_tab(self, tab: int | str) -> dict:
        """Switch to a tab by index."""
        self._engine.switch_tab(tab)
        return {"state": self.get_state()}

    def close_tab(self, tab: int | str = None) -> dict:
        """Close a tab. Defaults to current."""
        if tab is not None:
            self._engine.close_tab(tab)
        else:
            self._engine.close_tab()
        return {"state": self.get_state()}

    def list_tabs(self) -> list[dict]:
        """List open tabs with index and URL."""
        ctx = getattr(self._engine, '_context', None)
        if not ctx:
            page = self._engine.get_page()
            return [{"index": 0, "url": page.url}]
        return [{"index": i, "url": p.url} for i, p in enumerate(ctx.pages)]

    # ── High-level operations ────────────────────────────────

    def login(self, url: str, email: str = "", username: str = "",
              password: str = "", first_name: str = "", last_name: str = "") -> dict:
        """Log into a site. Navigates to url, fills form, handles CAPTCHA and verification.

        Browser must be started first via start().
        Does NOT stop the browser after -- caller may want to continue browsing.
        """
        from fantoma.captcha.orchestrator import CaptchaOrchestrator
        from urllib.parse import urlparse
        from uuid import uuid4

        account = email or username or "default"
        domain = urlparse(url).netloc
        log.info("Login: %s (account=%s)", url, account[:20])

        sessions = SessionManager()
        memory = FormMemory()
        visit_id = uuid4().hex

        # Try saved session first
        saved = sessions.load(domain, account)
        if saved:
            log.info("Found saved session for %s -- validating", domain)
            try:
                self._engine.load_storage_state(saved["storage_state"])
                self._engine.navigate(saved.get("login_url", url))
                time.sleep(3)
                page = self._engine.get_page()
                if _looks_logged_in(page, page.url, url):
                    log.info("Saved session valid")
                    memory.close()
                    return {"success": True, "url": page.url, "from_session": True,
                            "fields_filled": [], "steps": 0}
                log.info("Saved session expired")
                sessions.delete(domain, account)
            except Exception as e:
                log.warning("Session validation failed: %s", e)
                sessions.delete(domain, account)

        # Full login flow
        self._engine.navigate(url)
        time.sleep(3)
        page = self._engine.get_page()

        # Handle CAPTCHA
        captcha = CaptchaOrchestrator(self.config)
        captcha.handle(page, self._engine.screenshot)

        result = form_login(
            browser=self._engine,
            dom_extractor=self._dom,
            email=email, username=username, password=password,
            first_name=first_name, last_name=last_name,
            memory=memory, visit_id=visit_id,
            config=self.config, llm=self._llm,
        )

        page = self._engine.get_page()

        # Check if logged in
        if result.get("success") and _looks_logged_in(page, page.url, url):
            log.info("Login successful")
            try:
                state = self._engine.get_storage_state()
                sessions.save(domain, account, state, url)
            except Exception as e:
                log.warning("Failed to save session: %s", e)
            memory.record_visit(domain, True)
            memory.close()
            return result

        # Handle verification if needed
        if result.get("verification_needed"):
            vtype = result["verification_needed"]
            log.info("Verification needed: %s", vtype)
            value = self._get_verification(vtype, domain)
            if value and vtype == "code":
                self._enter_verification_code(value)
            elif value and vtype == "link":
                self._engine.navigate(value)
                time.sleep(5)

            page = self._engine.get_page()
            if _looks_logged_in(page, page.url, url):
                log.info("Logged in after verification")
                try:
                    state = self._engine.get_storage_state()
                    sessions.save(domain, account, state, url)
                except Exception:
                    pass
                memory.record_visit(domain, True)
                memory.close()
                result["success"] = True
                return result

        memory.record_visit(domain, False)
        memory.close()
        return result

    def extract(self, query: str, schema: dict = None) -> dict | list | str:
        """Extract data from the current page.

        With LLM: sends page text + query to LLM, returns structured data.
        Without LLM: returns raw ARIA tree text.
        """
        if not self._llm:
            return self._dom.extract(self._engine.get_page())

        import json as _json
        page = self._engine.get_page()
        main = page.locator("main, [role=main]")
        if main.count() > 0:
            full_text = main.first.inner_text()[:6000]
        else:
            full_text = page.inner_text("body")[:6000]

        if schema:
            type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
            schema_desc = ", ".join(f'"{k}": {type_map.get(v, "string")}' for k, v in schema.items())
            system = (f"Extract data as a JSON array. Each item must have these fields: {{{schema_desc}}}.\n"
                      "Return ONLY a valid JSON array. No explanation. No markdown.")
        else:
            system = "Extract the requested information. Return only the data, no explanation."

        response = self._llm.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": f"Extract: {query}\n\nPage content:\n{full_text}"}],
            max_tokens=2000,
        )
        if not response:
            return [] if schema else ""
        response = response.strip()
        if schema:
            if response.startswith("```"):
                response = response.split("\n", 1)[1].rsplit("```", 1)[0]
            try:
                data = _json.loads(response)
                return data if isinstance(data, list) else [data]
            except _json.JSONDecodeError:
                return []
        return response

    # ── Utilities ────────────────────────────────────────────

    def get_cookies(self) -> list[dict]:
        return self._engine.get_cookies()

    def set_cookies(self, cookies: list[dict]) -> None:
        self._engine.set_cookies(cookies)

    def get_storage_state(self) -> dict:
        return self._engine.get_storage_state()

    def load_storage_state(self, state: dict) -> None:
        self._engine.load_storage_state(state)

    # ── Private helpers ──────────────────────────────────────

    def _get_verification(self, vtype, domain):
        """Get verification code/link. IMAP -> callback -> terminal."""
        if self.config.email.host:
            from fantoma.browser.email_verify import check_inbox
            result = check_inbox(self.config.email, domain, prefer=vtype)
            if result:
                return result["value"]
        if self._verification_callback:
            try:
                msg = f"Enter verification {vtype} from {domain}"
                value = self._verification_callback(domain, msg)
                if value:
                    return value.strip()
            except Exception as e:
                log.warning("Verification callback failed: %s", e)
        try:
            value = input(f"\nVerification {vtype} from {domain}: ")
            return value.strip() if value else None
        except (EOFError, OSError):
            return None

    def _enter_verification_code(self, code):
        """Find verification input and enter the code."""
        page = self._engine.get_page()
        self._dom.extract(page)
        for el in self._dom._last_interactive:
            if el.get("role") in ("textbox", "input"):
                handle = self._dom.get_element_by_index(page, el["index"])
                if handle:
                    type_into(self._engine, handle, code)
                    page.keyboard.press("Enter")
                    return
        selectors = ['input[name*="code"]', 'input[name*="otp"]', 'input[name*="verify"]',
                     'input[placeholder*="code"]', 'input[type="text"]', 'input[type="number"]']
        for sel in selectors:
            try:
                handle = page.query_selector(sel)
                if handle and handle.is_visible():
                    type_into(self._engine, handle, code)
                    page.keyboard.press("Enter")
                    return
            except Exception:
                continue
