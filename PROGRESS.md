# Fantoma Development Progress

## Session 14: 2026-04-05 — Persist Container Patches + New Docker API Endpoints

### Summary
Ported two critical patches from the running container back to the git repo so they survive image rebuilds. Updated README with new Docker API endpoints and troubleshooting entries.

### Patches Persisted
1. **SSL fix** (`engine.py`): `ignore_https_errors=True` on both Camoufox and Chromium browser contexts. Prevents SSL certificate errors from blocking navigation on target sites.
2. **Asyncio fix** (`server.py`): Reset the asyncio event loop at `/start` to prevent "Sync API inside asyncio loop" crash from stale greenlet state after a previous session ends.

### New Docker API Endpoints (already in server.py, now documented)
- `POST /evaluate` — run JS in page
- `POST /fill` — fill input by CSS selector (bypasses ARIA tree 15-element limit)
- `POST /select` — select dropdown option by element ID and value
- `GET /state?mode=form|content|navigate` — mode parameter for adaptive DOM extraction
- `POST /manual/open` — open a visible browser session on noVNC (:6080)
- `GET /manual/screenshot` — screenshot of the manual session
- `POST /manual/close` — close manual session (cookies saved to profile)
- `GET /manual/status` — check if a manual session is active

## Session 13: 2026-03-31 — Camoufox glibc 2.42 Fix + 25-Site Live Test

### Summary
Fixed Camoufox SIGSEGV on Fedora 43 / glibc 2.42 using an LD_PRELOAD shim. Validated the fix with a full 25-site live test run using Hermes (9B local model). Result: 23/25 passing (92%). Zero browser crashes across all 25 tests.

### Root Cause

glibc 2.42 calls `madvise(MADV_GUARD_INSTALL)` (arg 102) during `pthread_create` for thread stack guard pages — a Linux 6.7 feature. Camoufox's seccomp BPF filter was compiled before these `madvise` values existed. Child processes (content, RDD, utility) receive SIGSYS and die, causing `TargetClosedError: Page crashed` on any navigation including `data:` URIs.

Firefox installs the seccomp filter via two paths: `prctl(PR_SET_SECCOMP)` and `syscall(SYS_seccomp)`. Both must be intercepted to fix the issue.

### Fix

LD_PRELOAD shim at `/home/steamvibe/tools/madvise_shim.so` (source: `madvise_shim.c`). Intercepts both seccomp install paths and converts `MADV_GUARD_INSTALL`/`MADV_GUARD_REMOVE` calls to no-ops before they reach the seccomp filter. Uses inline assembly for the `syscall()` intercept path to avoid breaking other syscalls via va_arg forwarding.

Required env vars set in `engine.py _start_camoufox()`:
- `LD_PRELOAD=/home/steamvibe/tools/madvise_shim.so`
- `DISPLAY=:99` (Xvfb)
- `LIBGL_ALWAYS_SOFTWARE=1` (Mesa software renderer for glxtest)

Also requires `glxtest` binary copied from `/usr/lib64/firefox/glxtest` to `~/.cache/camoufox/`.

**Wrong approaches (do not use):** Binary-patching JNE/JE in camoufox-bin or libxul.so made it worse. Intercepting madvise at the glibc wrapper level does nothing because glibc uses inline syscalls internally, not its own wrapper. daijro/camoufox#551 was closed with the wrong fix.

**Upgrade warning:** Camoufox upgrades wipe `~/.cache/camoufox/`. After any upgrade: re-copy glxtest, verify Xvfb is on :99, run one test to confirm the shim still works.

### 25-Site Live Test Results (Hermes 9B, 2026-03-31)

| # | Site | Result | Time | Notes |
|---|------|--------|------|-------|
| 1 | Guardian | PASS | 44s | |
| 2 | Reuters | FAIL | 2s | Stale context — agent gave up immediately |
| 3 | TechCrunch | PASS | 181s | |
| 4 | PyPI | PASS | 44s | |
| 5 | npm | PASS | 119s | |
| 6 | Regex101 | FAIL | 457s | Custom code editor — 87 steps, success_check timeout |
| 7 | Python docs | PASS | 249s | |
| 8 | Wayback Machine | PASS | 150s | |
| 9 | CodePen | PASS | 25s | |
| 10 | Reddit | PASS | 63s | React SPA |
| 11 | GitLab | PASS | 34s | |
| 12 | WordPress.com | PASS | 75s | |
| 13 | Twitch | PASS | 52s | Bot-protected |
| 14 | Discord | PASS | 55s | React |
| 15 | Spotify | PASS | 27s | |
| 16 | Dev.to | PASS | 99s | |
| 17 | Disqus | PASS | 78s | |
| 18 | Etsy | PASS | 151s | |
| 19 | eBay UK | PASS | 16s | |
| 20 | Argos | PASS | 56s | |
| 21 | Reed.co.uk | PASS | 43s | |
| 22 | Glassdoor UK | PASS | 34s | |
| 23 | Rightmove | PASS | 19s | |
| 24 | Ticketmaster UK | PASS | 38s | |
| 25 | TotalJobs | PASS | 144s | |

**23/25 passing. Zero browser crashes.** Both failures are agent logic (stale context, custom editor loop), not browser stability.

---

## Session 12: 2026-03-30 — Three Sequential-Test Bug Fixes

### Summary
Tracked down and fixed three bugs that prevented the 25-site live test suite from running reliably. Best result before stopping: tests 1–5 all passed (Guardian 15s, Reuters 30s, TechCrunch 61s, PyPI 29s, npm/npmcharts 88s).

### Changes

| # | Change | Files | What it does |
|---|--------|-------|-------------|
| 1 | asyncio running-loop fix | browser/engine.py | `stop()` now calls `asyncio._set_running_loop(None)` (not `set_event_loop`). Playwright's `_sync_base.py` leaves a stale pointer to the closed loop via `asyncio._set_running_loop()`. The next test's `PlaywrightContextManager.__enter__()` calls `asyncio.get_running_loop()`, finds the closed loop, and immediately errors with "Event loop is closed". Clearing the running-loop pointer (not the current-loop pointer) fixes this. Verified with 6 sequential browser sessions. |
| 2 | Stale page reference after browser restart | fantoma/executor.py | `execute_reactive()` set `page` once before the step loop. Level-3 environment escalation calls `browser.restart_with_new_fingerprint()` which replaces `self._page`. Now: (a) `page` refreshed at top of every step loop iteration, (b) `_maybe_escalate()` returns `bool` signalling restart, (c) all 3 call sites `break` the action batch on `True`. |
| 3 | DeepSeek response_format 400 error | fantoma/llm/client.py | `response_format` (JSON mode) was sent to all endpoints. DeepSeek and other cloud APIs return 400 "response_format type unavailable". Now only sent to local endpoints (`localhost` / `127.0.0.1`). |

### Known Issues (not yet fixed)
- SIGALRM + greenlet deadlock: when the 180s alarm fires inside a Playwright greenlet, `browser.stop()` hangs forever. Requires a timeout approach that doesn't use `signal.alarm` inside greenlets.
- libxul.so SIGSEGV: Firefox 135.0.1-beta.24 crashes at offset `0x30a02e8` after many SIGKILL'd browser processes accumulate system state. Clears on reboot.

---

## Session 11: 2026-03-30 — Event Loop Fix + Popups + Test Coverage Push

### Summary
Event loop crash fix, automatic popup/new tab handling, and 136 new unit tests covering previously untested core modules. 508 total tests.

### Changes

| # | Change | Files | What it does |
|---|--------|-------|-------------|
| 1 | Event loop fix | browser/engine.py, agent.py | `stop()` replaces closed asyncio event loop with fresh one. Removed 2s sleep workaround. |
| 2 | Auto-follow popups | browser/engine.py | `context.on("page")` listener auto-switches to popups/new tabs (OAuth, target="_blank"). Switches back on close. |
| 3 | Action parser tests | tests/test_action_parser.py | 55 tests: normalize_action + execute_action for every action type |
| 4 | Verification flow tests | tests/test_verification_flow.py | 30 tests: _get_verification 3-tier fallback + _enter_verification_code |
| 5 | Form assist tests | tests/test_form_assist.py | 23 tests: after_type, _try_autocomplete, _try_form_submit |
| 6 | Executor logic tests | tests/test_executor_logic.py | 28 tests: _compact_history, _page_likely_has_answer, _task_wants_login, _check_page_change, _maybe_escalate |

### Test Count
- Before: 365
- After: 508

---

## Session 10: 2026-03-30 — v0.6.0 Phase 3 (Competitive Edge Features)

### Summary
Four features from competitive analysis against Agent-E, Stagehand v3, Skyvern 2.0, and browser-use. Closes the gaps that matter while keeping Fantoma's code-first philosophy. 86 new tests, 365 total passing.

### New Features

| # | Feature | Files | What it does |
|---|---------|-------|-------------|
| 1 | Adaptive DOM modes | dom/accessibility.py, executor.py | Three extraction modes (form/content/navigate) inferred per step. Form mode boosts inputs, content mode strips UI for scraping. |
| 2 | ARIA landmark grouping | dom/accessibility.py | Elements grouped under nearest landmark parent: [form: Login], [navigation: Main nav]. Structural context for the LLM. |
| 3 | Per-step success criteria | browser/page_state.py, executor.py | Action-level verification (TYPE checks value, CLICK checks URL) + task-level progress tracking. Stall detection warns LLM after 3 stuck steps. |
| 4 | Script cache + self-healing | resilience/script_cache.py, executor.py | ScriptCache wired into executor. Fuzzy element matching (SequenceMatcher) heals renamed/moved elements during replay. |

### Files Changed

| File | Change |
|------|--------|
| fantoma/dom/accessibility.py | Added `mode` param, LANDMARK_ROLES, landmark tracking + grouped output |
| fantoma/executor.py | Added _infer_dom_mode(), cache lookup, _replay_cached(), stall detection |
| fantoma/browser/page_state.py | Added assess_progress(), _infer_task_intent() |
| fantoma/resilience/script_cache.py | Added heal_action() with difflib fuzzy matching |
| tests/test_dom_modes.py | **New** — 10 tests |
| tests/test_landmarks.py | **New** — 19 tests |
| tests/test_progress.py | **New** — 34 tests |
| tests/test_cache_replay.py | **New** — 8 tests |
| tests/test_script_cache.py | Extended — 8 new heal_action tests |

### Test Count
- Before: 279
- After: 365

### Credits
- Agent-E: adaptive DOM distillation concept (form/content/navigate modes)
- Stagehand v3: self-healing selector pattern
- Skyvern 2.0: planner-actor-validator loop (per-step success criteria)
- Healenium: fuzzy element matching approach
- LCoW (ICLR 2025): research validating contextualized observations (+15-24% LLM success)

---

## Session 9: 2026-03-30 — v0.6.0 Phase 2 (DOM Intelligence)

### Summary
Five code-only features that improve what the LLM sees and how reliably it communicates. Zero additional LLM calls. 39 new tests, 279 total passing.

### New Features

| # | Feature | Files | What it does |
|---|---------|-------|-------------|
| 1 | Structured JSON output | llm/structured.py (new), llm/client.py, executor.py | LLM returns `{"actions": [...]}` via JSON schema. Falls back to text parsing. Eliminates free-text parse failures. |
| 2 | DOM element deduplication | dom/accessibility.py | Removes repeated nav/footer elements by (role, name, state) tuple before pruning. Sites repeat links 3x — LLM now sees each once. |
| 3 | Iframe ARIA extraction | dom/frames.py (new), dom/accessibility.py | Payment forms, embedded logins, consent dialogs in iframes now visible. Up to 5 iframes per page, tagged with source frame. |
| 4 | Adaptive DOM wait | browser/observer.py, executor.py | Debounced MutationObserver replaces fixed network_idle. Resolves when DOM quiet for 300ms. Faster on SPAs. |
| 5 | Inline field state | dom/accessibility.py | `aria-invalid`, `required`, current value, error text shown inline. LLM sees `[3] textbox "Email" [invalid: "Please enter a valid email"]`. |

### Files Changed

| File | Change |
|------|--------|
| fantoma/llm/structured.py | **New** — ACTION_SCHEMA, parse_structured(), get_response_format() |
| fantoma/dom/frames.py | **New** — extract_frame_elements(), collect_all_frame_elements() |
| fantoma/llm/client.py | Added `response_format` param to chat() |
| fantoma/dom/accessibility.py | Added dedup_elements(), enrich_field_state(), iframe merge, _find_in_frame() |
| fantoma/browser/observer.py | Added wait_for_dom_stable() |
| fantoma/executor.py | Wired structured output + adaptive wait |
| tests/test_structured.py | **New** — 12 tests |
| tests/test_dedup.py | **New** — 7 tests |
| tests/test_frames.py | **New** — 7 tests |
| tests/test_adaptive_wait.py | **New** — 4 tests |
| tests/test_field_state.py | **New** — 9 tests |

### Test Count
- Before: 240
- After: 279

### Credits
- browser-use: structured JSON output design and DOM element deduplication pattern
- MDN MutationObserver + Playwright issues: adaptive wait debounce approach
- Playwright frames API: iframe enumeration and ARIA extraction

---

## Session 8: 2026-03-29 — v0.6.0 Phase 1 (Navigation Intelligence)

### Summary
Seven code-only features for better navigation and DOM intelligence. 240 tests passing, 7 commits.

### New Features
1. Action verification + error detection (page_state.py)
2. Smart element pruning — relevance-based scoring (accessibility.py)
3. MutationObserver change tracking (observer.py)
4. Tree diffing — new elements marked with `*` (diff.py)
5. Observation masking — action outcomes verbatim, old DOM dropped (executor.py)
6. Script caching — replays successful sequences without LLM (resilience/)
7. Reactive loop wiring of all above

---

## Session 7: 2026-03-29 — v0.5.0 (Session Persistence, Agent Upgrades)

### Summary
Eight features inspired by browser-use analysis, built on Fantoma's code-first architecture. 205 tests passing.

### New Features

| # | Feature | Files | What it does |
|---|---------|-------|-------------|
| 1 | Session persistence | session.py (new) | Saves cookies + localStorage to encrypted files per domain+account. Fernet encryption, atomic writes. |
| 2 | BrowserEngine storage state | engine.py | get_storage_state() / load_storage_state() for full Playwright storageState format. |
| 3 | Unified login pipeline | agent.py, form_login.py | Session-first → form fill → verify → post-verify check → login-back → save session. One browser, one tab. |
| 4 | Multi-action steps | action_parser.py, executor.py, prompts.py | LLM returns up to 5 actions per call. Page-change guards abort stale actions. 3-5x fewer LLM calls. |
| 5 | Paint-order DOM filtering | accessibility.py | Removes elements hidden behind modals/overlays via elementFromPoint(). Reduces LLM noise. |
| 6 | Free search tools | actions.py, action_parser.py | SEARCH_PAGE (text find) and FIND (CSS selector) — zero LLM cost alternatives to scrolling. |
| 7 | Message compaction | executor.py, prompts.py | Summarizes old step history via LLM when history exceeds 30 steps. Keeps 6 recent verbatim. |
| 8 | Sensitive data handling | agent.py, executor.py | Credentials as `<secret:key>` placeholders. Injected at execution time, filtered from logs/history. |

### Files Changed

| File | Change |
|------|--------|
| fantoma/session.py | **New** — SessionManager with Fernet encryption |
| fantoma/agent.py | Unified login pipeline, _make_browser, _save_session, _enter_verification_code, sensitive_data param |
| fantoma/executor.py | Multi-action loop, page-change guards, compaction, secrets injection/filtering |
| fantoma/action_parser.py | parse_actions(), SEARCH_PAGE/FIND patterns and execution |
| fantoma/browser/actions.py | search_page(), find_elements() |
| fantoma/browser/engine.py | get_storage_state(), load_storage_state() |
| fantoma/browser/form_login.py | _looks_logged_in() session expired signals |
| fantoma/dom/accessibility.py | _filter_occluded() paint-order filtering |
| fantoma/llm/prompts.py | Updated REACTIVE_SYSTEM, COMPACTION_SYSTEM |
| pyproject.toml | Version 0.5.0, sessions optional dep |
| tests/ | 5 new test files, 50+ new tests |

### Test Count
- Before: 155
- After: 205

---

## Session 6: 2026-03-28 — Full Code Audit (13 phases, 48 files)

### Summary
Top-down data-flow audit of every Python file. 15 commits, 155 tests passing.

### Bugs Fixed (10)

| # | File | Bug | Impact |
|---|------|-----|--------|
| 1 | executor.py | `_try_backtrack` used wrong API key on escalation | Auth failures when escalating to cloud API |
| 2 | executor.py | `execute_reactive` called LLM twice for result extraction | Double cost per task completion |
| 3 | engine.py | Chromium path ignored proxy config entirely | All Chromium sessions ran without proxy |
| 4 | engine.py | `navigate()` recovery left engine pointing at blank page on double-failure | Silent bad state after network errors |
| 5 | form_login.py | Name-only form steps didn't set `filled_this_step` | Multi-step signup forms broke on name-only pages |
| 6 | email_verify.py | IMAP connection leaked on exception after connect | Connections never closed on error |
| 7 | orchestrator.py | Human solver discarded the solved token instead of injecting it | Every human-solved CAPTCHA silently failed |
| 8 | extractor.py | JS fallback element lookup off-by-one (0-based vs 1-based) | Wrong element targeted when all selectors failed |
| 9 | client.py | Model resolution cached failure ("auto") permanently | LLM calls failed forever after one /v1/models timeout |
| 10 | weekly_monitor.py | Login test URL pointed to Gmail instead of ProtonMail | Monitor tested wrong site |

### Dead Code Removed

- `DelayConfig` class + `FantomaConfig.delays`, `.verbose`, `.escalation`, `ExtractionConfig.max_content_elements`
- `BrowserEngine.click()`, `.type_text()`, `.scroll()` (zero callers, diverged from actions.py)
- `Planner` import + instantiation in agent.py (reactive mode replaced it)
- `_captcha_telegram` storage in agent.py
- `SKIP_SELECTORS` and `INTERACTIVE_SELECTORS` constants in extractor.py
- Unused imports across 8 files (json, typing.Optional, re, typing.Any, logging)
- Unreachable fingerprint branch, redundant try/except in navigate()

### Naming/Type Fixes

- `captcha_config` param renamed to `config` in form_login.py + all callers
- `Agent.extract()` return type fixed: `list[dict] | str`, not `dict | str`
- Stale docstrings updated (planner references, 0-based vs 1-based)

### Infrastructure Fixes

- `tests/conftest.py` added — excludes live test files from pytest collection (fixed suite hangs)
- Hardcoded credentials redacted in tools/ and scripts, switched to env vars
- weekly_monitor.py missing port 8080 check added
- `FormMemory` context manager support added

### Config Fields Wired

- `CaptchaConfig.human_timeout` → orchestrator.py
- `TimeoutConfig.consent_dismiss` → executor.py

### Follow-up Items (not fixed — larger refactors)

- form_login.py (770 lines) — could split LLM labelling into separate module if it grows past 900
- verification.py — unused in production, only imported by examples/multi_tab.py
- selectors.py — utility functions unused by production code
- api_solver.py — task type names are CapSolver-specific, would need mapping for 2Captcha
- ProxyRotator created fresh on every resolve_proxy() call, can't rotate across sessions
- consent.py `timeout` param accepted but unused in function body

---

## Session 5: 2026-03-28 — v0.4.0 Bug Fixes + E2E Verification

### Bugs Fixed (7)

1. **CaptchaOrchestrator type mismatch** — received `CaptchaConfig` but expected `FantomaConfig`. Every CAPTCHA solve from login flow silently crashed. Fixed: pass full config from `agent.py` and `executor.py`.
2. **Raw DOM inputs overriding ARIA fields** — `_classify_fields` prepended raw DOM inputs, matching `<input name="new-account-password">` as password before ARIA's `textbox "Username"`. Fixed: append raw DOM after ARIA elements.
3. **Scroll loop not terminating** — env escalation cleared action history, resetting loop counter. Fixed: don't clear on env escalation (only on model escalation).
4. **Login verification false negatives** — `_looks_logged_in` didn't check URL change from start URL. Fixed: compare final URL to start URL + added more body text indicators.
5. **Verification page signals incomplete** — missed "activation email", "we've sent", "sent an email". Fixed: added 6 new signal phrases.
6. **IMAP email matching** — brand matching too strict (`dashboard.render.com` → "dashboard" not "render"), old emails returned, colour codes matched as verification codes. Fixed: subdomain stripping, date filtering, prefer links over codes.
7. **Overnight test script missing config** — no escalation, empty IMAP password, no crash recovery. Replaced with `tools/live_test.py`.

### End-to-End Verified

**Render.com** (brand new site, never tested):
- Signup → reCAPTCHA bypassed by Camoufox → email verification (link) via IMAP → account activated → **logged back in to dashboard**
- Credentials: plus-addressed proton email, standard password

**Notion** (code verification path):
- Signup → verification code detected → IMAP polled → 6-digit code extracted and typed → verified

**Discourse** (link verification path):
- Signup → "activation email" detected → IMAP polled → link extracted → navigated → verified

### Infrastructure

- Killed 7 zombie Chromium processes (oldest 19 days, one at 27% CPU)
- Fixed `process-watcher.sh` — stale browser killer for processes >2h with no active parent
- Fixed `tmp-cleanup.sh` — added chromium profiles, camoufox, stale screenshots; fixed arithmetic bug
- New test scripts: `tools/live_test.py` (10-site suite with startup validation), `tools/single_signup_test.py`

### Stats

- 155 unit tests passing
- 10-site live test: 7/10 passed
- Full pipeline verified on 3 sites (Render, Notion, Discourse)

---

## Session 4: 2026-03-27 — v0.4.0 (Email Verification)

### New Features

1. **Autonomous Email Verification** — after signup form submission, Fantoma detects verification pages and completes them automatically. Three-tier resolution: IMAP polling (fully autonomous), user callback function, terminal prompt (interactive fallback).

2. **IMAP Polling** (`fantoma/browser/email_verify.py`) — connects to any IMAP server, polls inbox for verification emails matching the site domain. Extracts 4-8 digit codes (regex with year/small-number filtering) or verification links (URL keyword matching + anchor tag parsing). Handles multipart HTML/text emails. Configurable timeout and poll interval.

3. **Verification Page Detection** (`fantoma/browser/form_login.py`) — `_detect_verification_page()` checks ARIA tree + page body text for code signals ("verification code", "enter code", "OTP", etc.) or link signals ("check your email", "sent you a link", etc.). Returns `verification_needed` type in `login()` result dict.

4. **Agent Wiring** (`fantoma/agent.py`) — `login()` catches `verification_needed`, calls `_get_verification()` (IMAP → callback → terminal). For codes: finds textbox on page, types code via `type_into`, presses Enter. For links: navigates browser to the verify URL. New constructor params: `email_imap` (dict) and `verification_callback` (callable).

5. **EmailConfig** (`fantoma/config.py`) — new dataclass: host, port (default 993), user, password, security ("ssl"/"starttls"/"none"). Added to `FantomaConfig` with empty defaults.

### Stats

- 16 new tests in `tests/test_email_verify.py`
- 146 total unit tests passing (0.35s)
- 3 files modified, 1 new file, 1 new test file
- Version bump pending live verification

---

## Session 3: 2026-03-27 — v0.2.0

### New Features (5)

1. **Form Memory** — SQLite database (`~/.local/share/fantoma/form_memory.db`) records what every login page looks like. Tables: sites (stats), form_steps (field label → purpose mapping), snapshots (full accessibility tree per step). When hardcoded labels don't match, checks database for hints from past visits. Live page always wins over stale data.

2. **Playwright Traces** — `Agent(trace=True)` records screenshots, DOM snapshots, and network activity. Saved as zip files at `~/.local/share/fantoma/traces/`. View with `playwright show-trace <file>.zip`. CLI: `fantoma logs --trace`. Wrapped in try/except for Camoufox compatibility.

3. **Fingerprint Self-Test** — `fantoma test fingerprint` runs 7 in-browser JS checks: UA vs platform, GPU vs OS, timezone vs locale, screen dimensions (catches Camoufox #330), WebGL present, DedicatedWorker cross-check, instance stability (catches Camoufox #328). No external sites visited.

4. **Smart Retry Escalation** — 3-level environment escalation when model escalation fails. Level 1: retry (existing). Level 2: clear cookies. Level 3: close browser, start fresh Camoufox instance with new fingerprint. Configurable: `ResilienceConfig(retry_levels=3)`.

5. **Patchright Chromium Fallback** — `Agent(browser="chromium")` opt-in. Uses Patchright (patches Runtime.enable leak). Install: `pip install fantoma[chromium]`. Default stays Camoufox. Import guard with clear error message.

### Bug Fixes (6)

1. **LLM prompt fix** — REACTIVE_SYSTEM prompt rewritten. LLM was saying DONE immediately without acting. Now: "Only say DONE when the task is fully COMPLETED." Hercules navigates correctly after fix.
2. **Raw DOM fallback** — when ARIA tree misses form inputs (HN, nopCommerce), Fantoma queries raw `<input>` elements via JS. Falls back when: (a) no textboxes in ARIA, or (b) textboxes exist but none match login/signup labels.
3. **Raw DOM buttons** — also finds `<button>` and `input[type=submit]` via JS when ARIA misses the submit button.
4. **OAuth button skip** — SKIP_LABELS now includes Apple, Facebook, GitHub, Google, Twitter, etc. Won't click "Continue with Google" when looking for the login button.
5. **Name fields** — `agent.login()` now accepts `first_name` and `last_name`. Matches: first name, firstname, given name, full name, name (when no username field). Also matches confirm password fields.
6. **Browser retry** — `agent.run()` retries browser start once after "Event loop is closed" error (Camoufox stale event loop between sequential runs).

### Login/Signup Test Results (v0.2.0, code path, no LLM)

| Site | Type | Fields Filled | Result |
|------|------|---------------|--------|
| the-internet.herokuapp.com | Login | Username, Password | **Logged in** |
| GitHub | Login (React) | Email, Password | Form filled |
| Hacker News | Login (vanilla) | acct, pw | Form filled (raw DOM fallback) |
| OrangeHRM | Login (SPA) | Username, Password | **Logged in** |
| SauceDemo | Login | Username, Password | Form filled |
| Practice Automation | Login | Username, Password | **Logged in** |
| DemoQA | Signup (4 fields) | First Name, Last Name, UserName, Password | All filled |
| nopCommerce | Signup (5 fields) | FirstName, LastName, Email, Password, ConfirmPassword | All filled (raw DOM) |
| Parabank | Signup (4 fields) | FirstName, LastName, Username, Password | **Account created** |
| Automationexercise | Signup (2 fields) | Name, Email | Multi-step form |
| HN | Signup (LLM) | Username, Password | **Account created** (LLM clicked create) |

### Stats

- 12 new commits on `fantoma-v0.2` branch
- 120+ unit tests (was 83)
- 17 files changed, +1,608 lines
- Version: 0.1.0 → 0.2.0
- Specs: `docs/superpowers/specs/2026-03-27-fantoma-v02-design.md`
- Plan: `docs/superpowers/plans/2026-03-27-fantoma-v02-plan.md`

---

## Sessions 1-2: 2026-03-22 to 2026-03-23

### Built
- 31 Python files across 7 packages
- 41 unit tests passing
- 4 examples, README.md, pyproject.toml, LICENSE (MIT)
- Docs: competitive analysis, proxy/VPN guide
- Stress test infrastructure (8-hour parallel runs)
- Python package: `pip install fantoma`

### LLM Compatibility (6 models, 688+ tests)

| Model | Type | Size | Tests | Pass | Fastest |
|-------|------|------|-------|------|---------|
| Homer (Qwen3.5-122B) | Local | 122B | 13+ | 13/13 | 14s |
| Hercules (Qwen3-Coder) | Local | 45B | 3 | 3/3 | 27s |
| Phi-3.5-mini | Local | 3.8B | 22 | 22/22 | 7s |
| Claude Sonnet | Cloud API | — | 280+ | ~98% | 12s |
| Kimi moonshot | Cloud API | — | 183+ | ~96% | 8s |
| GPT-4o-mini | Cloud API | — | 151+ | ~99% | 4s |

### Phi-3.5-mini (3.8B) — Small Model Validation

**15/15 PASS on complex bot-protected sites (2026-03-23):**

Instagram (40s), Facebook (9s), TikTok (37s), Reddit (9s), X.com (40s), Amazon UK (11s), Nike (53s), Walmart (25s), Indeed (76s), Craigslist (23s), LinkedIn (43s), Booking.com (11s), nowsecure.nl (81s), GitHub (10s), DuckDuckGo (26s)

**Key insight:** Code-based answer detection (`_page_likely_has_answer()`) bypasses small model DONE detection weakness. Model only needs to navigate; code handles completion. Runs on 8GB NVIDIA GPUs (~2.4GB model).

### Anti-Detection (proven across 688+ tests)

**Stress tests (8 hours, 3 APIs parallel):** 587 tests, 20 sites. 1.4% detection (all Reddit IP-based). Zero fingerprint detections. Zero CAPTCHAs triggered.

**Through ProtonVPN:** 20/20 PASS across 3 LLMs. Different IP confirmed.

**Protection systems bypassed (100%):**
- Cloudflare (X.com, Indeed, Etsy, Reddit, nowsecure.nl)
- DataDome (Amazon UK)
- PerimeterX (Zillow, Walmart)
- Akamai (Nike)
- Meta anti-bot (Instagram, Facebook)
- Custom (LinkedIn, Booking.com, Ticketmaster, TikTok, Craigslist, Rightmove, StubHub, GitHub)

**Fingerprint tests:** bot.sannysoft.com — all pass. nowsecure.nl — all pass.

**Only detection:** Reddit after 2+ hours from same IP (IP rate limiting, not fingerprint). Fixed with proxy rotation.

### CAPTCHA Solving (2 types proven)

| Type | How | Proven on |
|------|-----|-----------|
| ALTCHA (proof-of-work) | Click checkbox, browser solves automatically (free) | civilservicejobs.service.gov.uk |
| reCAPTCHA v2 | CapSolver API token injection (paid key) | Google reCAPTCHA demo |
| hCaptcha | Detection works, CapSolver rejected demo key | Partially tested |
| Cloudflare Turnstile | Untested (Camoufox prevents triggering) | — |

**User setup:** No CAPTCHA config needed for 99% of cases (Camoufox prevents them). For sites that force CAPTCHAs: `captcha_api="capsolver"` + API key.

### Accessibility Mode (proven)

- ARIA tree extraction via `page.locator("body").aria_snapshot()`
- Presents as assistive technology (prefers-reduced-motion, screen reader flags)
- 10/10 sites pass in accessibility mode
- Cleaner output than raw DOM: roles + names instead of HTML tags
- Element lookup via `get_by_role()` — more stable than CSS selectors
- Falls back to DOM extraction when ARIA tree is empty
- Legal protection: sites legally required to support assistive tech (WCAG 2.1, ADA, Equality Act 2010)
- **Fantoma's original anti-detection contribution** (not from Camoufox)

### Proxy/VPN (proven)

- ProtonVPN through gluetun: 20/20 PASS, confirmed different IP
- Proxy rotation: `VPNProxy(servers=[...])` rotates round-robin or random
- Supports: SOCKS5, HTTP, any provider (ProtonVPN, NordVPN, Mullvad, Bright Data, Oxylabs)
- Single proxy: `proxy="socks5://localhost:1080"`
- Rotation list: `proxy=["proxy1", "proxy2", "proxy3"]`
- VPN provider: `VPNProxy(servers=[...], username=..., password=...)`

### Structured Extraction (proven)

```python
books = agent.extract("https://books.toscrape.com", "First 3 books", schema={"title": str, "price": str})
# → [{"title": "A Light in...", "price": "£51.77"}, ...]
```

- Books to Scrape: 11s, correct JSON with 3 books
- Wikipedia: 12s, correct population (3,186,581) + capital (Cardiff)

### Cookie Consent (proven)

Auto-dismisses on: OneTrust (Rightmove, Indeed), Amazon (sp-cc), Meta (Instagram, Facebook), CookieBot. Uses JS click + force-hide. Detects by element selectors AND by counting cookie-related buttons (catches Meta's non-standard dialog).

### Autocomplete Handler (proven)

Spatial detection of dropdown suggestions near focused input. Clicks best text match. Proven on Rightmove ("LL65" exact match) and Booking.com ("Holyhead, Anglesey").

### API Usage (measured during stress tests)

| Provider | Tests | Notes |
|----------|-------|-------|
| Local (any size) | 29+ | Free — no API calls |
| Kimi Moonshot | 902 | Most affordable cloud option |
| GPT-4o-mini | 180 | Good balance of speed and reliability |
| Claude Sonnet | 1,159 | Most capable, highest reliability (99.9%) |

Fantoma uses 3-5 LLM calls per task at ~200 tokens each. Check each provider's current pricing.

### Architecture

- **Reactive mode**: see page → pick ONE action → repeat. No planner needed.
- **Accessibility-first**: ARIA tree default, DOM fallback.
- **0-based indexing**: universal across all models.
- **15 element cap**: priority sort (inputs > suggestions > buttons > links).
- **max_tokens**: 50 for actions, 200 for extraction.
- **5s click timeout**: prevents Playwright hangs.
- **Global SIGALRM timeout**: prevents infinite process hangs.
- **Scroll loop detection**: 5x same action in reactive mode = force DONE. Memory blacklists after 3 failures.
- **Cookie consent**: JS click + force-hide, Meta/OneTrust/CookieBot/Amazon.
- **Autocomplete**: spatial detection, clicks best text match near input.
- **Navigation crash recovery**: catches "context destroyed" from page transitions.
- **Action extraction**: regex finds CLICK/TYPE/etc from verbose LLM responses.
- **chat_template_kwargs**: only sent to local endpoints.
- **Proxy**: resolved per-session via ProxyRotator/VPNProxy.
- **Multi-tab sessions**: new_tab(url, name), switch_tab(name/index), close_tab(), tabs property.
- **Tab auto-cleanup**: MAX_TABS=5, oldest non-current tab closed automatically.
- **Content extraction**: targets `main` / `[role=main]` instead of `body` — strips nav noise.
- **Email verification**: regex code extraction (4-8 digits), URL pattern matching for verify links.
- **TYPE skip verification**: TYPE actions always succeed if element found (no page-change check).
- **Network idle wait**: 15s networkidle replaces 5s domcontentloaded for SPA transitions.
- **Heading cap**: raised from 10 to 25 in ARIA extraction (was cutting off content).

### Bugs Fixed: 28

### Git Commits: 31

### What's Validated for v0.1.0

- [x] Anti-detection: 587 stress tests + 20 VPN tests + 10/10 batch
- [x] Accessibility mode: 10/10 sites, ARIA tree
- [x] CAPTCHA: ALTCHA (CS Jobs) + reCAPTCHA v2 (Google demo)
- [x] VPN/proxy: ProtonVPN confirmed, rotation built
- [x] Structured extraction: schema-validated JSON
- [x] Cookie consent: Meta, OneTrust, CookieBot, Amazon
- [x] Autocomplete: Rightmove, Booking.com
- [x] 6 LLMs: 3.8B-122B local + Claude, Kimi, GPT-4o-mini
- [x] Small model (Phi-3.5-mini 3.8B): 22/22 tests, 15/15 complex sites, 8GB GPU proven
- [x] Reactive mode: default, works across all models
- [x] Proxy rotation: VPNProxy class, round-robin/random
- [x] Stress test infrastructure: parallel 8-hour runs + audit
- [x] Competitive analysis + roadmap
- [x] Authenticated login: ProtonMail inbox accessed (Hercules + Phi)
- [x] SPA navigation: React/SPA apps with network idle wait
- [x] Multi-tab sessions: new_tab(), switch_tab(), close_tab() with named tabs
- [x] Account creation: Reddit (verified), Booking.com (verified), Stack Overflow (reached verification)
- [x] Email verification: code extraction (regex), link extraction (URL pattern matching)
- [x] Tab auto-cleanup: MAX_TABS=5, oldest tabs closed automatically

### Complete Test Log

**Session 1 (2026-03-22):**
- 41 unit tests (planner, DOM extractor, resilience) — all pass
- 13 live tests with Homer (Qwen3.5-122B) — 13/13 PASS
- 3 live tests with Hercules (Qwen3-Coder) — 3/3 PASS
- 10-site batch test (anti-detection) — 10/10 PASS
- Bot fingerprint tests: bot.sannysoft.com PASS, nowsecure.nl PASS

**Session 2 (2026-03-23):**
- 8-hour stress tests (3 APIs in parallel):
  - Claude Sonnet: 264 tests, 20 sites, 100% pass rate
  - GPT-4o-mini: 143 tests, 20 sites, 100% pass rate
  - Kimi Moonshot: 180 tests, 20 sites, 97.8% pass rate (4 failures: timeouts on Ticketmaster, Indeed, Rightmove)
- Claude aggressive batch: 4 additional tests, 100% pass
- VPN tests through ProtonVPN: 20/20 PASS, confirmed different IP
- Accessibility mode: 10/10 sites PASS
- CAPTCHA tests:
  - ALTCHA on civilservicejobs.service.gov.uk: PASS (extracted "Security Officer")
  - reCAPTCHA v2 on Google demo: PASS ("Verification Success... Hooray!")
  - hCaptcha: detection works, CapSolver rejected demo key
- Structured extraction:
  - Books to Scrape: 11s, correct JSON with 3 books
  - Wikipedia: 12s, correct population + capital
- Phi-3.5-mini (3.8B) — 7 initial tests: 7/7 PASS
  - GitHub 9s, Wikipedia 11s, HN 17s, DuckDuckGo 33s, Amazon 13s, Books 7s, Nike 8s
- Phi-3.5-mini — 15 complex site tests: 15/15 PASS
  - Instagram 40s, Facebook 9s, TikTok 37s, Reddit 9s, X.com 40s, Amazon UK 11s, Nike 53s, Walmart 25s, Indeed 76s, Craigslist 23s, LinkedIn 43s, Booking.com 11s, nowsecure.nl 81s, GitHub 10s, DuckDuckGo 26s

- Authenticated login tests (ProtonMail):
  - Hercules (Qwen3-Coder): PASS — logged in, extracted 3/3 emails with subjects, senders, dates
  - Phi-3.5-mini (3.8B): PASS — logged in, extracted 2/3 emails (SPA rendering delay)
  - ProtonMail security did not detect bot activity
- Code improvements from login testing:
  - TYPE actions no longer require page-change verification (typing doesn't alter DOM)
  - Network idle wait replaces fixed 5s wait (catches SPA transitions)
  - Minimum context window documented: 8K minimum, 12K+ recommended

- Account creation tests (10 sites, 2 models in parallel):
  - Homer: Reddit (verified with code), Booking.com (code sent), GitHub (all steps OK), Pinterest (3/4), Medium (email submitted)
  - Phi: Stack Overflow (reached verification), Etsy (form filled), HackerNews (form filled), Indeed (email typed), TripAdvisor (accessed)
- Multi-tab verification (Reddit):
  - Tab 0: signup, Tab 1: ProtonMail login, regex extracted code 611955, switched back, entered code, Reddit accepted
  - Full loop: signup → receive email → extract code → verify — zero human intervention
- Code-based verification helper built:
  - `extract_verification_code()`: regex for 4-8 digit codes, filters years/common numbers
  - `extract_verification_link()`: URL pattern matching for verify/confirm/activate
  - `detect_verification_type()`: auto-detects code vs link
- Multi-tab session API: `new_tab(url, name)`, `switch_tab(name)`, `close_tab(name)`, `tabs` property
  - Auto-cleanup at MAX_TABS=5 to prevent RAM bloat
- Content extraction fix: uses `main` or `[role=main]` selector instead of `body` — strips nav noise
- Phi ProtonMail extraction: 3/3 emails after `main` selector fix (was 2/3 before)

- Full monitor suite (20 tests, Hercules + Phi side-by-side):

  | Test | Hercules (45B) | Phi (3.8B) |
  |------|---------------|------------|
  | bot.sannysoft (fingerprint) | PASS 18s | — |
  | nowsecure.nl (fingerprint) | PASS 15s | PASS 31s |
  | browserleaks.com (fingerprint) | PASS 20s | PASS 6s |
  | books.toscrape (scraping) | PASS 15s | PASS 7s |
  | quotes.toscrape (scraping) | PASS 13s | PASS 7s |
  | httpbin (headers check) | PASS 19s | PASS 9s |
  | Google reCAPTCHA demo | TIMEOUT (needs API key) | TIMEOUT |
  | CS Jobs (ALTCHA CAPTCHA) | PASS 21s | PASS 77s |
  | GitHub (rate limiting) | PASS 15s | PASS 5s |
  | Amazon UK (DataDome) | PASS 12s | PASS 10s |
  | Reddit (Cloudflare) | PASS 12s | PASS 11s |
  | Instagram (Meta anti-bot) | PASS 14s | PASS 108s |
  | Nike (Akamai) | PASS 55s | PASS 85s |
  | LinkedIn (custom) | PASS 11s | PASS 42s |
  | Craigslist (aggressive) | PASS 14s | PASS 12s |
  | Booking.com (PerimeterX) | PASS 13s | PASS 9s |
  | Structured extraction | PASS 14s | — |
  | ProtonMail login | PASS 23s | — |
  | Multi-tab | PASS 18s | — |
  | Verification regex | PASS 0s | — |

  19/20 PASS. Only failure: reCAPTCHA demo (needs paid CapSolver API key — expected).

  **Timing comparison:** Hercules averages ~18s per test. Phi averages ~30s but some sites take much longer (Instagram 108s, Nike 85s, CS Jobs 77s). Simple sites Phi is faster (GitHub 5s vs 15s, books.toscrape 7s vs 15s). Complex SPAs favour the larger model.

- Overnight stress test (7 hours, 3 cloud APIs, 20 sites each, 2026-03-24):

  | Provider | Rounds | Tests | Pass | Rate |
  |----------|--------|-------|------|------|
  | OpenAI GPT-4o-mini | 9 | 180 | 180 | 100% |
  | Claude Sonnet | 58 | 1,159 | 1,158 | 99.9% |
  | Kimi Moonshot | 46 | 902 | 872 | 96.7% |
  | **Combined** | **113** | **2,241** | **2,210** | **98.6%** |

  Intervals: 10min (first 2h) → 5min (2-4h) → 3min (4-7h). Progressively more aggressive.

  **Claude failures:** 1 total — Walmart "Max steps reached" (slow page load)
  **OpenAI failures:** 0 — perfect run
  **Kimi failures:** 30 total — spread across 16 sites, mostly "Max steps reached". Rough patch 02:38-04:10 (possible API rate limiting). Worst: nowsecure.nl (5), X.com (4), Nike (3), Instagram (3)

  **Zero fingerprint detections across all 2,241 tests. No site blocked Fantoma.**

**Total: 2,990+ live browser tests across 6 LLMs and 20+ bot-protected sites.**

### Still Needed Before Publish

1. ~~Update README with all new features~~ DONE (2026-03-23)
2. ~~Account creation + email verification~~ DONE (2026-03-23) — Reddit verified, Booking.com verified, 10 sites tested
3. ~~Multi-tab sessions~~ DONE (2026-03-23) — named tabs, auto-cleanup, shared cookies
4. ~~Login flows~~ DONE (2026-03-23) — ProtonMail (both models), SPA wait fixes
5. ~~Small vs large model comparison~~ DONE (2026-03-23) — documented in README
6. ~~Weekly monitor~~ DONE (2026-03-23) — Friday 02:00, 20 tests, Telegram notification
7. ~~Full monitor suite run~~ DONE (2026-03-23) — 19/20 PASS on both models
8. ~~Final regression test~~ DONE (2026-03-23) — 41/41 unit tests pass, 3 tests updated for 0-based indexing
9. ~~Code audit + refactor~~ DONE (2026-03-23) — executor split (738→452 lines), action_parser.py, captcha/orchestrator.py, 77 lines dead code removed
10. ~~Configurable timeouts/limits~~ DONE (2026-03-23) — TimeoutConfig, ExtractionConfig in config.py
11. ~~Prompt improvements~~ DONE (2026-03-23) — REACTIVE_SYSTEM updated, EXTRACTION_SYSTEM extracted, LLM client retry
12. ~~Escalation API keys~~ DONE (2026-03-23) — per-endpoint keys, fixed local→cloud escalation bug
13. ~~CLI setup wizard~~ DONE (2026-03-24) — fantoma setup/test/run/monitor, guided 4-step wizard
14. ~~Cleanup for release~~ DONE (2026-03-24) — temp files deleted, .gitignore updated, AgentResult exported, 7 examples
15. ~~Overnight stress test~~ DONE (2026-03-24) — 2,241 tests, 98.6% pass rate, 3 APIs, 7 hours
16. Fresh install test (pip install in clean venv)
17. Create GitHub repo
