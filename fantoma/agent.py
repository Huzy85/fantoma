"""Fantoma Agent — the main public API."""
import logging
import signal
import time
from dataclasses import dataclass
from typing import Any

from fantoma.config import FantomaConfig
from fantoma.executor import Executor
from fantoma.browser.engine import BrowserEngine
from fantoma.llm.client import LLMClient
from fantoma.resilience.escalation import EscalationChain

log = logging.getLogger("fantoma")


@dataclass
class AgentResult:
    """Result of an agent.run() call."""
    success: bool
    data: Any = None           # Extracted data (if task asked to extract something)
    steps_taken: int = 0
    steps_detail: list = None  # List of step descriptions + outcomes
    error: str = ""
    tokens_used: int = 0
    escalations: int = 0


class Agent:
    """The Fantoma browser agent.

    Usage:
        agent = Agent(llm_url="http://localhost:8080/v1")
        result = agent.run("Go to hacker news and find the top post about AI")
    """

    def __init__(
        self,
        llm_url: str = "http://localhost:8080/v1",
        api_key: str = "",
        model: str = "auto",
        headless: bool = True,
        profile_dir: str = None,
        proxy: str | dict = None,
        escalation: list[str] = None,
        escalation_keys: list[str] = None,
        captcha_api: str = None,
        captcha_key: str = None,
        captcha_webhook: str = None,  # Any webhook URL (Slack, Discord, custom)
        captcha_telegram: dict = None,  # {"token": "bot-token", "chat_id": "123"}
        max_steps: int = 50,
        timeout: int = 300,
        verbose: bool = False,
        trace: bool = False,
        browser: str = "camoufox",
        email_imap: dict = None,
        verification_callback: callable = None,
        sensitive_data: dict = None,
    ):
        # Build config
        self.config = FantomaConfig()
        self.config.llm.base_url = llm_url
        self.config.llm.api_key = api_key
        self.config.llm.model = model
        self.config.browser.headless = headless
        self.config.browser.profile_dir = profile_dir
        self.config.browser.browser_engine = browser
        self._proxy = proxy
        self.config.resilience.max_steps = max_steps
        self.config.browser.timeout = timeout
        self.config.browser.trace = trace

        if captcha_api:
            self.config.captcha.api = captcha_api
        if captcha_key:
            self.config.captcha.key = captcha_key
        if captcha_webhook:
            self.config.captcha.webhook = captcha_webhook
        # captcha_telegram: reserved for future Telegram-based CAPTCHA solver
        # (accepted in __init__ but not yet wired to CaptchaOrchestrator)

        # Email verification config
        if email_imap:
            from fantoma.config import EmailConfig
            self.config.email = EmailConfig(
                host=email_imap.get("host", ""),
                port=email_imap.get("port", 993),
                user=email_imap.get("user", ""),
                password=email_imap.get("password", ""),
                security=email_imap.get("security", "ssl"),
            )
        self._verification_callback = verification_callback
        self._sensitive_data = sensitive_data or {}

        # Set up escalation chain with per-endpoint API keys
        endpoints = escalation or [llm_url]
        if escalation_keys:
            esc_keys = escalation_keys
        else:
            esc_keys = [api_key] + [""] * (len(endpoints) - 1)
        self.escalation = EscalationChain(endpoints, esc_keys)

        # Set up logging
        if verbose:
            logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")

        # LLM client (will be recreated on escalation)
        self._llm = LLMClient(base_url=llm_url, api_key=api_key, model=model)

    def run(self, task: str, start_url: str = None) -> AgentResult:
        """Run a browser task described in natural language.

        Args:
            task: What to do, e.g. "Find the cheapest flight from London to Barcelona"
            start_url: Optional starting URL.

        Returns:
            AgentResult with success status, extracted data, and step details.
        """
        log.info("Task: %s", task)

        try:
            browser = BrowserEngine(
                headless=self.config.browser.headless,
                profile_dir=self.config.browser.profile_dir,
                proxy=self._proxy,
                trace=self.config.browser.trace,
                browser_engine=self.config.browser.browser_engine,
            )
            browser.start()
        except Exception as e:
            # Retry once for transient failures (resource contention, etc.)
            log.warning("Browser start failed (%s), retrying...", e)
            try:
                browser = BrowserEngine(
                    headless=self.config.browser.headless,
                    profile_dir=self.config.browser.profile_dir,
                    proxy=self._proxy,
                    trace=self.config.browser.trace,
                    browser_engine=self.config.browser.browser_engine,
                )
                browser.start()
            except Exception as e2:
                log.error("Browser start failed on retry: %s", e2)
                return AgentResult(success=False, error=f"Browser start failed: {e2}")

        try:
            executor = Executor(
                browser=browser,
                llm=self._llm,
                config=self.config,
                escalation=self.escalation,
                sensitive_data=self._sensitive_data,
            )

            # Navigate to start URL if provided
            if start_url:
                browser.navigate(start_url)

            # Reactive mode with timeout protection

            def _timeout_handler(signum, frame):
                raise TimeoutError(f"Agent run exceeded {self.config.browser.timeout}s timeout")

            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(self.config.browser.timeout)
            try:
                result = executor.execute_reactive(task)
                return result
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        except TimeoutError as e:
            log.error("Agent timed out: %s", e)
            return AgentResult(success=False, error=str(e))
        except Exception as e:
            log.error("Agent run failed: %s", e)
            return AgentResult(success=False, error=str(e))
        finally:
            try:
                browser.stop()
            except Exception:
                pass

    def login(self, url: str, email: str = "", username: str = "", password: str = "",
              first_name: str = "", last_name: str = "") -> AgentResult:
        """Log into a site. Tries saved session first, falls back to full login.

        Full pipeline: saved cookies → form fill → CAPTCHA → submit →
        email verification → post-verify login-back → save session.

        Args:
            url: Login/signup page URL
            email: Email address
            username: Username
            password: Password
            first_name: First name (signup forms)
            last_name: Last name (signup forms)

        Returns:
            AgentResult with success status and login details.
        """
        from fantoma.browser.form_login import login as form_login, _looks_logged_in
        from fantoma.browser.form_memory import FormMemory
        from fantoma.dom.accessibility import AccessibilityExtractor
        from fantoma.session import SessionManager
        from urllib.parse import urlparse
        from uuid import uuid4

        account = email or username or "default"
        domain = urlparse(url).netloc
        log.info("Login: %s (account=%s)", url, account[:20])

        sessions = SessionManager()
        memory = FormMemory()
        visit_id = uuid4().hex

        # ── Step 1: Try saved session ──────────────────────────
        saved = sessions.load(domain, account)
        if saved:
            log.info("Found saved session for %s — validating", domain)
            try:
                browser = self._make_browser()
                browser.start()
                browser.load_storage_state(saved["storage_state"])
                browser.navigate(saved.get("login_url", url))
                time.sleep(3)
                page = browser.get_page()
                if _looks_logged_in(page, page.url, url):
                    log.info("Saved session valid — already logged in")
                    browser.stop()
                    memory.close()
                    return AgentResult(success=True, data={"url": page.url, "from_session": True}, steps_taken=0)
                log.info("Saved session expired — proceeding with full login")
                sessions.delete(domain, account)
                browser.stop()
            except Exception as e:
                log.warning("Session validation failed: %s", e)
                sessions.delete(domain, account)
                try:
                    browser.stop()
                except Exception:
                    pass

        # ── Steps 2-4: Full login flow ─────────────────────────
        try:
            browser = self._make_browser()
            browser.start()
        except Exception as e:
            log.error("Browser start failed: %s", e)
            memory.close()
            return AgentResult(success=False, error=f"Browser start failed: {e}")

        try:
            browser.navigate(url)
            time.sleep(3)

            dom = AccessibilityExtractor(
                max_elements=self.config.extraction.max_elements,
                max_headings=self.config.extraction.max_headings,
            )

            result = form_login(
                browser=browser,
                dom_extractor=dom,
                email=email,
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
                memory=memory,
                visit_id=visit_id,
                config=self.config,
                llm=self._llm,
            )

            page = browser.get_page()

            # ── Step 4: Check if already logged in ─────────────
            if result.get("success") and _looks_logged_in(page, page.url, url):
                log.info("Login successful after form fill")
                self._save_session(sessions, browser, domain, account, url)
                memory.record_visit(domain, True)
                memory.close()
                browser.stop()
                return AgentResult(success=True, data=result, steps_taken=result.get("steps", 0))

            # ── Step 5: Email verification ─────────────────────
            if result.get("verification_needed"):
                vtype = result["verification_needed"]
                log.info("Verification needed: %s", vtype)
                value = self._get_verification(vtype, domain)

                if value and vtype == "code":
                    self._enter_verification_code(browser, dom, value)
                elif value and vtype == "link":
                    browser.navigate(value)
                    time.sleep(5)

                # Step 5b: Check verification accepted
                if value:
                    time.sleep(3)
                    page = browser.get_page()
                    try:
                        post_body = page.inner_text("body")[:2000].lower()
                    except Exception:
                        post_body = ""

                    error_signals = ["invalid code", "incorrect code", "expired",
                                     "try again", "wrong code", "invalid link"]
                    if any(s in post_body for s in error_signals):
                        log.warning("Verification rejected — error detected on page")
                        memory.record_visit(domain, False)
                        memory.close()
                        browser.stop()
                        return AgentResult(success=False, data=result,
                                           steps_taken=result.get("steps", 0),
                                           error="Verification code/link rejected")

            # ── Step 6: Post-verification check ────────────────
            page = browser.get_page()
            if _looks_logged_in(page, page.url, url):
                log.info("Logged in after verification")
                self._save_session(sessions, browser, domain, account, url)
                memory.record_visit(domain, True)
                memory.close()
                browser.stop()
                return AgentResult(success=True, data=result, steps_taken=result.get("steps", 0))

            # Not logged in — try login-back with same credentials
            log.info("Not logged in after verification — attempting login-back")
            browser.navigate(url)
            time.sleep(3)

            login_result = form_login(
                browser=browser,
                dom_extractor=dom,
                email=email,
                username=username,
                password=password,
                memory=memory,
                visit_id=visit_id + "_loginback",
                config=self.config,
                llm=self._llm,
            )

            # ── Step 7: Final check ────────────────────────────
            page = browser.get_page()
            if _looks_logged_in(page, page.url, url):
                log.info("Login-back successful")
                self._save_session(sessions, browser, domain, account, url)
                memory.record_visit(domain, True)
                memory.close()
                browser.stop()
                return AgentResult(success=True, data=login_result, steps_taken=login_result.get("steps", 0))

            log.warning("Login failed — not logged in after all attempts")
            memory.record_visit(domain, False)
            memory.close()
            browser.stop()
            error_msg = result.get("errors", [""])[0] if result.get("errors") else "Login failed"
            return AgentResult(success=False, data=login_result,
                               steps_taken=login_result.get("steps", 0),
                               error=error_msg)

        except Exception as e:
            log.error("Login failed: %s", e)
            return AgentResult(success=False, error=str(e))
        finally:
            try:
                browser.stop()
            except Exception:
                pass
            memory.close()

    def _make_browser(self) -> BrowserEngine:
        """Create a BrowserEngine with current config."""
        return BrowserEngine(
            headless=self.config.browser.headless,
            profile_dir=self.config.browser.profile_dir,
            proxy=self._proxy,
            trace=self.config.browser.trace,
            browser_engine=self.config.browser.browser_engine,
        )

    def _save_session(self, sessions, browser, domain, account, login_url):
        """Save browser state to session manager."""
        try:
            state = browser.get_storage_state()
            sessions.save(domain, account, state, login_url)
        except Exception as e:
            log.warning("Failed to save session: %s", e)

    def _enter_verification_code(self, browser, dom, code):
        """Find the verification code input on the page and type the code."""
        from fantoma.browser.actions import type_into
        page = browser.get_page()

        # Strategy 1: ARIA tree textbox
        dom.extract(page)
        for el in dom._last_interactive:
            if el.get("role") in ("textbox", "input"):
                handle = dom.get_element_by_index(page, el["index"])
                if handle:
                    type_into(browser, handle, code)
                    page.keyboard.press("Enter")
                    log.info("Entered verification code via ARIA textbox")
                    return

        # Strategy 2: raw DOM selectors for code/OTP inputs
        selectors = [
            'input[name*="code"]', 'input[name*="otp"]', 'input[name*="token"]',
            'input[name*="verify"]', 'input[placeholder*="code"]',
            'input[placeholder*="Code"]', 'input[autocomplete*="one-time"]',
            'input[type="text"]', 'input[type="number"]', 'input[type="tel"]',
        ]
        for sel in selectors:
            try:
                handle = page.query_selector(sel)
                if handle and handle.is_visible():
                    type_into(browser, handle, code)
                    page.keyboard.press("Enter")
                    log.info("Entered verification code via selector: %s", sel)
                    return
            except Exception:
                continue

        # Strategy 3: any visible empty input
        try:
            inputs = page.query_selector_all('input[type="text"], input[type="number"], input[type="tel"], input:not([type])')
            for inp in inputs:
                if inp.is_visible() and inp.get_attribute("value") in ("", None):
                    type_into(browser, inp, code)
                    page.keyboard.press("Enter")
                    log.info("Entered verification code via fallback empty input")
                    return
        except Exception:
            pass

        log.warning("Could not find verification code input on page")

    def _get_verification(self, vtype, domain):
        """Get verification code/link. Priority: IMAP → callback → terminal → None."""
        # Tier 1: IMAP
        if self.config.email.host:
            from fantoma.browser.email_verify import check_inbox
            result = check_inbox(self.config.email, domain, prefer=vtype)
            if result:
                log.info("IMAP verification: %s=%s", result["type"], result["value"][:30])
                return result["value"]

        # Tier 2: Callback
        if self._verification_callback:
            try:
                msg = f"Enter verification code from {domain}" if vtype == "code" else f"Enter verification link from {domain}"
                value = self._verification_callback(domain, msg)
                if value:
                    return value.strip()
            except Exception as e:
                log.warning("Verification callback failed: %s", e)

        # Tier 3: Terminal prompt (only in interactive mode)
        try:
            if vtype == "code":
                value = input(f"\nVerification code from {domain}: ")
            else:
                value = input(f"\nVerification link from {domain}: ")
            return value.strip() if value else None
        except (EOFError, OSError):
            log.info("No interactive terminal — verification cannot be completed")
            return None

    def extract(self, url: str, query: str, schema: dict = None) -> list[dict] | str:
        """Navigate to a URL and extract structured data.

        Args:
            url: Page to extract from.
            query: What to extract, e.g. "all products with name and price"
            schema: Optional JSON schema for validation, e.g.
                    {"name": str, "price": float, "in_stock": bool}
                    Returns a list of dicts matching this schema.
                    If None, returns raw extracted text.

        Returns:
            List of dicts (if schema provided) or string (if no schema).

        Usage:
            # Raw extraction
            data = agent.extract("https://example.com", "Get all headings")

            # Structured extraction
            products = agent.extract(
                "https://books.toscrape.com",
                "Extract all books",
                schema={"title": str, "price": str}
            )
        """
        import json as _json

        log.info("Extract: %s → %s", url, query[:50])

        try:
            browser = BrowserEngine(
                headless=self.config.browser.headless,
                proxy=self._proxy,
                browser_engine=self.config.browser.browser_engine,
            )
            browser.start()
        except Exception as e:
            log.error("Browser start failed: %s", e)
            return [] if schema else ""

        try:
            browser.navigate(url)

            # Get main content (skips nav/sidebar noise), fall back to body
            page = browser.get_page()
            time.sleep(3)  # Let JS render
            main = page.locator("main, [role=main]")
            if main.count() > 0:
                full_text = main.first.inner_text()[:6000]
            else:
                full_text = page.inner_text("body")[:6000]

            # Build extraction prompt
            if schema:
                type_map = {str: "string", int: "integer", float: "number", bool: "boolean"}
                schema_desc = ", ".join(
                    f'"{k}": {type_map.get(v, "string")}' for k, v in schema.items()
                )
                system = (
                    f"Extract data as a JSON array. Each item must have these fields: {{{schema_desc}}}.\n"
                    "Return ONLY a valid JSON array. No explanation. No markdown."
                )
            else:
                system = "Extract the requested information. Return only the data, no explanation."

            response = self._llm.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Extract: {query}\n\nPage content:\n{full_text}"},
                ],
                max_tokens=2000,
            )

            if not response:
                return [] if schema else ""

            response = response.strip()

            # Parse structured response
            if schema:
                # Strip markdown code fences
                if response.startswith("```"):
                    response = response.split("\n", 1)[1].rsplit("```", 1)[0]
                try:
                    data = _json.loads(response)
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict):
                        return [data]
                    return []
                except _json.JSONDecodeError:
                    log.warning("Could not parse JSON from extraction response")
                    return []
            else:
                return response

        except Exception as e:
            log.error("Extraction failed: %s", e)
            return [] if schema else ""
        finally:
            try:
                browser.stop()
            except Exception:
                pass

    def session(self, start_url: str):
        """Create a step-by-step session for manual control.

        Usage:
            with agent.session("https://example.com") as s:
                s.act("Click the login button")
                s.act("Type 'user@email.com' in the email field")
                data = s.extract("Get the account balance")
        """
        return _Session(self, start_url)


class _Session:
    """Step-by-step session for manual agent control.

    Supports multi-tab workflows (signup + email verification, etc).
    Tracks open tabs with names for easy reference. Auto-cleans tabs
    when count exceeds max_tabs (default 5) to prevent RAM bloat.
    """

    MAX_TABS = 5  # Auto-close oldest tabs beyond this limit

    def __init__(self, agent: Agent, start_url: str):
        self.agent = agent
        self.start_url = start_url
        self._browser = None
        self._executor = None
        self._tabs: list[dict] = []  # [{index, name, url}]

    def __enter__(self):
        self._browser = BrowserEngine(
            headless=self.agent.config.browser.headless,
            profile_dir=self.agent.config.browser.profile_dir,
            proxy=self.agent._proxy,
            browser_engine=self.agent.config.browser.browser_engine,
        )
        self._browser.start()
        self._browser.navigate(self.start_url)

        self._executor = Executor(
            browser=self._browser,
            llm=self.agent._llm,
            config=self.agent.config,
            escalation=self.agent.escalation,
            sensitive_data=self.agent._sensitive_data,
        )
        self._tabs = [{"index": 0, "name": "main", "url": self.start_url}]
        return self

    def __exit__(self, *args):
        if self._browser:
            self._browser.stop()

    def act(self, instruction: str) -> bool:
        """Execute a single instruction on the current page."""
        return self._executor.execute_single_step(instruction)

    def extract(self, query: str) -> str:
        """Extract information from the current page."""
        return self._executor.extract_data(query)

    def new_tab(self, url: str, name: str = None) -> int:
        """Open a new tab. Shares cookies/session with all other tabs.

        Args:
            url: URL to open in the new tab.
            name: Optional name for easy reference (e.g. "email", "signup").

        Returns:
            Tab index.

        Usage:
            s.new_tab("https://mail.example.com", name="email")
            # ... do stuff in email tab ...
            s.switch_tab("signup")  # switch back by name
        """
        # Auto-cleanup: close oldest non-current tabs if at limit
        if self._browser.tab_count() >= self.MAX_TABS:
            log.info("Tab limit (%d) reached — closing oldest tab", self.MAX_TABS)
            # Close the second tab (index 1), keep 0 (main) and current
            if len(self._tabs) > 1:
                oldest = self._tabs[1]
                self._browser.close_tab(oldest["index"])
                self._tabs.pop(1)
                # Re-index remaining tabs
                self._reindex_tabs()

        idx = self._browser.new_tab(url)
        tab_name = name or f"tab-{idx}"
        self._tabs.append({"index": idx, "name": tab_name, "url": url})
        return idx

    def switch_tab(self, tab: int | str):
        """Switch to a tab by index (int) or name (str).

        Usage:
            s.switch_tab(0)         # by index
            s.switch_tab("email")   # by name
        """
        if isinstance(tab, str):
            for t in self._tabs:
                if t["name"] == tab:
                    self._browser.switch_tab(t["index"])
                    return
            log.warning("Tab '%s' not found. Open tabs: %s",
                       tab, [t["name"] for t in self._tabs])
            return
        self._browser.switch_tab(tab)

    def close_tab(self, tab: int | str = None):
        """Close a tab by index or name. Defaults to current tab."""
        if tab is None:
            self._browser.close_tab()
            return
        if isinstance(tab, str):
            for i, t in enumerate(self._tabs):
                if t["name"] == tab:
                    self._browser.close_tab(t["index"])
                    self._tabs.pop(i)
                    self._reindex_tabs()
                    return
            return
        self._browser.close_tab(tab)
        self._tabs = [t for t in self._tabs if t["index"] != tab]
        self._reindex_tabs()

    @property
    def tabs(self) -> list[dict]:
        """List open tabs with their names and URLs."""
        # Update URLs from live pages
        ctx = self._browser._context or (self._browser._page.context if self._browser._page else None)
        if ctx:
            pages = ctx.pages
            for t in self._tabs:
                if t["index"] < len(pages):
                    t["url"] = pages[t["index"]].url
        return list(self._tabs)

    def _reindex_tabs(self):
        """Re-index tabs after one is closed."""
        ctx = self._browser._context or (self._browser._page.context if self._browser._page else None)
        if ctx:
            pages = ctx.pages
            for i, t in enumerate(self._tabs):
                if i < len(pages):
                    t["index"] = i
