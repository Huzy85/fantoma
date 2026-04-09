# Fantoma Tool Separation — Design Spec

**Date:** 2026-03-31
**Goal:** Separate Fantoma into a browser tool (`Fantoma` class) and a convenience orchestrator wrapper (`Agent` class). The tool is the core product. The wrapper exists for vibe coders who want `agent.run("do this")`.

## Problem

Fantoma currently mixes two roles in one package:

1. **Browser tool** — engine, anti-detection, DOM reading, form filling, CAPTCHA, sessions, actions
2. **Orchestrator** — reactive LLM loop, conversation history, escalation chains, script caching, stall detection, action parsing, message compaction

This makes it impossible for external orchestrators (Scout, custom pipelines) to use Fantoma as a tool. They'd have to bypass `run()` and call internal methods that weren't designed as a public API.

## Solution

Two classes with clear boundaries:

- **`Fantoma`** — the browser tool. Owns browser lifecycle, DOM state, actions, forms, CAPTCHA, sessions. No LLM loop. No task planning. Callers drive it step by step.
- **`Agent`** — thin convenience wrapper. Owns `run()` (the reactive LLM loop). Delegates all browser operations to a `Fantoma` instance.

## Competitive Advantage: Accessibility-First Interaction

Fantoma interacts with pages through the browser's accessibility API (ARIA tree) — the same channel used by screen readers (JAWS, NVDA, VoiceOver). This is both a technical and stealth advantage:

- **Zero mouse telemetry.** No mouse movements, no click coordinates, no scroll velocity. Anti-bot systems that fingerprint pointer behaviour see nothing because there is no pointer.
- **Zero visual layer interaction.** No screenshots, no pixel coordinates, no viewport-relative positioning. The browser sees accessibility API calls — identical to a screen reader user.
- **Legally protected channel.** WCAG, ADA, and the EU Accessibility Act require websites to support accessibility APIs. Blocking accessibility access means blocking disabled users — a legal liability. Sites cannot selectively block Fantoma without also blocking assistive technology.
- **Competitors use detectable methods.** browser-use takes screenshots and generates mouse events. Stagehand v3 uses CDP (Chrome DevTools Protocol). Skyvern combines LLM with computer vision. All of these produce signals that anti-bot systems can fingerprint. Fantoma produces none.

The `Fantoma` class exposes element IDs from the ARIA tree. Actions like `click(5)` and `type_text(3, "hello")` resolve through accessibility handles, not mouse coordinates. This is the core reason Fantoma passes bot detection on sites where screenshot-based agents get caught.

## Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| LLM required? | Optional | Tool works without one. Field labelling degrades to heuristics. `extract()` returns raw ARIA text. |
| Keep `run()`? | Yes, in `Agent` | browser-use has 82K stars because of `run()`. Vibe coders want one-liners. |
| Action return value? | Result + new state | Orchestrator almost always wants state after action. Saves a round trip. |
| Docker API? | Simple, single session | M5 runs one browser. Multi-session is additive later. |
| CLI? | Keep as-is, powered by `Agent` | `fantoma run "task"` uses the wrapper. No rewrite needed. |

---

## 1. `Fantoma` Class — Public API

New file: `fantoma/browser_tool.py`

### Constructor

```python
class Fantoma:
    def __init__(
        self,
        llm_url: str = None,           # Optional — for field labelling + extract
        api_key: str = "",
        model: str = "auto",
        headless: bool = True,
        proxy: str = None,
        browser: str = "camoufox",      # or "chromium"
        captcha_api: str = None,
        captcha_key: str = None,
        email_imap: dict = None,
        verification_callback: callable = None,
        timeout: int = 300,
        trace: bool = False,
    ):
```

LLM is optional. Without it:
- `login()` uses heuristics only (ARIA labels, raw DOM, Form Memory). Works on most standard sites. Fails silently on exotic forms with no identifiable fields.
- `extract()` returns raw ARIA tree text instead of LLM-parsed structured data.
- All other methods work identically.

### Lifecycle

```python
def start(self, url: str = None) -> dict
    # Start browser, optionally navigate. Returns initial state.
    # Creates BrowserEngine, sets up Camoufox/Patchright, Xvfb, etc.

def stop(self) -> None
    # Close browser, clean up asyncio event loop.

def restart(self) -> dict
    # Fresh fingerprint (new Camoufox identity). Returns new state.
    # Used when a site blocks the current fingerprint.
```

### State

```python
def get_state(self) -> dict
    # Returns:
    # {
    #     "url": "https://...",
    #     "title": "Page Title",
    #     "aria_tree": "...",        # Formatted ARIA tree (landmarks, pruned, deduped)
    #     "errors": [...],           # Visible error messages on page (role=alert, aria-invalid, etc.)
    #     "tab_count": 1,
    # }
    # The ARIA tree uses the existing AccessibilityExtractor with all v0.6 features:
    # adaptive DOM modes, landmark grouping, smart pruning, dedup, inline field state.

def screenshot(self) -> bytes
    # PNG bytes of current viewport.
```

### Actions

All actions return the same shape:

```python
{
    "success": bool,
    "changed": bool,          # Did URL or DOM change?
    "url_changed": bool,
    "errors": [...],          # Any new error messages after the action
    "state": {                # Full state dict (same as get_state())
        "url": "...",
        "title": "...",
        "aria_tree": "...",
        "errors": [...],
        "tab_count": 1,
    }
}
```

Methods:

```python
def click(self, element_id: int) -> dict
def type_text(self, element_id: int, text: str) -> dict
def select(self, element_id: int, value: str) -> dict
def scroll(self, direction: str = "down") -> dict     # "up", "down", "left", "right"
def press_key(self, key: str) -> dict                 # "Enter", "Tab", "Escape", etc.
def navigate(self, url: str) -> dict
```

Each action:
1. Injects MutationObserver before acting (existing `observer.py`)
2. Executes the action via `actions.py` (existing code)
3. Waits for DOM stability (adaptive wait, existing `observer.py`)
4. Runs action verification (existing `page_state.py`)
5. Collects mutations and builds new state
6. Returns result + state

Element IDs come from the ARIA tree in `get_state()`. The tree shows `[3] textbox "Email"` — caller passes `3` to `type_text(3, "hello@example.com")`.

### Tabs

```python
def new_tab(self, url: str) -> dict         # Opens tab, navigates, returns state of new tab
def switch_tab(self, tab: int | str) -> dict  # By index or name
def close_tab(self, tab: int | str = None) -> dict  # Defaults to current
def list_tabs(self) -> list[dict]           # [{index, name, url}]
```

### High-Level Operations

```python
def login(self, url: str, email: str = "", username: str = "",
          password: str = "", first_name: str = "", last_name: str = "") -> dict
    # Navigates to url, then runs the full pipeline:
    # saved session check -> form fill -> CAPTCHA -> email verification -> save session.
    # Code-driven. LLM used only for field labelling fallback (if provided).
    # Browser must be started first via start(). Does NOT stop the browser after — caller
    # may want to continue browsing after login.
    # Returns: {"success": bool, "fields_filled": [...], "url": "...", "steps": int,
    #           "verification_needed": "code"|"link"|None}

def extract(self, query: str, schema: dict = None) -> dict | str
    # Extract data from current page.
    # With LLM: sends page text + query to LLM, returns structured data.
    # Without LLM: returns raw ARIA tree text.
    # Browser must be started and on the target page. Does NOT navigate.
    # Caller navigates first via start(url) or navigate(url).
```

Note: `extract()` operates on the current page. The old `agent.extract(url, query)` navigated then extracted. In the tool API, navigation and extraction are separate steps. `Agent.extract()` combines them for convenience.

### Utilities

```python
def get_cookies(self) -> list[dict]
def set_cookies(self, cookies: list[dict]) -> None
def get_storage_state(self) -> dict
def load_storage_state(self, state: dict) -> None
```

### Internal Wiring

`Fantoma` composes these existing modules internally:
- `BrowserEngine` — browser lifecycle, Camoufox/Patchright, popup handling
- `AccessibilityExtractor` — ARIA tree with all v0.6 features
- `page_state` — action verification, error detection
- `observer` — MutationObserver, adaptive wait
- `form_login` — code-driven login
- `form_memory` — SQLite field mapping cache
- `CaptchaOrchestrator` — CAPTCHA detection + solving
- `SessionManager` — encrypted session persistence
- `LLMClient` — thin HTTP client (only if llm_url provided)
- `consent` — cookie banner dismissal (called automatically after navigation)

---

## 2. `Agent` Class — Convenience Wrapper

Stays in `fantoma/agent.py`. Becomes a thin orchestrator built on `Fantoma`.

### Constructor

```python
class Agent:
    def __init__(
        self,
        llm_url: str = "http://localhost:8080/v1",  # Required for Agent (run needs LLM)
        api_key: str = "",
        model: str = "auto",
        escalation: list[str] = None,
        escalation_keys: list[str] = None,
        max_steps: int = 50,
        sensitive_data: dict = None,
        **kwargs,                                    # Passed through to Fantoma
    ):
        self.fantoma = Fantoma(llm_url=llm_url, api_key=api_key, model=model, **kwargs)
        self.escalation = EscalationChain(escalation or [llm_url], ...)
        self._llm = LLMClient(base_url=llm_url, api_key=api_key, model=model)
        self._sensitive_data = sensitive_data or {}
```

### Methods

```python
def run(self, task: str, start_url: str = None) -> AgentResult:
    # The reactive LLM loop:
    # 1. self.fantoma.start(start_url)
    # 2. Loop:
    #    a. Build LLM prompt from task + state.aria_tree + action history
    #    b. Call LLM for next action
    #    c. Parse action string ("CLICK [5]", "TYPE [3] 'hello'", "DONE")
    #    d. Call self.fantoma.click(5) / self.fantoma.type_text(3, "hello") / break
    #    e. Update history with action + result
    # 3. Extract answer if task asked for data
    # 4. self.fantoma.stop()
    # Handles: escalation, loop detection, timeout, history compaction.

def login(self, url, **creds) -> AgentResult:
    # Delegates to self.fantoma.login()
    # Wraps result in AgentResult

def extract(self, url, query, schema=None):
    # self.fantoma.start(url)
    # result = self.fantoma.extract(query, schema)
    # self.fantoma.stop()
    # return result

def session(self, start_url):
    # Returns _Session that uses self.fantoma directly
```

### What Agent Owns (orchestrator concerns)

- LLM conversation history (list of messages)
- History compaction (when context gets too long)
- Action parsing (LLM text -> method calls on Fantoma)
- Escalation chain (switching between LLM endpoints)
- Loop detection (5x same action -> escalate or stop)
- Timeout management (threading.Event, checked between steps)
- Sensitive data masking in LLM prompts (`<secret:email>`)
- `AgentResult` construction

### What Agent Does NOT Own

- Browser lifecycle (Fantoma)
- DOM extraction (Fantoma)
- Form filling (Fantoma)
- CAPTCHA solving (Fantoma)
- Session persistence (Fantoma)
- Action execution (Fantoma)
- Page state verification (Fantoma)

---

## 3. Files Deleted / Reorganised

### Deleted (orchestrator code absorbed into slim agent.py)

| File | Lines | Reason |
|------|-------|--------|
| `executor.py` | ~450 | Reactive loop → ~80 lines in agent.py |
| `resilience/checkpoint.py` | ~80 | Backtracking = orchestrator. Not needed in slim agent. |
| `resilience/memory.py` | ~100 | Action blacklisting. Agent tracks its own history. |
| `resilience/script_cache.py` | ~150 | Cache replay decisions = orchestrator. |
| `action_parser.py` | ~200 | LLM text → actions. Moves into agent.py. |
| `llm/prompts.py` | ~100 | LLM prompts belong to the orchestrator. Moves into agent.py. |
| `llm/structured.py` | ~80 | JSON schema for actions. Moves into agent.py. |
| `llm/vision.py` | ~60 | Vision fallback. Moves into agent.py if kept. |
| `planner.py` | ~50 | Dead code. |

**Total removed from core: ~1,270 lines**
**Total added to agent.py: ~150-200 lines** (slim reactive loop + action parsing + prompts)

### Kept Untouched

All files in `browser/`, `dom/`, `captcha/`, plus `session.py`, `config.py`, `cli.py`, `llm/client.py`.

### New Files

| File | Purpose |
|------|---------|
| `fantoma/browser_tool.py` | The `Fantoma` class (~300 lines) |

### Moved

| What | From | To |
|------|------|----|
| `EscalationChain` | `resilience/escalation.py` | Stays where it is, imported by agent.py only |
| Reactive loop logic | `executor.py` | `agent.py` |
| Action parsing | `action_parser.py` | `agent.py` |
| LLM prompts | `llm/prompts.py` | `agent.py` |

---

## 4. Docker Container API

`server.py` updated to use `Fantoma` directly. Single session.

### Endpoints

| Endpoint | Method | Body | Returns |
|----------|--------|------|---------|
| `/health` | GET | — | `{"status": "ok"}` |
| `/start` | POST | `{"url": "https://..."}` | State dict |
| `/stop` | POST | — | `{"status": "stopped"}` |
| `/state` | GET | — | State dict |
| `/click` | POST | `{"element_id": 5}` | Action result + state |
| `/type` | POST | `{"element_id": 3, "text": "hello"}` | Action result + state |
| `/navigate` | POST | `{"url": "https://..."}` | Action result + state |
| `/scroll` | POST | `{"direction": "down"}` | Action result + state |
| `/press_key` | POST | `{"key": "Enter"}` | Action result + state |
| `/screenshot` | GET | — | PNG image |
| `/login` | POST | `{"url": ..., "email": ..., "password": ...}` | Login result |
| `/extract` | POST | `{"query": ..., "schema": ...}` | Extracted data |
| `/run` | POST | `{"task": ..., "start_url": ...}` | AgentResult (uses Agent wrapper) |

Session behaviour:
- `/start` creates a `Fantoma` instance and stores it in memory
- All action/state endpoints use the stored instance
- `/stop` destroys it
- Second `/start` while session active returns `{"error": "session active", "url": "current_url"}`
- `/run` and `/login` manage their own lifecycle (start + stop internally)

---

## 5. Package Exports

```python
# fantoma/__init__.py
from fantoma.browser_tool import Fantoma
from fantoma.agent import Agent, AgentResult
```

Both classes importable from the top level:
- `from fantoma import Fantoma` — for orchestrators and tool users
- `from fantoma import Agent` — for vibe coders who want `run()`

README shows both:

```python
# Tool API (for orchestrators, integrations, step-by-step control)
from fantoma import Fantoma
browser = Fantoma()
state = browser.start("https://news.ycombinator.com")
# state["aria_tree"] contains the page — feed to your own LLM
browser.click(3)
browser.stop()

# Convenience API (for quick tasks)
from fantoma import Agent
agent = Agent(llm_url="http://localhost:8080/v1")
result = agent.run("Go to HN and find the top AI post")
```

---

## 6. Testing Strategy

- Existing 508 tests remain. Most test `browser/`, `dom/`, `captcha/` modules which are untouched.
- Tests for deleted files (`test_action_parser.py`, `test_executor_logic.py`) get updated to test the equivalent code in `agent.py`.
- New tests for `Fantoma` class: lifecycle, state shape, action results, login delegation, extract with/without LLM.
- Container tests updated for new endpoints.
- The 25-site live test suite runs against `Fantoma` directly (not through `Agent`).

---

## 7. Migration Path

No users yet, so no backwards compatibility needed. This is a clean break:

1. Create `browser_tool.py` with `Fantoma` class
2. Rewrite `agent.py` as thin wrapper
3. Delete orchestrator files
4. Update `server.py` for new endpoints
5. Update `__init__.py` exports
6. Update tests
7. Update README
8. Version bump to v0.7.0
