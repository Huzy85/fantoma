# Fantoma v0.5.0 — Session Persistence, Unified Login Pipeline & Agent Upgrades

## Problem

Fantoma can fill forms, handle CAPTCHAs, and verify emails — but these pieces don't chain together. The browser closes between phases. Cookies are lost. Every login starts from scratch. The hard work of getting into a site is thrown away.

The reactive executor also lags behind browser-use in several areas: one action per LLM call (slow), no DOM filtering for occluded elements (noisy), no free search tools (everything goes through the LLM), and no history compaction (long tasks blow context).

## What This Solves

1. **Unified signup→verify→login pipeline** — one browser session, start to finish
2. **Cookie persistence** — once logged in, saved to disk so next time is instant
3. **Works for both Fantoma-driven and user-driven logins** — cookies saved either way
4. **Multi-action steps** — LLM returns up to 5 actions per call, 3-5x fewer LLM calls
5. **Cleaner DOM** — paint-order filtering removes elements hidden behind others
6. **Free search tools** — find text or query elements on page without LLM cost
7. **Long task support** — message compaction summarizes old history to stay in context
8. **Secure credentials** — sensitive data passed as placeholders, injected at execution time

---

## Part 1: Session Persistence

### New File: `fantoma/session.py`

Saves browser state (cookies + localStorage + sessionStorage) to disk after successful login. Loads them next time to skip the login. One file per site per account. Files are encrypted.

### Storage

```
~/.local/share/fantoma/sessions/
├── .key                                    # Encryption key (0600 perms)
├── github.com--user@email.com.enc
├── render.com--user@email.com.enc
```

### What's Saved

Each encrypted file contains:
- Full browser state in Playwright's `storageState` format (cookies + localStorage + sessionStorage). Many modern sites store auth tokens in localStorage, not cookies.
- The login URL (for validation — navigate here to check if cookies still work)
- Timestamps (when saved, when last validated)

No passwords. Never.

### Security

- Files encrypted with Fernet (from Python `cryptography` library)
- Key auto-generated once, stored in `.key` with `0600` permissions (owner-only read)
- If key is lost/deleted, old sessions stop working — next login creates fresh ones
- `cryptography` is an optional dependency: `pip install fantoma[sessions]`
- Without it, sessions still work but stored as plaintext JSON with a warning logged on save

### Public API

```python
class SessionManager:
    def __init__(self, base_dir="~/.local/share/fantoma/sessions/"):
        ...

    def load(self, domain: str, account: str) -> dict | None:
        """Load saved browser state. Returns None if no session or decryption fails."""

    def save(self, domain: str, account: str, storage_state: dict, login_url: str):
        """Encrypt and save browser state to disk. Atomic writes (temp file → rename)."""

    def delete(self, domain: str, account: str):
        """Remove a saved session."""

    def list(self, domain: str = None) -> list[dict]:
        """List saved sessions. Filter by domain if provided."""
```

### Cookie Validation

When `login()` finds saved cookies:

1. Load full browser state (cookies + localStorage) into browser context
2. Navigate to the saved login URL
3. Run `_looks_logged_in()` — checks URL indicators and body text
4. If logged in → cookies good, return success
5. If not → cookies expired, delete them, proceed with full login

`_looks_logged_in()` gets one small addition: detect "session expired" / "please log in again" signals.

Validation only happens during `login()` calls. No background checks, no scheduled browser launches.

---

## Part 2: Unified Login Pipeline

### Changes to `agent.login()`

```
login() called
    │
    ├─ 1. Try saved session
    │     Load browser state → navigate to site → check if logged in
    │     If yes → done (return success)
    │
    ├─ 2. Fill the form
    │     Same code path as now (heuristics + LLM fallback)
    │
    ├─ 3. Handle CAPTCHA (same as now)
    │
    ├─ 4. Submit and check result
    │     ├─ Landed on dashboard → logged in → save session → done
    │     ├─ Verification page → go to step 5
    │     └─ Still on login page → return error
    │
    ├─ 5. Email verification
    │     Get code/link from IMAP
    │     Enter code in page OR click verification link (same tab)
    │     Wait, re-read page — check for error messages ("invalid code", "expired")
    │     and check if verification page is gone (no more code/link signals)
    │
    ├─ 6. Post-verification check
    │     ├─ Already logged in → save session → done
    │     ├─ Shows login form → fill with same credentials → submit
    │     └─ Neither → navigate back to login URL → fill → submit
    │
    └─ 7. Final check
          Confirm logged in → save session → return result
```

### Browser Rules

- One browser instance per `login()` call
- One tab only — verification links open in the same tab
- IMAP polling is Python-side, browser sits idle during it
- Hard timeout via SIGALRM (existing mechanism)
- `finally` block always kills the browser, even on errors
- Zero browser processes remain after `login()` returns

### Two Modes (unchanged)

- **`agent.login()`** — fully managed. One tab, one browser, automatic cleanup. Requires IMAP for email verification.
- **`agent.session()`** — fully manual. Developer controls tabs. For custom flows.

---

## Part 3: Multi-Action Steps

### What Changes

Currently the LLM returns one action per call. browser-use returns up to 5. This cuts LLM calls by 3-5x.

### How It Works

- Update `REACTIVE_SYSTEM` prompt to allow returning multiple actions (up to 5), separated by newlines
- `execute_reactive()` parses multiple actions from one LLM response
- Execute them sequentially with **page-change guards**: after each action, check if the URL changed or the DOM focus shifted. If so, abort remaining actions (the page moved, remaining actions are stale).
- Actions marked as "terminates sequence" (NAVIGATE, DONE) abort the queue immediately.

### Changes

- `fantoma/llm/prompts.py` — update REACTIVE_SYSTEM to allow multi-action responses, increase max_tokens from 100 to 300
- `fantoma/executor.py` — `execute_reactive()` loop handles action lists, adds page-change guards between actions
- `fantoma/action_parser.py` — `parse_actions()` function that splits multi-action responses

---

## Part 4: Paint-Order DOM Filtering

### What Changes

Currently Fantoma sends every interactive element to the LLM, including elements hidden behind modals, overlays, or other elements. This creates noise — the LLM clicks invisible buttons.

### How It Works

After extracting the accessibility tree, run a JS check on each interactive element:
1. Get the element's bounding box centre point
2. Call `document.elementFromPoint(x, y)` — returns whatever element is visually on top at that point
3. If the returned element is NOT the target element (or a child of it), the element is occluded — remove it from the list

This is what browser-use calls "paint-order filtering." Simple JS, no external dependencies.

### Changes

- `fantoma/dom/accessibility.py` — add `_filter_occluded()` method, called after element extraction
- Existing element indexing stays the same, just with fewer elements

---

## Part 5: Free Search Tools

### What Changes

Two new actions the LLM can use that cost zero tokens:

1. **SEARCH_PAGE "query"** — finds all visible text matches on the page, returns their locations and surrounding context. Like Ctrl+F.
2. **FIND "css selector"** — runs `querySelectorAll` and returns matching elements with their text/attributes. Like the browser DevTools selector.

These let the LLM find things on long pages without scrolling and without extra LLM calls.

### Changes

- `fantoma/action_parser.py` — register SEARCH_PAGE and FIND actions
- `fantoma/browser/actions.py` — implement `search_page()` and `find_elements()` as JS evaluations
- `fantoma/llm/prompts.py` — add these to the REACTIVE_SYSTEM action list

---

## Part 6: Message Compaction

### What Changes

Long tasks (50+ steps) blow the LLM's context window. The LLM starts forgetting what it did. browser-use solves this by summarizing old history with an LLM call.

### How It Works

- Track step history as text (already done via `steps_detail`)
- When history exceeds a threshold (default 30 steps or ~15K tokens), trigger compaction
- Make one LLM call: "Summarize what has been accomplished so far"
- Replace old history with the summary, keep the last 6 steps verbatim
- The summary is marked as "unverified context" so the LLM doesn't claim things are done based on it

### Changes

- `fantoma/executor.py` — add `_compact_history()` method, called every N steps
- `fantoma/llm/prompts.py` — add COMPACTION_SYSTEM prompt

---

## Part 7: Sensitive Data Handling

### What Changes

Currently, if you pass credentials in a task string ("sign up with email foo@bar.com password Secret123"), the password appears in logs, LLM history, and error messages. browser-use solves this with placeholder injection.

### How It Works

- New `Agent` parameter: `sensitive_data={"email": "foo@bar.com", "password": "Secret123"}`
- In the LLM prompt, these appear as `<secret:email>` and `<secret:password>`
- At execution time (just before typing), placeholders are replaced with real values
- Logs show `<secret:password>` not the actual password
- LLM never sees the real credentials

### Changes

- `fantoma/agent.py` — accept `sensitive_data` dict, pass to executor
- `fantoma/executor.py` — replace placeholders in actions before execution, filter from history
- `fantoma/llm/prompts.py` — tell LLM about available secrets in the system prompt

---

## Files Changed (Complete)

| File | Change |
|------|--------|
| `fantoma/session.py` | **New** — SessionManager class with encryption |
| `fantoma/agent.py` | Unified login pipeline, session save/load, sensitive_data param |
| `fantoma/executor.py` | Multi-action steps, page-change guards, message compaction, sensitive data filtering |
| `fantoma/action_parser.py` | Multi-action parsing, SEARCH_PAGE and FIND actions |
| `fantoma/browser/actions.py` | `search_page()` and `find_elements()` implementations |
| `fantoma/browser/form_login.py` | `_looks_logged_in()` — add session expired signals |
| `fantoma/dom/accessibility.py` | Paint-order filtering (`_filter_occluded()`) |
| `fantoma/llm/prompts.py` | Multi-action prompt, compaction prompt, search tool descriptions, sensitive data instructions |
| `pyproject.toml` | Add `sessions` optional dependency (`cryptography`), version bump to 0.5.0 |
| `tests/test_session.py` | **New** — SessionManager tests |
| `tests/test_multi_action.py` | **New** — multi-action parsing and page-change guard tests |
| `tests/test_search_tools.py` | **New** — SEARCH_PAGE and FIND action tests |
| `tests/test_paint_order.py` | **New** — occluded element filtering tests |

## Dependencies

- `cryptography` (optional, for Fernet encryption)
- No other new dependencies

## Out of Scope

- Background session validation / refresh
- Credential storage (passwords never saved to disk)
- Multi-browser-instance management
- Coordinate clicking (requires vision model — Fantoma is DOM-first)
- Cloud browser support (not needed for Fantoma's use case)
- Watchdog event architecture (over-engineered for our needs)
- Planning system (Fantoma's reactive mode + code-first approach works better)
