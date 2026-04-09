# Fantoma Tool Separation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate Fantoma into a browser tool (`Fantoma` class) and a thin orchestrator wrapper (`Agent` class), so external systems can drive the browser step-by-step without going through the LLM loop.

**Architecture:** New `Fantoma` class in `browser_tool.py` composes existing modules (BrowserEngine, AccessibilityExtractor, page_state, observer, form_login, CaptchaOrchestrator, SessionManager). `Agent` becomes a thin wrapper that owns only the LLM reactive loop and delegates all browser operations to `Fantoma`. Orchestrator-only files (executor, checkpoint, memory, script_cache, planner, action_parser, prompts, structured, vision) get deleted or absorbed into agent.py.

**Tech Stack:** Python 3.10+, Playwright, Camoufox, Flask (server.py), pytest

**Spec:** `docs/superpowers/specs/2026-03-31-fantoma-tool-separation-design.md`

---

### Task 1: Create `Fantoma` class — lifecycle and state

**Files:**
- Create: `fantoma/browser_tool.py`
- Test: `tests/test_browser_tool.py`

- [ ] **Step 1: Write failing tests for lifecycle and state**

```python
# tests/test_browser_tool.py
import pytest
from unittest.mock import MagicMock, patch


class TestFantomaLifecycle:
    """Test Fantoma start/stop/restart and get_state."""

    def test_start_returns_state_dict(self):
        """start() should return a dict with url, title, aria_tree, errors, tab_count."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "https://example.com"
            mock_page.title.return_value = "Example"

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = "[1] link 'Home'"
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    state = f.start("https://example.com")

                    assert isinstance(state, dict)
                    assert state["url"] == "https://example.com"
                    assert state["title"] == "Example"
                    assert state["aria_tree"] == "[1] link 'Home'"
                    assert state["errors"] == []
                    assert "tab_count" in state
                    mock_engine.start.assert_called_once()
                    mock_engine.navigate.assert_called_once_with("https://example.com")

    def test_start_without_url(self):
        """start() with no URL should start browser without navigating."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "about:blank"
            mock_page.title.return_value = ""

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = ""
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    state = f.start()
                    mock_engine.navigate.assert_not_called()

    def test_stop_closes_browser(self):
        """stop() should call browser.stop()."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "about:blank"
            mock_page.title.return_value = ""

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = ""
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    f.start()
                    f.stop()
                    mock_engine.stop.assert_called_once()

    def test_stop_without_start_is_safe(self):
        """stop() before start() should not raise."""
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f.stop()  # Should not raise

    def test_get_state_returns_current_page(self):
        """get_state() should return fresh state from current page."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "https://example.com/page2"
            mock_page.title.return_value = "Page 2"

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = "[1] button 'Submit'"
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    f.start()
                    state = f.get_state()
                    assert state["url"] == "https://example.com/page2"
                    assert state["aria_tree"] == "[1] button 'Submit'"

    def test_restart_returns_fresh_state(self):
        """restart() should stop and start with fresh fingerprint."""
        from fantoma.browser_tool import Fantoma
        with patch("fantoma.browser_tool.BrowserEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            mock_page = MagicMock()
            mock_engine.get_page.return_value = mock_page
            mock_page.url = "https://example.com"
            mock_page.title.return_value = "Example"

            with patch("fantoma.browser_tool.AccessibilityExtractor") as MockDOM:
                mock_dom = MagicMock()
                MockDOM.return_value = mock_dom
                mock_dom.extract.return_value = ""
                mock_dom._last_interactive = []

                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    f = Fantoma()
                    f.start("https://example.com")
                    state = f.restart()
                    assert isinstance(state, dict)
                    # stop called at least once (from restart)
                    assert mock_engine.stop.call_count >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py -v`
Expected: FAIL — `fantoma.browser_tool` does not exist

- [ ] **Step 3: Write `Fantoma` class — lifecycle, state, constructor**

```python
# fantoma/browser_tool.py
"""Fantoma — the browser tool. Drive a browser step by step.

Accessibility-first: interacts through the ARIA tree, same channel as screen readers.
No mouse movements, no pixel coordinates, no visual layer signals for anti-bot to detect.
"""
import logging
import time

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser_tool.py tests/test_browser_tool.py
git commit -m "feat: add Fantoma browser tool class — lifecycle and state"
```

---

### Task 2: Add action methods to `Fantoma`

**Files:**
- Modify: `fantoma/browser_tool.py`
- Modify: `tests/test_browser_tool.py`

- [ ] **Step 1: Write failing tests for actions**

```python
# Append to tests/test_browser_tool.py

class TestFantomaActions:
    """Test click, type_text, scroll, navigate, press_key, select."""

    def _make_fantoma(self):
        """Create a Fantoma with mocked internals for action testing."""
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._dom = MagicMock()

        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        mock_page.url = "https://example.com"
        mock_page.title.return_value = "Test"
        f._dom.extract.return_value = "[1] button 'Click me'"
        f._dom._last_interactive = []
        return f, mock_page

    def test_click_returns_result_with_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        mock_element = MagicMock()
        f._dom.get_element_by_index.return_value = mock_element

        with patch("fantoma.browser_tool.click_element", return_value=True):
            with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                with patch("fantoma.browser_tool.wait_for_dom_stable"):
                    result = f.click(1)

        assert result["success"] is True
        assert "state" in result
        assert result["state"]["url"] == "https://example.com"

    def test_click_element_not_found(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        f._dom.get_element_by_index.return_value = None

        with patch("fantoma.browser_tool.detect_errors", return_value=[]):
            result = f.click(999)

        assert result["success"] is False

    def test_type_text_returns_result_with_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        mock_element = MagicMock()
        f._dom.get_element_by_index.return_value = mock_element

        with patch("fantoma.browser_tool.type_into", return_value=True):
            with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                with patch("fantoma.browser_tool.wait_for_dom_stable"):
                    result = f.type_text(1, "hello")

        assert result["success"] is True
        assert "state" in result

    def test_navigate_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()

        with patch("fantoma.browser_tool.dismiss_consent"):
            with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                with patch("fantoma.browser_tool.wait_for_dom_stable"):
                    result = f.navigate("https://other.com")

        assert result["success"] is True
        assert "state" in result
        f._engine.navigate.assert_called_once_with("https://other.com")

    def test_scroll_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()

        with patch("fantoma.browser_tool.scroll_page", return_value=True):
            with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                result = f.scroll("down")

        assert result["success"] is True
        assert "state" in result

    def test_press_key_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()

        with patch("fantoma.browser_tool.detect_errors", return_value=[]):
            result = f.press_key("Enter")

        assert result["success"] is True
        page.keyboard.press.assert_called_once_with("Enter")

    def test_select_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        mock_element = MagicMock()
        f._dom.get_element_by_index.return_value = mock_element

        with patch("fantoma.browser_tool.detect_errors", return_value=[]):
            with patch("fantoma.browser_tool.wait_for_dom_stable"):
                result = f.select(1, "Option A")

        assert result["success"] is True
        mock_element.select_option.assert_called_once_with(label="Option A")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py::TestFantomaActions -v`
Expected: FAIL — action methods don't exist

- [ ] **Step 3: Add action methods to `Fantoma`**

Add these imports at the top of `fantoma/browser_tool.py`:

```python
from fantoma.browser.actions import click_element, type_into, scroll_page
```

Add these methods to the `Fantoma` class after `screenshot()`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser_tool.py tests/test_browser_tool.py
git commit -m "feat: add action methods to Fantoma — click, type, scroll, navigate, select, press_key"
```

---

### Task 3: Add tabs, login, extract, utilities to `Fantoma`

**Files:**
- Modify: `fantoma/browser_tool.py`
- Modify: `tests/test_browser_tool.py`

- [ ] **Step 1: Write failing tests for tabs, login, extract, utilities**

```python
# Append to tests/test_browser_tool.py

class TestFantomaTabs:
    """Test tab management methods."""

    def _make_fantoma(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._dom = MagicMock()
        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        mock_page.url = "https://example.com"
        mock_page.title.return_value = "Test"
        f._dom.extract.return_value = ""
        f._dom._last_interactive = []
        return f, mock_page

    def test_new_tab_returns_state(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        f._engine.new_tab.return_value = 1

        with patch("fantoma.browser_tool.detect_errors", return_value=[]):
            result = f.new_tab("https://other.com")

        assert "state" in result
        f._engine.new_tab.assert_called_once_with("https://other.com")

    def test_list_tabs(self):
        from fantoma.browser_tool import Fantoma
        f, page = self._make_fantoma()
        mock_ctx = MagicMock()
        mock_page1 = MagicMock()
        mock_page1.url = "https://example.com"
        mock_page2 = MagicMock()
        mock_page2.url = "https://other.com"
        mock_ctx.pages = [mock_page1, mock_page2]
        f._engine._context = mock_ctx

        tabs = f.list_tabs()
        assert len(tabs) == 2
        assert tabs[0]["url"] == "https://example.com"
        assert tabs[1]["url"] == "https://other.com"


class TestFantomaLogin:
    """Test login delegation."""

    def test_login_navigates_and_fills(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._dom = MagicMock()
        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        mock_page.url = "https://example.com/dashboard"
        mock_page.title.return_value = "Dashboard"
        f._dom.extract.return_value = ""
        f._dom._last_interactive = []

        with patch("fantoma.browser_tool.form_login") as mock_form_login:
            mock_form_login.return_value = {
                "success": True, "steps": 1, "url": "https://example.com/dashboard",
                "fields_filled": ["email", "password"],
            }
            with patch("fantoma.browser_tool._looks_logged_in", return_value=True):
                with patch("fantoma.browser_tool.detect_errors", return_value=[]):
                    result = f.login("https://example.com/login",
                                     email="test@test.com", password="pass")

        assert result["success"] is True


class TestFantomaExtract:
    """Test extraction with and without LLM."""

    def test_extract_without_llm_returns_aria(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()  # No llm_url
        f._engine = MagicMock()
        f._dom = MagicMock()
        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        f._dom.extract.return_value = "[1] heading 'Books'\n[2] text 'Python for Beginners'"
        f._dom._last_interactive = []

        result = f.extract("What books are listed?")
        assert "Books" in result
        assert "Python for Beginners" in result

    def test_extract_with_llm_calls_chat(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma(llm_url="http://localhost:8080/v1")
        f._engine = MagicMock()
        f._dom = MagicMock()
        mock_page = MagicMock()
        f._engine.get_page.return_value = mock_page
        mock_page.inner_text.return_value = "Book: Python, Price: $10"
        f._dom.extract.return_value = ""
        f._dom._last_interactive = []
        f._llm = MagicMock()
        f._llm.chat.return_value = '{"title": "Python", "price": "$10"}'

        result = f.extract("Extract books", schema={"title": str, "price": str})
        f._llm.chat.assert_called_once()


class TestFantomaUtilities:
    """Test cookie and storage methods."""

    def test_get_cookies(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._engine.get_cookies.return_value = [{"name": "sid", "value": "abc"}]
        cookies = f.get_cookies()
        assert cookies == [{"name": "sid", "value": "abc"}]

    def test_get_storage_state(self):
        from fantoma.browser_tool import Fantoma
        f = Fantoma()
        f._engine = MagicMock()
        f._engine.get_storage_state.return_value = {"cookies": [], "origins": []}
        state = f.get_storage_state()
        assert "cookies" in state
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py::TestFantomaTabs tests/test_browser_tool.py::TestFantomaLogin tests/test_browser_tool.py::TestFantomaExtract tests/test_browser_tool.py::TestFantomaUtilities -v`
Expected: FAIL — methods don't exist yet

- [ ] **Step 3: Add tabs, login, extract, utilities to `Fantoma`**

Add these imports to the top of `fantoma/browser_tool.py`:

```python
from fantoma.browser.form_login import login as form_login, _looks_logged_in
from fantoma.browser.form_memory import FormMemory
from fantoma.session import SessionManager
```

Add these methods to the `Fantoma` class:

```python
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
        Does NOT stop the browser after — caller may want to continue browsing.
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
            log.info("Found saved session for %s — validating", domain)
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
        from fantoma.browser.actions import type_into
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser_tool.py tests/test_browser_tool.py
git commit -m "feat: add tabs, login, extract, utilities to Fantoma tool class"
```

---

### Task 4: Rewrite `Agent` as thin wrapper over `Fantoma`

**Files:**
- Modify: `fantoma/agent.py`
- Modify: `tests/test_browser_tool.py`

- [ ] **Step 1: Write failing tests for the new Agent**

```python
# Append to tests/test_browser_tool.py

class TestAgentWrapper:
    """Test Agent delegates to Fantoma."""

    def test_agent_creates_fantoma(self):
        from fantoma.agent import Agent
        with patch("fantoma.agent.Fantoma") as MockFantoma:
            agent = Agent(llm_url="http://localhost:8080/v1")
            MockFantoma.assert_called_once()

    def test_agent_login_delegates(self):
        from fantoma.agent import Agent
        with patch("fantoma.agent.Fantoma") as MockFantoma:
            mock_f = MagicMock()
            MockFantoma.return_value = mock_f
            mock_f.login.return_value = {"success": True, "fields_filled": ["email"], "steps": 1, "url": "https://x.com"}
            mock_f.start.return_value = {"url": "about:blank", "title": "", "aria_tree": "", "errors": [], "tab_count": 1}

            agent = Agent(llm_url="http://localhost:8080/v1")
            result = agent.login("https://x.com/login", email="a@b.com", password="pass")

            mock_f.login.assert_called_once()
            assert result.success is True

    def test_agent_extract_delegates(self):
        from fantoma.agent import Agent
        with patch("fantoma.agent.Fantoma") as MockFantoma:
            mock_f = MagicMock()
            MockFantoma.return_value = mock_f
            mock_f.start.return_value = {"url": "https://books.com", "title": "", "aria_tree": "", "errors": [], "tab_count": 1}
            mock_f.extract.return_value = [{"title": "Python"}]

            agent = Agent(llm_url="http://localhost:8080/v1")
            result = agent.extract("https://books.com", "Get books", schema={"title": str})

            mock_f.start.assert_called()
            mock_f.extract.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py::TestAgentWrapper -v`
Expected: FAIL — Agent doesn't use Fantoma yet

- [ ] **Step 3: Rewrite `agent.py`**

Replace the entire contents of `fantoma/agent.py` with:

```python
"""Fantoma Agent — convenience wrapper for vibe coders.

Provides run() — describe a task in English, the agent does it.
Delegates all browser operations to the Fantoma tool class.
"""
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from fantoma.browser_tool import Fantoma
from fantoma.llm.client import LLMClient
from fantoma.resilience.escalation import EscalationChain

log = logging.getLogger("fantoma")


@dataclass
class AgentResult:
    """Result of an agent.run() call."""
    success: bool
    data: Any = None
    steps_taken: int = 0
    steps_detail: list = None
    error: str = ""
    tokens_used: int = 0
    escalations: int = 0


# ── LLM prompts (orchestrator concerns) ─────────────────────

REACTIVE_PROMPT = """\
You control a browser. Your job is to COMPLETE the task, not just observe the page.

Pick 1-5 actions from this list (one per line):
CLICK [number]
TYPE [number] "text"
SELECT [number] "option"
SCROLL down
SCROLL up
NAVIGATE https://example.com
PRESS Enter
DONE

Rules:
- Match [number] to the element list shown after the task.
- Elements marked with * are NEW (just appeared from your last action) — focus on these.
- You may return multiple actions (one per line) to execute in sequence.
- To fill a form: TYPE each field, then CLICK submit — all in one response.
- After typing in a search field, add PRESS Enter.
- NAVIGATE and DONE end the sequence — any actions after them are ignored.
- Only say DONE when the task is fully COMPLETED.
- Do NOT say DONE just because you can see a form or page — you must interact with it first.
- If secrets are available, use them with <secret:name> syntax.
- After each action you'll see an outcome. Use this feedback.
- Reply with ONLY action lines, nothing else.\
"""

EXTRACTION_PROMPT = """\
Extract ONLY the answer from the page content below. No code. No explanation. Just the data.\
"""

COMPACTION_PROMPT = """\
Summarize what has been accomplished so far in this browser automation task.
Include: pages visited, forms filled, buttons clicked, data found, errors encountered.
Be specific. Keep it under 200 words.\
"""

# ── Action parsing ───────────────────────────────────────────

_ACTION_PATTERNS = [
    re.compile(r'CLICK\s*\[?(\d+)\]?', re.IGNORECASE),
    re.compile(r'TYPE\s*\[?(\d+)\]?\s*["\'](.+?)["\']', re.IGNORECASE),
    re.compile(r'SELECT\s*\[?(\d+)\]?\s*["\'](.+?)["\']', re.IGNORECASE),
    re.compile(r'SCROLL\s*(UP|DOWN)', re.IGNORECASE),
    re.compile(r'NAVIGATE\s+["\']?(https?://\S+?)["\']?\s*$', re.IGNORECASE),
    re.compile(r'PRESS\s+(\w+)', re.IGNORECASE),
    re.compile(r'DONE', re.IGNORECASE),
]


def _parse_actions(raw: str) -> list[tuple[str, dict]]:
    """Parse LLM response into (action_type, params) tuples."""
    results = []
    for line in (raw or "").strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # CLICK [N]
        m = re.match(r'CLICK\s*\[?(\d+)\]?', line, re.IGNORECASE)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))
            continue
        # TYPE [N] "text"
        m = re.match(r'TYPE\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("type_text", {"element_id": int(m.group(1)), "text": m.group(2)}))
            continue
        # SELECT [N] "value"
        m = re.match(r'SELECT\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("select", {"element_id": int(m.group(1)), "value": m.group(2)}))
            continue
        # SCROLL
        m = re.match(r'SCROLL\s*(UP|DOWN)', line, re.IGNORECASE)
        if m:
            results.append(("scroll", {"direction": m.group(1).lower()}))
            continue
        # NAVIGATE
        m = re.match(r'NAVIGATE\s+["\']?(https?://\S+?)["\']?\s*$', line, re.IGNORECASE)
        if m:
            results.append(("navigate", {"url": m.group(1)}))
            break  # Terminator
        # PRESS
        m = re.match(r'PRESS\s+(\w+)', line, re.IGNORECASE)
        if m:
            results.append(("press_key", {"key": m.group(1)}))
            continue
        # DONE
        if re.match(r'DONE', line, re.IGNORECASE):
            results.append(("done", {}))
            break  # Terminator

        # Fallback: bare [N] → click
        m = re.search(r'\[(\d+)\]', line)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))

        if len(results) >= 5:
            break

    return results


class Agent:
    """Convenience wrapper — describe a task, the agent does it.

    Usage:
        agent = Agent(llm_url="http://localhost:8080/v1")
        result = agent.run("Go to HN and find the top post about AI")
    """

    def __init__(
        self,
        llm_url: str = "http://localhost:8080/v1",
        api_key: str = "",
        model: str = "auto",
        escalation: list[str] = None,
        escalation_keys: list[str] = None,
        max_steps: int = 50,
        sensitive_data: dict = None,
        **kwargs,
    ):
        self.fantoma = Fantoma(llm_url=llm_url, api_key=api_key, model=model, **kwargs)
        self._max_steps = max_steps
        self._sensitive_data = sensitive_data or {}

        endpoints = escalation or [llm_url]
        keys = escalation_keys or [api_key] + [""] * (len(endpoints) - 1)
        self.escalation = EscalationChain(endpoints, keys)
        self._llm = LLMClient(base_url=llm_url, api_key=api_key, model=model)

    def run(self, task: str, start_url: str = None) -> AgentResult:
        """Run a browser task described in English."""
        log.info("Task: %s", task)
        history = []
        steps_detail = []

        try:
            state = self.fantoma.start(start_url)
        except Exception as e:
            return AgentResult(success=False, error=f"Browser start failed: {e}")

        # Timeout via threading.Event (safe with Playwright greenlets)
        timeout_event = threading.Event()
        timer = threading.Timer(self.fantoma.config.browser.timeout, timeout_event.set)
        timer.daemon = True
        timer.start()

        try:
            for step_num in range(1, self._max_steps + 1):
                if timeout_event.is_set():
                    return AgentResult(success=bool(steps_detail), data=state.get("aria_tree", ""),
                                       steps_taken=step_num - 1, steps_detail=steps_detail,
                                       error=f"Timeout after {step_num - 1} steps",
                                       escalations=self.escalation.total_escalations)

                # Mask secrets in the ARIA tree
                aria = state["aria_tree"]
                for name, value in self._sensitive_data.items():
                    aria = aria.replace(value, f"<secret:{name}>")

                # Build LLM messages
                messages = [{"role": "system", "content": REACTIVE_PROMPT}]
                if history:
                    messages.append({"role": "assistant", "content": "\n".join(history[-10:])})
                messages.append({"role": "user", "content": f"Task: {task}\n\nPage ({state['url']}):\n{aria}"})

                # Ask LLM
                raw = self._llm.chat(messages, max_tokens=500)
                if not raw:
                    continue

                actions = _parse_actions(raw)
                if not actions:
                    continue

                for action_type, params in actions:
                    if action_type == "done":
                        data = self._extract_answer(task, state)
                        return AgentResult(success=True, data=data, steps_taken=step_num,
                                           steps_detail=steps_detail,
                                           escalations=self.escalation.total_escalations)

                    # Unmask secrets before executing
                    if "text" in params:
                        for name, value in self._sensitive_data.items():
                            params["text"] = params["text"].replace(f"<secret:{name}>", value)

                    # Call the Fantoma tool method
                    method = getattr(self.fantoma, action_type)
                    result = method(**params)
                    state = result.get("state", state)

                    action_desc = f"{action_type}({params})"
                    outcome = "OK" if result["success"] else "FAILED"
                    history.append(f"Step {step_num}: {action_desc} → {outcome}")
                    steps_detail.append({"step": step_num, "action": action_desc,
                                         "success": result["success"], "url": state["url"]})

                    if not result["success"]:
                        break  # Let LLM re-evaluate on next iteration

                # Loop detection: last 5 actions identical
                if len(history) >= 5 and len(set(history[-5:])) == 1:
                    if self.escalation.can_escalate():
                        new_ep = self.escalation.escalate()
                        self._llm = LLMClient(base_url=new_ep,
                                               api_key=self.escalation.current_api_key())
                        history.clear()
                    else:
                        return AgentResult(success=False, error="Action loop detected",
                                           steps_taken=step_num, steps_detail=steps_detail,
                                           escalations=self.escalation.total_escalations)

            return AgentResult(success=False, error="Max steps reached",
                               steps_taken=self._max_steps, steps_detail=steps_detail,
                               escalations=self.escalation.total_escalations)
        except Exception as e:
            return AgentResult(success=False, error=str(e))
        finally:
            timer.cancel()
            self.fantoma.stop()

    def login(self, url: str, **creds) -> AgentResult:
        """Log into a site. Delegates to Fantoma."""
        try:
            self.fantoma.start()
            result = self.fantoma.login(url, **creds)
            return AgentResult(
                success=result.get("success", False),
                data=result,
                steps_taken=result.get("steps", 0),
            )
        except Exception as e:
            return AgentResult(success=False, error=str(e))
        finally:
            self.fantoma.stop()

    def extract(self, url: str, query: str, schema: dict = None):
        """Navigate to a URL and extract data. Delegates to Fantoma."""
        try:
            self.fantoma.start(url)
            return self.fantoma.extract(query, schema)
        except Exception as e:
            log.error("Extract failed: %s", e)
            return [] if schema else ""
        finally:
            self.fantoma.stop()

    def session(self, start_url: str):
        """Create a step-by-step session."""
        return _Session(self, start_url)

    def _extract_answer(self, task: str, state: dict) -> str:
        """Try to extract a concise answer from the current page."""
        try:
            messages = [
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": f"Task: {task}\n\nPage content:\n{state['aria_tree'][:4000]}"},
            ]
            return self._llm.chat(messages, max_tokens=1000) or ""
        except Exception:
            return state.get("aria_tree", "")[:2000]


class _Session:
    """Step-by-step session using Fantoma tool directly."""

    def __init__(self, agent: Agent, start_url: str):
        self.agent = agent
        self.start_url = start_url

    def __enter__(self):
        self.agent.fantoma.start(self.start_url)
        return self

    def __exit__(self, *args):
        self.agent.fantoma.stop()

    def act(self, instruction: str) -> dict:
        """Execute one instruction. Sends to LLM, executes result via Fantoma."""
        state = self.agent.fantoma.get_state()
        messages = [
            {"role": "system", "content": REACTIVE_PROMPT},
            {"role": "user", "content": f"Task: {instruction}\n\nPage ({state['url']}):\n{state['aria_tree']}"},
        ]
        raw = self.agent._llm.chat(messages, max_tokens=200)
        actions = _parse_actions(raw or "")
        result = state
        for action_type, params in actions:
            if action_type == "done":
                break
            method = getattr(self.agent.fantoma, action_type)
            r = method(**params)
            result = r.get("state", result)
        return result

    def extract(self, query: str) -> str:
        """Extract info from current page."""
        return self.agent.fantoma.extract(query)

    def new_tab(self, url: str, name: str = None) -> dict:
        return self.agent.fantoma.new_tab(url)

    def switch_tab(self, tab: int | str) -> dict:
        return self.agent.fantoma.switch_tab(tab)

    def close_tab(self, tab: int | str = None) -> dict:
        return self.agent.fantoma.close_tab(tab)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py tests/test_browser_tool.py
git commit -m "refactor: rewrite Agent as thin wrapper over Fantoma tool class"
```

---

### Task 5: Delete orchestrator files

**Files:**
- Delete: `fantoma/executor.py`
- Delete: `fantoma/action_parser.py`
- Delete: `fantoma/planner.py`
- Delete: `fantoma/resilience/checkpoint.py`
- Delete: `fantoma/resilience/memory.py`
- Delete: `fantoma/resilience/script_cache.py`
- Delete: `fantoma/llm/prompts.py`
- Delete: `fantoma/llm/structured.py`
- Delete: `fantoma/llm/vision.py`
- Delete: `tests/test_action_parser.py`
- Delete: `tests/test_executor_logic.py`
- Delete: `tests/test_planner.py`
- Delete: `tests/test_structured.py`
- Delete: `tests/test_multi_action.py`
- Delete: `tests/test_cache_replay.py`

- [ ] **Step 1: Delete orchestrator source files**

```bash
cd /home/workspace/workbench/fantoma
rm fantoma/executor.py fantoma/action_parser.py fantoma/planner.py
rm fantoma/resilience/checkpoint.py fantoma/resilience/memory.py fantoma/resilience/script_cache.py
rm fantoma/llm/prompts.py fantoma/llm/structured.py fantoma/llm/vision.py
```

- [ ] **Step 2: Delete tests for deleted files**

```bash
cd /home/workspace/workbench/fantoma
rm tests/test_action_parser.py tests/test_executor_logic.py tests/test_planner.py
rm tests/test_structured.py tests/test_multi_action.py tests/test_cache_replay.py
```

- [ ] **Step 3: Run remaining tests to check for import errors**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/test_container_live.py 2>&1 | head -80`
Expected: No import errors from deleted files. Fix any broken imports.

- [ ] **Step 4: Fix any import errors**

Check `fantoma/resilience/__init__.py` and `fantoma/llm/__init__.py` — remove references to deleted modules if present. Check `fantoma/cli.py` — update any imports from executor or action_parser to use agent.py or browser_tool.py instead.

- [ ] **Step 5: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/test_container_live.py`
Expected: All remaining tests PASS

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add -A
git commit -m "refactor: remove orchestrator files — executor, action_parser, planner, checkpoint, memory, script_cache, prompts, structured, vision"
```

---

### Task 6: Update `__init__.py` and exports

**Files:**
- Modify: `fantoma/__init__.py`

- [ ] **Step 1: Update `__init__.py`**

Replace contents of `fantoma/__init__.py`:

```python
"""Fantoma — The undetectable AI browser agent."""
__version__ = "0.7.0"
from fantoma.browser_tool import Fantoma
from fantoma.agent import Agent, AgentResult
```

- [ ] **Step 2: Verify imports work**

Run: `cd /home/workspace/workbench/fantoma && python -c "from fantoma import Fantoma, Agent, AgentResult; print('OK:', Fantoma, Agent, AgentResult)"`
Expected: Prints OK with class references

- [ ] **Step 3: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/__init__.py
git commit -m "feat: export Fantoma tool class from package, bump to v0.7.0"
```

---

### Task 7: Rewrite `server.py` for new API

**Files:**
- Modify: `server.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Write failing tests for new endpoints**

```python
# tests/test_server.py
import pytest
import json
from unittest.mock import patch, MagicMock


@pytest.fixture
def client():
    """Create a test client with mocked Fantoma."""
    with patch("server.Fantoma") as MockFantoma:
        mock_f = MagicMock()
        MockFantoma.return_value = mock_f
        mock_f.start.return_value = {"url": "https://example.com", "title": "Example",
                                     "aria_tree": "[1] link 'Home'", "errors": [], "tab_count": 1}
        mock_f.get_state.return_value = {"url": "https://example.com", "title": "Example",
                                          "aria_tree": "[1] link 'Home'", "errors": [], "tab_count": 1}
        mock_f.click.return_value = {"success": True, "changed": True, "url_changed": False,
                                      "errors": [], "state": {"url": "https://example.com",
                                      "title": "Example", "aria_tree": "[1] button 'Submit'",
                                      "errors": [], "tab_count": 1}}
        mock_f.stop.return_value = None

        import server
        server._fantoma = None  # Reset state
        app = server.app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c, mock_f


def test_health(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json["status"] == "ok"


def test_start_creates_session(client):
    c, mock_f = client
    r = c.post("/start", json={"url": "https://example.com"})
    assert r.status_code == 200
    assert "url" in r.json
    mock_f.start.assert_called_once_with("https://example.com")


def test_start_while_active_returns_error(client):
    c, mock_f = client
    c.post("/start", json={"url": "https://example.com"})
    r = c.post("/start", json={"url": "https://other.com"})
    assert r.status_code == 409
    assert "error" in r.json


def test_stop_clears_session(client):
    c, mock_f = client
    c.post("/start", json={"url": "https://example.com"})
    r = c.post("/stop")
    assert r.status_code == 200
    mock_f.stop.assert_called_once()


def test_state_returns_current(client):
    c, mock_f = client
    c.post("/start", json={"url": "https://example.com"})
    r = c.get("/state")
    assert r.status_code == 200
    assert "aria_tree" in r.json


def test_click_returns_result(client):
    c, mock_f = client
    c.post("/start", json={"url": "https://example.com"})
    r = c.post("/click", json={"element_id": 1})
    assert r.status_code == 200
    assert r.json["success"] is True
    mock_f.click.assert_called_once_with(1)


def test_action_without_session_returns_error(client):
    c, _ = client
    r = c.post("/click", json={"element_id": 1})
    assert r.status_code == 400
    assert "error" in r.json
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_server.py -v`
Expected: FAIL — new endpoints don't exist

- [ ] **Step 3: Rewrite `server.py`**

Replace the entire contents of `server.py`:

```python
"""Fantoma HTTP API — runs inside the Docker container.

Tool API: /start, /stop, /state, /click, /type, /navigate, etc.
Convenience: /run (uses Agent wrapper), /login, /extract.
Single session at a time.
"""
import json
import logging
import os

from flask import Flask, request, jsonify, send_file
from io import BytesIO

from fantoma.browser_tool import Fantoma
from fantoma.agent import Agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")
log = logging.getLogger("fantoma.server")

app = Flask(__name__)

# ── Config from environment ──────────────────────────────────
LOCAL_LLM_URL = os.environ.get("LOCAL_LLM_URL", "http://host.docker.internal:8081/v1")
BACKUP_LLM_URL = os.environ.get("BACKUP_LLM_URL", "http://host.docker.internal:8082/v1")
CLOUD_LLM_URL = os.environ.get("CLOUD_LLM_URL", "")
CLOUD_LLM_KEY = os.environ.get("CLOUD_LLM_KEY", "")
CAPTCHA_API = os.environ.get("CAPTCHA_API", "capsolver")
CAPTCHA_KEY = os.environ.get("CAPTCHA_KEY", "")
PROXY_URL = os.environ.get("FANTOMA_PROXY", None)
HEADLESS_MODE = os.environ.get("FANTOMA_HEADLESS", "virtual")

# ── Session state ────────────────────────────────────────────
_fantoma: Fantoma | None = None


def _get_fantoma_defaults() -> dict:
    return {
        "llm_url": LOCAL_LLM_URL or None,
        "headless": HEADLESS_MODE,
        "proxy": PROXY_URL,
        "captcha_api": CAPTCHA_API,
        "captcha_key": CAPTCHA_KEY,
        "browser": "camoufox",
    }


def _require_session():
    if _fantoma is None:
        return jsonify({"error": "No active session. POST /start first."}), 400
    return None


# ── Lifecycle endpoints ──────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "session_active": _fantoma is not None,
                     "engine": "camoufox", "display": os.environ.get("DISPLAY", "none")})


@app.route("/start", methods=["POST"])
def start():
    global _fantoma
    if _fantoma is not None:
        return jsonify({"error": "session active", "url": "unknown"}), 409

    data = request.get_json(force=True) or {}
    defaults = _get_fantoma_defaults()
    _fantoma = Fantoma(**defaults)

    try:
        state = _fantoma.start(data.get("url"))
        return jsonify(state)
    except Exception as e:
        _fantoma = None
        return jsonify({"error": str(e)}), 500


@app.route("/stop", methods=["POST"])
def stop():
    global _fantoma
    if _fantoma:
        _fantoma.stop()
        _fantoma = None
    return jsonify({"status": "stopped"})


# ── State endpoints ──────────────────────────────────────────

@app.route("/state", methods=["GET"])
def state():
    err = _require_session()
    if err:
        return err
    return jsonify(_fantoma.get_state())


@app.route("/screenshot", methods=["GET"])
def screenshot():
    err = _require_session()
    if err:
        return err
    img = _fantoma.screenshot()
    return send_file(BytesIO(img), mimetype="image/png")


# ── Action endpoints ─────────────────────────────────────────

@app.route("/click", methods=["POST"])
def click():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.click(data["element_id"]))


@app.route("/type", methods=["POST"])
def type_text():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.type_text(data["element_id"], data["text"]))


@app.route("/navigate", methods=["POST"])
def navigate():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.navigate(data["url"]))


@app.route("/scroll", methods=["POST"])
def scroll():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.scroll(data.get("direction", "down")))


@app.route("/press_key", methods=["POST"])
def press_key():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    return jsonify(_fantoma.press_key(data["key"]))


# ── High-level endpoints ─────────────────────────────────────

@app.route("/login", methods=["POST"])
def login():
    """Manages its own session — starts, logs in, leaves browser open."""
    global _fantoma
    data = request.get_json(force=True)
    url = data.get("url")
    if not url:
        return jsonify({"error": "Missing 'url'"}), 400

    if _fantoma is None:
        defaults = _get_fantoma_defaults()
        _fantoma = Fantoma(**defaults)
        _fantoma.start()

    try:
        result = _fantoma.login(
            url=url, email=data.get("email", ""), username=data.get("username", ""),
            password=data.get("password", ""), first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/extract", methods=["POST"])
def extract():
    err = _require_session()
    if err:
        return err
    data = request.get_json(force=True)
    query = data.get("query")
    if not query:
        return jsonify({"error": "Missing 'query'"}), 400

    schema = data.get("schema")
    if schema:
        type_map = {"str": str, "int": int, "float": float, "bool": bool,
                     "string": str, "integer": int, "number": float, "boolean": bool}
        schema = {k: type_map.get(v, str) for k, v in schema.items()}

    try:
        result = _fantoma.extract(query, schema=schema)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/run", methods=["POST"])
def run_task():
    """Convenience — uses Agent wrapper. Manages its own lifecycle."""
    data = request.get_json(force=True)
    task = data.get("task")
    if not task:
        return jsonify({"error": "Missing 'task'"}), 400

    defaults = _get_fantoma_defaults()
    escalation = [defaults["llm_url"]]
    escalation_keys = [""]
    if BACKUP_LLM_URL:
        escalation.append(BACKUP_LLM_URL)
        escalation_keys.append("")
    if CLOUD_LLM_URL:
        escalation.append(CLOUD_LLM_URL)
        escalation_keys.append(CLOUD_LLM_KEY)

    try:
        agent = Agent(
            llm_url=defaults["llm_url"], escalation=escalation,
            escalation_keys=escalation_keys,
            captcha_api=CAPTCHA_API, captcha_key=CAPTCHA_KEY,
            proxy=PROXY_URL, headless=HEADLESS_MODE, browser="camoufox",
            max_steps=data.get("max_steps", 50), timeout=data.get("timeout", 300),
            sensitive_data=data.get("sensitive_data"),
        )
        result = agent.run(task, start_url=data.get("url"))
        return jsonify({
            "success": result.success, "data": result.data,
            "steps_taken": result.steps_taken, "error": result.error,
            "escalations": result.escalations,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("FANTOMA_PORT", 7860))
    log.info("Fantoma server starting on port %d", port)
    app.run(host="0.0.0.0", port=port, threaded=True)
```

- [ ] **Step 4: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_server.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add server.py tests/test_server.py
git commit -m "refactor: rewrite server.py with tool API endpoints — /start, /stop, /state, /click, /type, /navigate"
```

---

### Task 8: Update CLI imports

**Files:**
- Modify: `fantoma/cli.py`

- [ ] **Step 1: Check CLI for broken imports**

Run: `cd /home/workspace/workbench/fantoma && python -c "import fantoma.cli" 2>&1`
Check output for ImportError referencing deleted files (executor, action_parser, etc.)

- [ ] **Step 2: Fix any broken imports in cli.py**

Replace any imports from `executor`, `action_parser`, `planner` with imports from `agent` or `browser_tool`. The CLI should use `Agent` for `fantoma run "task"` and `Fantoma` for `fantoma test`.

- [ ] **Step 3: Verify CLI loads**

Run: `cd /home/workspace/workbench/fantoma && python -m fantoma.cli --help 2>&1 || python -c "import fantoma.cli; print('OK')"`
Expected: No import errors

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/cli.py
git commit -m "fix: update CLI imports for tool separation"
```

---

### Task 9: Run full test suite and fix breakage

**Files:**
- Various — depends on what breaks

- [ ] **Step 1: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/test_container_live.py 2>&1 | tail -40`
Expected: Note which tests fail

- [ ] **Step 2: Fix any remaining import errors or broken references**

Common issues:
- Tests that imported from `executor` or `action_parser` — update or delete
- `resilience/__init__.py` referencing deleted modules — clean up
- `llm/__init__.py` referencing deleted modules — clean up

- [ ] **Step 3: Run full test suite again**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/test_container_live.py`
Expected: All tests PASS

- [ ] **Step 4: Commit fixes**

```bash
cd /home/workspace/workbench/fantoma
git add -A
git commit -m "fix: resolve remaining test breakage from tool separation"
```

---

### Task 10: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README header and examples**

Replace the opening code example in README.md to show both APIs:

```python
from fantoma import Fantoma, Agent

# Tool API — drive the browser step by step
browser = Fantoma()
state = browser.start("https://news.ycombinator.com")
# state["aria_tree"] → feed to your LLM, get back an action
result = browser.click(3)
# result["state"]["aria_tree"] → updated page
browser.stop()

# Convenience API — describe a task, the agent does it
agent = Agent(llm_url="http://localhost:8080/v1")
result = agent.run("Go to github.com/trending and tell me the top repo")

# Login — no LLM needed
browser = Fantoma()
browser.start()
result = browser.login("https://github.com/login", email="me@example.com", password="...")
browser.stop()
```

- [ ] **Step 2: Update architecture section**

Replace the architecture tree to reflect the new structure:

```
fantoma/
├── browser_tool.py      # Fantoma class — the browser tool (start, stop, click, type, login, extract)
├── agent.py             # Agent class — convenience wrapper with run() for vibe coders
├── session.py           # Encrypted session persistence
├── cli.py               # CLI + interactive mode (uses Agent)
├── config.py            # Settings
├── dom/                 # Page reading (ARIA tree + raw DOM fallback)
├── browser/             # Browser engine, anti-detection, forms, CAPTCHA, consent
├── captcha/             # Detection + solving (PoW, API, human fallback)
├── llm/                 # Thin OpenAI-compatible client (for field labelling + extract)
└── resilience/          # Escalation chain (used by Agent only)
```

- [ ] **Step 3: Add the accessibility-first section**

Add a section explaining the stealth advantage: accessibility-first interaction, no mouse telemetry, same channel as screen readers, legally protected.

- [ ] **Step 4: Update version references to 0.7.0**

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add README.md
git commit -m "docs: update README for v0.7.0 tool separation — dual API, accessibility-first"
```

---

### Task 11: Docker container rebuild and smoke test

**Files:**
- Modify: `Dockerfile` (if needed — check if server.py path changed)

- [ ] **Step 1: Rebuild Docker image**

```bash
cd /home/workspace/workbench/fantoma
docker compose -f docker-compose.fantoma.yml build --no-cache
```

- [ ] **Step 2: Start container**

```bash
docker compose -f docker-compose.fantoma.yml up -d
```

- [ ] **Step 3: Smoke test — health**

```bash
curl -s http://localhost:7860/health | python3 -m json.tool
```

Expected: `{"status": "ok", "session_active": false, ...}`

- [ ] **Step 4: Smoke test — start + state + click + stop**

```bash
# Start session
curl -s -X POST http://localhost:7860/start -d '{"url": "https://news.ycombinator.com"}' -H "Content-Type: application/json" | python3 -m json.tool

# Get state
curl -s http://localhost:7860/state | python3 -m json.tool

# Click first link
curl -s -X POST http://localhost:7860/click -d '{"element_id": 1}' -H "Content-Type: application/json" | python3 -m json.tool

# Stop
curl -s -X POST http://localhost:7860/stop | python3 -m json.tool
```

Expected: Each returns valid JSON, state contains aria_tree, click returns success+state

- [ ] **Step 5: Smoke test — /run still works**

```bash
curl -s -X POST http://localhost:7860/run -d '{"task": "Go to Hacker News and tell me the top post", "url": "https://news.ycombinator.com"}' -H "Content-Type: application/json" | python3 -m json.tool
```

Expected: Returns result with success=true and data containing the top post

- [ ] **Step 6: Commit any Dockerfile changes**

```bash
cd /home/workspace/workbench/fantoma
git add Dockerfile docker-compose.fantoma.yml
git commit -m "build: update Docker for v0.7.0" || echo "No changes needed"
```
