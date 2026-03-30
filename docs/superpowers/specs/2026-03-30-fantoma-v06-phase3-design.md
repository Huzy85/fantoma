# Fantoma v0.6 Phase 3 — Competitive Edge Features

**Date:** 2026-03-30
**Status:** Approved
**Branch:** `feat/v0.6-navigation-intelligence`

## Overview

Four code-only features that close gaps identified through competitive analysis against browser-use, Stagehand v3, Agent-E, and Skyvern 2.0. All features maintain Fantoma's core philosophy: code-first, LLM as brain not hands.

**Credits:**
- Agent-E (adaptive DOM distillation concept)
- Stagehand v3 (self-healing selector pattern)
- Skyvern 2.0 (planner-actor-validator loop idea)

## Build Order

Features 1 & 2 are independent and can be built in parallel. Feature 4 depends on neither. Feature 3 depends on Feature 4 (uses `assess_progress()` during cache replay validation).

```
Feature 1 (Adaptive DOM Modes) ─┐
                                 ├──► Feature 4 (Success Criteria) ──► Feature 3 (Cache + Self-Healing)
Feature 2 (Parent-Context Labels)┘
```

---

## Feature 1: Adaptive DOM Modes

**Inspired by:** Agent-E's `text_only` / `input_fields` / `all_fields` DOM extraction modes.

**Problem:** Every step sends the same DOM shape to the LLM regardless of what it's doing. A login form doesn't need 50 navigation links. A content extraction task doesn't need interactive elements numbered.

### Design

Add a `mode` parameter to `extract_aria()` with three values:

| Mode | Behaviour | Use case |
|------|-----------|----------|
| `navigate` | Current behaviour (default) | General browsing, clicking links |
| `form` | Inputs boosted to top, `max_elements=20`, `max_headings=5` | Login, search, checkout |
| `content` | Uses `extract_aria_content` path, no interactive numbering | Scraping, reading articles |

**Mode inference** happens in `executor.py` via a new `_infer_dom_mode(task, page)` function:

1. **Keyword matching on the task string:**
   - `form` keywords: login, sign in, register, checkout, search, fill, enter, submit, subscribe, signup, sign up
   - `content` keywords: extract, read, scrape, copy, get text, find information, summarize
   - Default: `navigate`

2. **Page state override:** If the page has 5+ textbox elements in the current ARIA snapshot, force `form` mode regardless of keywords. This catches forms the task description doesn't mention.

3. **Re-evaluated every step.** Mode can change mid-task (e.g., navigate to a page, then fill a form).

### Files

| File | Changes |
|------|---------|
| `fantoma/dom/accessibility.py` | Add `mode` parameter to `extract_aria()`. In `form` mode: sort interactive elements so inputs come first, apply tighter `max_elements` and `max_headings`. In `content` mode: delegate to existing content extraction path. |
| `fantoma/executor.py` | Add `_infer_dom_mode(task, page)`. Call it before each `extract_aria()` invocation, pass result as `mode`. |
| `tests/test_dom_modes.py` | New file. Test keyword matching, page state override, mode-specific output shapes. |

### Acceptance Criteria

- `extract_aria(snapshot, mode="form")` returns max 20 elements, inputs first
- `extract_aria(snapshot, mode="content")` returns text content without numbered interactive elements
- `_infer_dom_mode("login to the site", page)` returns `"form"`
- Page with 5+ textboxes overrides keyword-inferred mode to `"form"`
- Default mode is `"navigate"` (unchanged behaviour)

---

## Feature 2: Parent-Context Labels

**Problem:** The LLM sees a flat list of interactive elements with no sense of where they sit on the page. Two "Submit" buttons in different forms look identical.

### Design

Parse ARIA landmark roles from the snapshot output and group elements under their nearest landmark parent.

**Tracked landmarks:** `form`, `navigation`, `region`, `main`, `banner`, `contentinfo`, `complementary`, `search`.

**Implementation in `extract_aria()`:**

Track state during snapshot line parsing:
- `current_landmark: str | None` — the active landmark name (e.g., `"Login form"`, `"Main navigation"`)
- `landmark_indent: int` — indentation level of the current landmark line
- `element_groups: dict[str, list]` — maps landmark label to list of elements

When parsing a line:
1. If the line matches a landmark role pattern (e.g., `- form "Login"`), set `current_landmark` to `"{role}: {name}"` and record its indent level.
2. If the line's indent drops to or below `landmark_indent`, clear `current_landmark`.
3. Assign each interactive element to `current_landmark` (or `None` for elements outside any landmark).

**Output format:**

```
[Login form]
[1] textbox "Username"
[2] textbox "Password" (required)
[3] button "Sign In"

[Main navigation]
[4] link "Home"
[5] link "Dashboard"

[Other]
[6] button "Cookie consent"
```

Element indices remain globally sequential — no renumbering per group. Elements without a landmark parent go under `[Other]`.

### Files

| File | Changes |
|------|---------|
| `fantoma/dom/accessibility.py` | Add landmark tracking state variables. During interactive element collection, track which landmark each element belongs to. In output formatting, group elements under landmark headers. |
| `tests/test_landmarks.py` | New file. Test landmark detection, element grouping, `[Other]` fallback, index continuity. |

### Acceptance Criteria

- Elements inside a `form` landmark get grouped under `[Login form]` (or whatever the form's name is)
- Elements inside `navigation` get grouped under `[Main navigation]`
- Elements outside any landmark appear under `[Other]`
- Element indices are globally sequential (no gaps, no restarts)
- Landmarks without interactive elements inside them produce no group header

---

## Feature 3: Script Cache Wiring + Self-Healing Selectors

**Inspired by:** Stagehand v3's self-healing selector pattern using fuzzy matching.

**Problem:** `ScriptCache` exists in `fantoma/resilience/script_cache.py` but is not wired into the executor. Cached scripts also break when page elements change slightly between runs.

### Part 1: Wire ScriptCache into Executor

At the start of `execute_reactive()`:

1. Build an element key from the current ARIA snapshot (using `ScriptCache._make_key()` logic).
2. Call `cache.lookup(domain, element_key)`.
3. If a cached script is found, attempt replay via a new `_replay_cached(page, script, dom_extractor)` method.
4. If replay succeeds (all actions verified via `assess_progress()`), return the result without LLM calls.
5. If replay fails at any step, abandon cache and fall through to normal LLM-driven execution.
6. On successful LLM-driven task completion, save the action sequence to cache via `cache.save()`.

### Part 2: Self-Healing Selectors

When replaying a cached script, each action references an element by index. If the expected element at that index doesn't match the cached `target_role` and `target_name`:

1. **Extend cached action format** to include `target_role` and `target_name` fields (the role and name of the element the action targeted when originally recorded).
2. **On mismatch during replay:** scan all current interactive elements for fuzzy matches using `difflib.SequenceMatcher`:
   - Role must match exactly
   - Name similarity threshold: 0.7 (SequenceMatcher ratio)
   - If exactly one match found: rewrite the action's index to point to the matched element
   - If zero or multiple matches: healing fails, abandon cached script
3. **On successful heal:** save the repaired script back to cache with updated indices.

### Files

| File | Changes |
|------|---------|
| `fantoma/executor.py` | Import `ScriptCache`. Instantiate at `__init__`. Add cache lookup at start of `execute_reactive()`. Add `_replay_cached()` method. Save successful sequences on completion. |
| `fantoma/resilience/script_cache.py` | Extend action dict format to include `target_role`, `target_name`. Add `heal_action()` method that does fuzzy element matching. |
| `tests/test_script_cache.py` | Extend with tests for the new `target_role`/`target_name` fields and `heal_action()`. |
| `tests/test_cache_replay.py` | New file. Test full replay flow: cache hit, successful replay, failed replay fallback, self-healing on element shift. |

### Acceptance Criteria

- Cached script replays without LLM calls when elements match
- Failed replay falls through to normal LLM execution (no crash)
- Self-healing finds a renamed button (e.g., "Log In" → "Sign In") via fuzzy match
- Healing fails gracefully when multiple candidates match
- Successful heal updates the cached script
- `assess_progress()` validates each replayed action

---

## Feature 4: Per-Step Success Criteria

**Inspired by:** Skyvern 2.0's planner-actor-validator pattern.

**Problem:** Fantoma checks if the page changed after each action but doesn't assess whether the action actually achieved what was intended or whether the overall task is progressing.

### Design

New function `assess_progress(page, action, task, dom_extractor)` in `page_state.py`.

**Layer 1 — Action-level verification:**

| Action type | Check |
|-------------|-------|
| `TYPE` into a field | Read field value back, confirm it contains the typed text |
| `CLICK` a submit/login button | Check if form disappeared from DOM or URL changed |
| `CLICK` a link | Check if URL changed |
| `SELECT` an option | Read select value back, confirm it matches |
| Other actions | `action_ok = True` (no specific check) |

**Layer 2 — Task-level progress:**

A `_infer_task_intent(task)` function maps task descriptions to intent categories:

| Intent | Keywords | Progress signal |
|--------|----------|-----------------|
| `auth` | login, sign in, log in, authenticate | URL no longer contains login/signin path segments |
| `extract` | extract, scrape, read, copy, get | New text content appeared in page that wasn't there before |
| `navigate` | go to, visit, open, navigate | URL matches expected destination pattern |
| `None` | (no match) | `progress_ok = None` (skip layer 2) |

**Return value:**

```python
{
    "action_ok": bool,      # Did this specific action work?
    "progress_ok": bool | None,  # Is the overall task progressing? None = can't tell
    "reason": str            # Human-readable explanation
}
```

**Stall detection in executor:**

Track consecutive steps where `action_ok=True` but `progress_ok=False`. After 3 such steps, inject a warning into the LLM prompt: `"Warning: actions are succeeding but task isn't progressing. Try a different approach."` Counter resets when `progress_ok` becomes `True` or `None`.

### Files

| File | Changes |
|------|---------|
| `fantoma/browser/page_state.py` | Add `assess_progress()` function. Add `_infer_task_intent()` helper. Action-level checks use page evaluation (read field values, check URL, check DOM presence). |
| `fantoma/executor.py` | Call `assess_progress()` after each action in `execute_reactive()`. Track stall counter. Inject warning into LLM prompt at 3 consecutive stalls. |
| `tests/test_progress.py` | New file. Test action-level checks for each action type, task intent inference, stall detection trigger. |

### Acceptance Criteria

- `assess_progress()` returns `action_ok=True` when a TYPE action's value appears in the field
- `assess_progress()` returns `action_ok=False` when a TYPE action's value is missing from the field
- `_infer_task_intent("login to github")` returns `"auth"`
- Stall warning appears in LLM prompt after 3 consecutive action_ok=True + progress_ok=False
- `progress_ok=None` when task intent can't be inferred (no false positives)

---

## Scope Boundaries

**In scope:**
- The 4 features described above
- Unit tests for each feature
- Integration into existing executor flow

**Out of scope (YAGNI):**
- Visual/screenshot analysis
- Multi-tab coordination
- LLM-based DOM mode selection (keywords + page state is sufficient)
- Semantic similarity for self-healing (difflib is sufficient)
- Persistent progress tracking across sessions
- Custom user-defined success criteria
