# Fantoma Full Code Audit — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit every Python file in Fantoma for correctness and code health, fixing issues on the spot.

**Architecture:** Top-down, data-flow order. Start with config (everything depends on it), follow how a request flows through agent → executor → browser → captcha → DOM → LLM → resilience, then CLI, tests, tools. Each phase ends with a test run checkpoint.

**Tech Stack:** Python 3.14, Playwright/Patchright, Camoufox, httpx, SQLite

**Per-file checklist:**
1. Correctness — types match caller/callee, config passed right, returns consumed properly, errors not swallowed
2. Dead code — unused imports, unreachable branches, leftover debug
3. Boundaries — file does one job
4. Duplication — same logic repeated across files
5. Naming — misleading names, stale comments

**Fix policy:** Fix bugs/dead code/naming on the spot. Flag large refactors (file splits) in a follow-up section.

---

### Task 1: Phase 1 — Config (100 lines)

**Files:**
- Audit: `fantoma/config.py`
- Test: `tests/` (full suite)

- [ ] **Step 1: Read config.py line by line**

Read `fantoma/config.py`. Check:
- All dataclass fields have correct types and defaults
- EmailConfig, CaptchaConfig, FantomaConfig are consistent with how agent.py and executor.py use them
- No dead fields or unused imports

- [ ] **Step 2: Cross-reference config usage**

Grep for `config.captcha`, `config.email`, `config.timeouts` across the codebase. Verify every field in config.py is actually used, and every config access in other files references a field that exists.

```bash
grep -rn "config\.\(captcha\|email\|timeouts\|resilience\|browser\)" fantoma/ --include="*.py" | grep -v __pycache__
```

- [ ] **Step 3: Fix any issues found**

Apply fixes directly. Common issues to watch for:
- Fields accessed but not defined in the dataclass
- Default values that don't match how the field is used (e.g. `""` for something that should be `None`)
- Type mismatches between definition and usage

- [ ] **Step 4: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

Expected: Same pass/fail count as baseline (167 passed, 1 failed, 2 errors).

- [ ] **Step 5: Commit if changes were made**

```bash
git add fantoma/config.py
git commit -m "audit: phase 1 — config.py reviewed and cleaned"
```

---

### Task 2: Phase 2 — Core Orchestration (1,329 lines)

**Files:**
- Audit: `fantoma/agent.py` (619 lines), `fantoma/executor.py` (615 lines), `fantoma/planner.py` (95 lines)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read agent.py line by line**

Check:
- `Agent.__init__` — are all params stored correctly on self/config?
- `Agent.run()` — does it pass config correctly to Executor?
- `Agent.login()` — does it pass config correctly to form_login? Does it handle verification_needed return?
- `Agent.extract()` — what does it return? Does the caller (test_extract_quotes) expect the right type?
- Public API surface — are method signatures consistent with README/examples?
- Error handling — are exceptions caught too broadly? Any bare `except:`?

- [ ] **Step 2: Read executor.py line by line**

Check:
- CaptchaOrchestrator receives FantomaConfig (not CaptchaConfig) — was bug #1 from Session 5 fully fixed?
- LLM client creation — correct URL/key/escalation passing?
- Step loop — action parsing, DONE detection, max steps
- Screenshot function passed to CAPTCHA handler correctly
- Return values — what does executor return to agent?

- [ ] **Step 3: Read planner.py line by line**

Check:
- Input/output types
- Integration with executor (if any)
- Dead code

- [ ] **Step 4: Fix all issues found across the three files**

Fix directly. Pay special attention to:
- The agent.extract() return type (known bug — returns list but test expects result object)
- Config wiring between agent → executor → captcha
- Any remaining CaptchaConfig vs FantomaConfig mismatches

- [ ] **Step 5: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

Expected: Baseline or better (fix for extract_quotes would make it 168 passed).

- [ ] **Step 6: Commit**

```bash
git add fantoma/agent.py fantoma/executor.py fantoma/planner.py
git commit -m "audit: phase 2 — agent, executor, planner reviewed and cleaned"
```

---

### Task 3: Phase 3 — Browser Core (697 lines)

**Files:**
- Audit: `fantoma/browser/engine.py` (428), `fantoma/browser/actions.py` (206), `fantoma/browser/humanize.py` (63)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read engine.py line by line**

Check:
- Browser launch — Camoufox vs Patchright vs Chromium selection logic
- Page lifecycle — new_page, close, context management
- Proxy configuration wiring
- Screenshot/trace methods
- Resource cleanup (are browsers always closed on error?)
- Navigation timeout and retry logic (the 30s timeout seen in logs)

- [ ] **Step 2: Read actions.py line by line**

Check:
- `type_into`, `click_element`, `scroll` — do they accept the right element types?
- Humanize integration — are delays applied consistently?
- Error handling on stale elements
- Return values — do callers check them?

- [ ] **Step 3: Read humanize.py line by line**

Check:
- Delay ranges (are they realistic?)
- Integration points with actions.py
- Any dead code

- [ ] **Step 4: Fix all issues found**

- [ ] **Step 5: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add fantoma/browser/engine.py fantoma/browser/actions.py fantoma/browser/humanize.py
git commit -m "audit: phase 3 — browser engine, actions, humanize reviewed and cleaned"
```

---

### Task 4: Phase 4 — Form Handling (1,085 lines)

**Files:**
- Audit: `fantoma/browser/form_login.py` (770), `fantoma/browser/form_assist.py` (157), `fantoma/browser/form_memory.py` (158)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read form_login.py line by line**

This is the biggest file. Check:
- `login()` function — parameter types, what it receives from agent.py
- `_classify_fields()` — ARIA vs raw DOM ordering (bug #2 from Session 5 — was it fully fixed?)
- `_detect_verification_page()` — signal phrases complete?
- CAPTCHA integration at line ~309 — config passed correctly?
- Submit button matching — OAuth button exclusion working?
- Field filling loop — double-fill prevention working?
- Return value shape — does agent.py consume it correctly?
- Flag if file should be split (770 lines is large)

- [ ] **Step 2: Read form_assist.py line by line**

Check:
- What does this provide that form_login.py doesn't?
- Is it used? Grep for imports.
- Integration points

- [ ] **Step 3: Read form_memory.py line by line**

Check:
- SQLite operations — are connections closed properly?
- Schema matches what form_login writes/reads
- Thread safety (if relevant)

- [ ] **Step 4: Fix all issues found**

- [ ] **Step 5: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add fantoma/browser/form_login.py fantoma/browser/form_assist.py fantoma/browser/form_memory.py
git commit -m "audit: phase 4 — form_login, form_assist, form_memory reviewed and cleaned"
```

---

### Task 5: Phase 5 — Post-Login Flows (622 lines)

**Files:**
- Audit: `fantoma/browser/consent.py` (202), `fantoma/browser/email_verify.py` (196), `fantoma/browser/verification.py` (224)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read consent.py line by line**

Check:
- Cookie consent detection — selectors up to date?
- Dismissal strategies (Escape, button click, force-hide)
- Integration with executor (when is it called?)
- False positive risk (detecting non-consent overlays)

- [ ] **Step 2: Read email_verify.py line by line**

Check:
- IMAP connection — security modes (ssl/starttls/none) handled correctly?
- Email matching — domain extraction, date filtering, code vs link detection
- The fixes from Session 5 (subdomain stripping, colour code filtering) — are they solid?
- Connection cleanup (IMAP logout on error)
- Timeout handling

- [ ] **Step 3: Read verification.py line by line**

Check:
- What is this file vs email_verify.py? Overlap?
- Integration points
- Dead code

- [ ] **Step 4: Fix all issues found**

- [ ] **Step 5: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add fantoma/browser/consent.py fantoma/browser/email_verify.py fantoma/browser/verification.py
git commit -m "audit: phase 5 — consent, email_verify, verification reviewed and cleaned"
```

---

### Task 6: Phase 6 — CAPTCHA (935 lines)

**Files:**
- Audit: `fantoma/captcha/detector.py` (103), `fantoma/captcha/sitekey.py` (206), `fantoma/captcha/orchestrator.py` (260), `fantoma/captcha/api_solver.py` (108), `fantoma/captcha/pow_solver.py` (38), `fantoma/captcha/human_solver.py` (66), `fantoma/captcha/telegram_solver.py` (154)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read all captcha files**

These were patched earlier today. Verify the fixes are clean and check:
- detector.py — the data-sitekey fallback and _resolve_sitekey_type we just added
- sitekey.py — the retry loop we just added, backoff timing
- orchestrator.py — tier 1/2/3 flow, inject_token correctness
- api_solver.py — CapSolver task types match their API docs
- pow_solver.py — ALTCHA logic
- human_solver.py + telegram_solver.py — webhook/Telegram integration, dead code

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_captcha.py tests/test_captcha_edge_cases.py -q --tb=short
```

Then full suite:
```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add fantoma/captcha/
git commit -m "audit: phase 6 — captcha subsystem reviewed and cleaned"
```

---

### Task 7: Phase 7 — DOM (1,124 lines)

**Files:**
- Audit: `fantoma/dom/extractor.py` (505), `fantoma/dom/accessibility.py` (362), `fantoma/dom/diff.py` (106), `fantoma/dom/selectors.py` (151)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read all DOM files**

Check:
- extractor.py — ARIA tree parsing, raw DOM fallback, element identification
- accessibility.py — how ARIA data is structured and returned
- diff.py — DOM diffing between steps (used by executor?)
- selectors.py — CSS selector generation, element lookup
- Type consistency between what DOM returns and what form_login/executor consume

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_dom_extractor.py tests/ -q --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add fantoma/dom/
git commit -m "audit: phase 7 — DOM subsystem reviewed and cleaned"
```

---

### Task 8: Phase 8 — LLM (383 lines)

**Files:**
- Audit: `fantoma/llm/client.py` (205), `fantoma/llm/prompts.py` (91), `fantoma/llm/vision.py` (87)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read all LLM files**

Check:
- client.py — API call construction, escalation chain logic, timeout handling, response parsing
- prompts.py — system/user prompt templates, FIELD_LABELLER prompt
- vision.py — screenshot encoding, multimodal message construction
- Are prompts consistent with how executor.py and form_login.py use them?

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add fantoma/llm/
git commit -m "audit: phase 8 — LLM subsystem reviewed and cleaned"
```

---

### Task 9: Phase 9 — Resilience (171 lines)

**Files:**
- Audit: `fantoma/resilience/escalation.py` (61), `fantoma/resilience/checkpoint.py` (55), `fantoma/resilience/memory.py` (55)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read all resilience files**

Check:
- escalation.py — model escalation vs environment escalation, the scroll loop fix from Session 5
- checkpoint.py — state saving/restoring
- memory.py — action history, loop detection
- Integration with executor.py — are these called correctly?

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_resilience.py tests/test_smart_retry.py tests/ -q --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add fantoma/resilience/
git commit -m "audit: phase 9 — resilience subsystem reviewed and cleaned"
```

---

### Task 10: Phase 10 — Browser Stealth (531 lines)

**Files:**
- Audit: `fantoma/browser/stealth.py` (130), `fantoma/browser/fingerprint.py` (224), `fantoma/browser/proxy.py` (177)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read all stealth files**

Check:
- stealth.py — JS patches, what gets injected into the browser
- fingerprint.py — self-test logic, the 7 checks mentioned in PROGRESS.md
- proxy.py — proxy configuration, rotation logic
- Integration with engine.py — applied at the right time?

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/test_fingerprint.py tests/ -q --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add fantoma/browser/stealth.py fantoma/browser/fingerprint.py fantoma/browser/proxy.py
git commit -m "audit: phase 10 — stealth, fingerprint, proxy reviewed and cleaned"
```

---

### Task 11: Phase 11 — CLI (1,089 lines)

**Files:**
- Audit: `fantoma/cli.py` (1089)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read cli.py line by line**

Check:
- Command definitions match Agent API (are all agent features exposed?)
- Argument parsing — types, defaults, help text
- Config construction from CLI args — does it build FantomaConfig correctly?
- Output formatting
- Dead commands or unreachable code paths
- Flag if file should be split (1089 lines is large)

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

- [ ] **Step 4: Commit**

```bash
git add fantoma/cli.py
git commit -m "audit: phase 11 — CLI reviewed and cleaned"
```

---

### Task 12: Phase 12 — Tests (3,497 lines)

**Files:**
- Audit all `tests/*.py` files (18 files)
- Test: `tests/` (full suite)

- [ ] **Step 1: Read each test file**

Check:
- Broken fixtures (live_reddit_test.py sub_name — known)
- Tests returning values instead of asserting (real_site_test.py warnings — known)
- Mock quality — are mocks realistic? Do they match current API?
- Coverage gaps — any major code paths without tests?
- Stale tests testing removed features
- test_extract_quotes failure — is it a test bug or a code bug? (Known: agent.extract returns list)

- [ ] **Step 2: Fix broken tests**

Fix:
- live_reddit_test.py fixture
- real_site_test.py return warnings
- test_extract_quotes type mismatch
- Any stale mocks

- [ ] **Step 3: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

Expected: 168+ passed, 0 failed, 0 errors (all three known issues fixed).

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "audit: phase 12 — test suite reviewed, broken tests fixed"
```

---

### Task 13: Phase 13 — Tools, Scripts, Examples (2,601 + 133 + 889 lines)

**Files:**
- Audit: `tools/*.py` (5 files), `examples/*.py` (7 files), root scripts: `hn_post.py`, `stress_test.py`, `stress_audit.py`, `weekly_monitor.py`
- Test: `tests/` (full suite)

- [ ] **Step 1: Read tools/ files**

Check:
- confidence_test.py, live_test.py, overnight_test.py, single_signup_test.py — do they use current Agent API?
- x_probe.py — still functional?
- Hardcoded credentials — should use config files instead?
- Dead/unused scripts

- [ ] **Step 2: Read root scripts**

Check:
- weekly_monitor.py — matches current agent/executor API?
- stress_test.py, stress_audit.py — still compatible?
- hn_post.py — still functional?

- [ ] **Step 3: Read examples/**

Check:
- Do examples match current Agent API and README?
- Any examples referencing removed features?

- [ ] **Step 4: Fix all issues found**

- [ ] **Step 5: Run tests**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -q --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add tools/ examples/ hn_post.py stress_test.py stress_audit.py weekly_monitor.py
git commit -m "audit: phase 13 — tools, scripts, examples reviewed and cleaned"
```

---

### Task 14: Final Summary

- [ ] **Step 1: Update PROGRESS.md**

Add a new section documenting all audit changes and any follow-up items flagged.

- [ ] **Step 2: Run final test suite**

```bash
cd /home/workspace/workbench/fantoma && python3 -m pytest tests/ -v --tb=short
```

Capture full output. Confirm all tests pass.

- [ ] **Step 3: Final commit**

```bash
git add PROGRESS.md
git commit -m "audit: complete — 13-phase review of all 48 Python files"
```
