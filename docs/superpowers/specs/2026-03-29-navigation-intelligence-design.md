# Fantoma v0.6.0 — Navigation Intelligence

## Problem

Fantoma v0.5 can fill forms, persist sessions, and batch actions — but the reactive LLM loop is naive about what happens after each action. It doesn't check for errors, doesn't tell the LLM what changed, and sends irrelevant elements. Estimated WebVoyager score: ~60%. Target: 80%+.

## Philosophy Constraints

- No screenshots or vision. DOM and accessibility tree only.
- One small local LLM (7-9B). Can't run two models. Context windows 32-49K.
- Code does the work. LLM advises only when code can't handle it.
- No heavy infrastructure. SQLite is fine. PostgreSQL is not.
- Every feature must work without increasing LLM calls. Prefer reducing them.

## What This Solves

Seven code-only techniques that make the reactive loop smarter:

1. **Action verification** — check what actually happened after every action
2. **Inline error detection** — find and report error messages to the LLM
3. **Smart element pruning** — show only task-relevant elements, not the first N
4. **Observation masking** — drop old DOM snapshots, keep action history (replaces v0.5 compaction)
5. **MutationObserver tracking** — precise feedback on what each action changed
6. **Tree diffing** — mark new elements so LLM sees what appeared
7. **Script caching** — replay successful action sequences without LLM

---

## Feature 1: Action Verification

### What

After every CLICK or form submit, run a code-only check and report the outcome to the LLM.

### How

New function `verify_action()` in `fantoma/browser/page_state.py`. Returns an `ActionOutcome` dict:

```python
{
    "url_changed": bool,         # URL different from before action
    "error_found": str | None,   # Error message text, or None
    "new_elements": int,         # Count of new interactive elements
    "dom_changed": bool,         # DOM hash different from before
}
```

Called from `executor.py` after every action in the reactive loop. The outcome gets appended to the LLM prompt for the next step:

Instead of: `Failed (don't repeat): CLICK [5]`
Now: `After CLICK [5]: error appeared — "Invalid email address"`

Or: `After CLICK [7]: URL changed to /dashboard, 12 new elements appeared`

### What Changes

- **Create:** `fantoma/browser/page_state.py` — `verify_action(page, pre_url, pre_dom_hash, dom_extractor) -> dict`
- **Modify:** `fantoma/executor.py` — call `verify_action()` after each action, include outcome in next LLM prompt

---

## Feature 2: Inline Error Detection

### What

Scan the page for error signals after form submissions. Pure JS, no LLM.

### How

New function `detect_errors()` in `fantoma/browser/page_state.py`. Runs JS on the page:

1. Query elements with `role="alert"` or `aria-live="assertive"`
2. Query elements with classes containing: error, invalid, warning, danger, alert-danger, form-error
3. Query elements with `aria-invalid="true"`
4. Query elements referenced by `aria-describedby` or `aria-errormessage` on invalid inputs
5. Check visible text for patterns: "invalid", "incorrect", "failed", "try again", "required field", "already exists", "too short", "doesn't match", "not found"

Returns a list of error strings found (max 3, to keep prompt short). Empty list if no errors.

### Integration Points

- Called by `verify_action()` as part of the post-action check
- Also called by `agent.login()` after form submit — errors included in the AgentResult
- Also called by `form_login.py` — if errors detected after submit, return them in the result dict so `login()` knows WHY it failed, not just that it failed

### What Changes

- **Create:** `fantoma/browser/page_state.py` — `detect_errors(page) -> list[str]`
- **Modify:** `fantoma/executor.py` — use error info in action outcomes
- **Modify:** `fantoma/browser/form_login.py` — check for errors after submit, include in result
- **Modify:** `fantoma/agent.py` — include error messages in AgentResult.error

---

## Feature 3: Smart Element Pruning

### What

Replace the hard cap (first 15 elements) with relevance-based scoring. The LLM sees the 15 most relevant elements for the current task.

### How

New function `prune_elements()` in `fantoma/dom/accessibility.py`.

Scoring algorithm (all code, no LLM):

1. Extract keywords from the task string. Stop words removed. Lowercase.
2. For each element, compute a relevance score:
   - **+3** if element name contains a task keyword (exact word match)
   - **+2** if element role is textbox/combobox/searchbox (form inputs are usually relevant)
   - **+2** if element name matches a submit pattern ("next", "continue", "sign in", "submit", "login", "search")
   - **+1** if element role is checkbox or radio (form elements)
   - **-2** if element name matches navigation noise ("menu", "settings", "notifications", "cookie", "close")
   - **+0** baseline for everything else (links, generic buttons)
3. Sort by score descending. Take top N (default 15, from config).
4. Re-index from 0 so the LLM sees [0] through [14].

Elements not shown to the LLM are still tracked internally — if the LLM somehow references an index beyond the shown range (shouldn't happen, but safety), `get_element_by_index` can still find it.

### Task Keyword Passing

The task string needs to reach the DOM extractor. Currently `AccessibilityExtractor.extract()` only takes `page`. Add an optional `task` parameter:

```python
def extract(self, page, task: str = "") -> str:
```

`executor.py` already has the task string — pass it through.

### What Changes

- **Modify:** `fantoma/dom/accessibility.py` — add `prune_elements(elements, task, max_elements)`, update `extract()` signature to accept task
- **Modify:** `fantoma/executor.py` — pass task string to `dom.extract(page, task=task)`

---

## Feature 4: Observation Masking

### What

Replace LLM-based history compaction with simpler observation masking. Keep action history, drop old DOM snapshots. Zero LLM calls.

### How

The prompt sent to the LLM currently includes compacted memory (via an LLM summarization call). Replace with:

- **Action history:** kept verbatim, capped at 50 entries. Format: `"Step 1: CLICK [3] → URL changed to /login"`. Includes the action outcome from Feature 1.
- **DOM snapshot:** only the most recent one. All previous snapshots discarded.
- **LLM compaction becomes fallback, not default.** The `_compact_history()` method stays but is only called when action history exceeds 40% of estimated context window (measured at ~2 chars/token). For most tasks (under 50 steps), no LLM call is ever made. `COMPACTION_SYSTEM` prompt stays.

When history exceeds 50 entries, oldest entries drop off the front. Simple list truncation.

Note: observation masking was validated on coding tasks (JetBrains, SWE-bench), not web navigation specifically. The principle transfers but keeping LLM compaction as fallback is prudent.

### What Changes

- **Modify:** `fantoma/executor.py` — change default from compaction-first to masking-first. Keep `_compact_history()` as fallback when history is too long for context window. Cap action history at 50 entries. Update prompt construction to include action outcomes.
- **No changes to** `fantoma/llm/prompts.py` COMPACTION_SYSTEM — kept as fallback.

---

## Feature 5: MutationObserver Change Tracking

### What

Inject a MutationObserver before each action, collect mutations after. Report what changed precisely.

### How

New file `fantoma/browser/observer.py` with two functions:

```python
def inject_observer(page) -> None:
    """Inject a MutationObserver that records changes. Call BEFORE the action."""

def collect_mutations(page) -> dict:
    """Collect recorded mutations. Call AFTER the action. Returns summary."""
```

The observer is injected via `page.evaluate()`. It stores mutations in a `window.__fantoma_mutations` array. After the action, `collect_mutations()` reads and clears that array.

Returns:
```python
{
    "added": ["div.error-message", "span.alert"],    # tag.class of added nodes
    "removed": ["div.loading-spinner"],               # tag.class of removed nodes
    "changed_attrs": [{"element": "input#email", "attr": "aria-invalid", "value": "true"}],
    "text_changes": ["Error: Invalid email address"], # new visible text content
}
```

This summary is compact (a few lines) and goes into the action outcome that the LLM sees.

### Limitations

- MutationObserver only fires for mutations in the same document. If the page navigates (full page load), mutations are lost — but that's OK because we detect navigation via URL change.
- Shadow DOM mutations need separate observers per shadow root. For v0.6 we skip shadow DOM mutations — they're rare in auth flows.
- The observer is disconnected after collection to avoid memory leaks.
- **Camoufox isolates `page.evaluate()` in a separate world by default.** The observer must run in the main world to see DOM mutations from page scripts. Fix: pass `main_world_eval=True` to the Camoufox constructor in `engine.py`. This is a one-line change.
- For async DOM changes (network responses, timers), mutations may not be collected immediately. Mitigation: wait for network idle after the action before collecting. Fantoma already does this with `wait_for_network_idle()`.

### What Changes

- **Create:** `fantoma/browser/observer.py` — `inject_observer()`, `collect_mutations()`
- **Modify:** `fantoma/executor.py` — inject before action, collect after, include in action outcome

---

## Feature 6: Tree Diffing for Dynamic Content

### What

When new elements appear after an action (dropdown, modal, next form step), mark them in the LLM prompt.

### How

`AccessibilityExtractor` already caches `_last_interactive` (the previous step's elements). After extracting the current elements:

1. Compare current elements with `_last_interactive` by (role, name) tuple
2. Elements in current but not in previous are "new"
3. Mark new elements with `*` prefix in the output:

```
Elements (5 of 12):
[0] button "Submit"
*[1] textbox "Verification code"
*[2] button "Verify"
[3] link "Resend code"
[4] link "Back to login"
```

The LLM immediately focuses on new elements — they're the ones that just appeared because of its last action.

### What Changes

- **Modify:** `fantoma/dom/accessibility.py` — in `extract()`, compare current elements with `_last_interactive` before overwriting. Mark new ones. Update `_parse_interactive_from_output` to handle `*` prefix.
- **Modify:** `fantoma/dom/accessibility.py` — in `extract_aria()`, accept a `previous_elements` parameter for diff comparison.

---

## Feature 7: Script Caching for Repeat Visits

### What

After a successful `agent.run()`, save the action sequence. Next time, replay without LLM. Fall back to LLM if replay fails.

### How

New class `ScriptCache` in `fantoma/resilience/script_cache.py`. SQLite storage at `~/.local/share/fantoma/script_cache.db`.

**Cache key:** Domain + sorted list of (role, name) tuples from the initial page's interactive elements. Matching uses fuzzy overlap (>80% of elements match) rather than exact hash — sites change minor elements between visits (ads, recommendations, A/B tests). The lookup finds the cached entry with the highest overlap above the threshold.

**Cache value:** ordered list of actions with expected outcomes:
```python
[
    {"action": "CLICK [3]", "expected_url_pattern": "/login", "expected_elements": ["textbox:Email", "textbox:Password"]},
    {"action": 'TYPE [0] "<credentials>"', "expected_url_pattern": None},
    ...
]
```

Credentials are stored as `<secret:KEY>` placeholders (same format as v0.5 sensitive data). At replay time, if `sensitive_data` is set on the Agent, placeholders are replaced with real values. If no sensitive_data, the cache entry is skipped (can't replay without credentials). Task-string credentials (parsed via regex in `_try_code_form_fill`) are not cached — only sensitive_data secrets are safe to reference as placeholders.

**Replay logic:**
1. At the start of `execute_reactive()`, check cache for current page structure
2. If cache hit, execute actions one by one
3. After each action, verify the outcome matches expectations (URL pattern, expected element roles)
4. If any step fails verification: abort replay, fall back to normal LLM-driven execution from current state
5. If all steps succeed: return result without any LLM calls

**Cache write:**
1. At the end of a successful `execute_reactive()`, save the action sequence
2. Only cache if: task succeeded, took fewer than 20 steps, no escalations needed

### What Changes

- **Create:** `fantoma/resilience/script_cache.py` — `ScriptCache` class with `lookup()`, `save()`, `replay()`
- **Modify:** `fantoma/executor.py` — check cache at start of `execute_reactive()`, save on success

---

## Files Changed (Complete)

| File | Change |
|------|--------|
| `fantoma/browser/page_state.py` | **New** — `verify_action()`, `detect_errors()` |
| `fantoma/browser/observer.py` | **New** — `inject_observer()`, `collect_mutations()` |
| `fantoma/resilience/script_cache.py` | **New** — `ScriptCache` class (SQLite) |
| `fantoma/dom/accessibility.py` | Smart pruning, tree diffing, task parameter |
| `fantoma/executor.py` | Action verification loop, observation masking, MutationObserver integration, script cache check/save |
| `fantoma/browser/engine.py` | Add `main_world_eval=True` to Camoufox constructor |
| `fantoma/llm/prompts.py` | Update REACTIVE_SYSTEM with error/outcome format |
| `fantoma/browser/form_login.py` | Error detection after submit |
| `fantoma/agent.py` | Error messages in AgentResult |
| `pyproject.toml` | Version bump to 0.6.0 |
| `tests/test_page_state.py` | **New** — action verification + error detection tests |
| `tests/test_observer.py` | **New** — MutationObserver tests |
| `tests/test_pruning.py` | **New** — element pruning tests |
| `tests/test_tree_diff.py` | **New** — tree diffing tests |
| `tests/test_script_cache.py` | **New** — script cache tests |

## Dependencies

None new. SQLite is stdlib. All features are pure Python + Playwright JS evaluation.

## Out of Scope

- Screenshots or vision of any kind
- Second LLM model
- Embedding-based search
- Cloud services or external APIs
- Changes to `agent.session()` API
- Hierarchical sub-agents or task decomposition
- Look-ahead / world model simulation
