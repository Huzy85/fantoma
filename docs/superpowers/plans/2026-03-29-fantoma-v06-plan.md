# Fantoma v0.6.0 — Navigation Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Fantoma's reactive loop smarter with seven code-only techniques: action verification, error detection, smart pruning, observation masking, MutationObserver tracking, tree diffing, and script caching. Target: 80%+ WebVoyager score.

**Architecture:** Seven features built in dependency order. Features 1-2 (page_state.py) first — they provide the verification and error detection primitives. Features 3 and 6 (accessibility.py changes) can be done together. Feature 4 (observation masking) modifies executor.py's prompt construction. Feature 5 (observer.py) is independent. Feature 7 (script_cache.py) depends on features 1-2 for verification logic. Final integration wires everything into executor.py.

**Tech Stack:** Python 3.10+, Playwright, Camoufox, SQLite (stdlib), pytest

**Spec:** `docs/superpowers/specs/2026-03-29-navigation-intelligence-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `fantoma/browser/page_state.py` | **New** — `verify_action()` and `detect_errors()` |
| `fantoma/browser/observer.py` | **New** — MutationObserver inject/collect |
| `fantoma/resilience/script_cache.py` | **New** — SQLite action sequence cache |
| `fantoma/dom/accessibility.py` | Smart pruning + tree diffing |
| `fantoma/executor.py` | Wire all features into the reactive loop |
| `fantoma/browser/engine.py` | One-line: `main_world_eval=True` |
| `fantoma/llm/prompts.py` | Update REACTIVE_SYSTEM with outcome format |
| `fantoma/browser/form_login.py` | Error detection after submit |
| `fantoma/agent.py` | Error messages in AgentResult |
| `pyproject.toml` | Version bump to 0.6.0 |
| `tests/test_page_state.py` | **New** — verification + error detection |
| `tests/test_observer.py` | **New** — MutationObserver |
| `tests/test_pruning.py` | **New** — element pruning |
| `tests/test_tree_diff.py` | **New** — tree diffing |
| `tests/test_script_cache.py` | **New** — script cache |

---

### Task 1: Action Verification & Error Detection — `fantoma/browser/page_state.py`

**Files:**
- Create: `fantoma/browser/page_state.py`
- Create: `tests/test_page_state.py`

- [ ] **Step 1: Write failing tests for page_state**

Create `tests/test_page_state.py`:

```python
"""Tests for action verification and inline error detection."""
import pytest
from unittest.mock import MagicMock, PropertyMock


def _make_page(url="https://example.com/login", inner_text="Welcome", elements=None):
    """Create a mock Playwright page."""
    page = MagicMock()
    type(page).url = PropertyMock(return_value=url)
    page.inner_text.return_value = inner_text
    page.evaluate.return_value = []
    page.query_selector_all.return_value = []
    return page


def _make_dom_extractor(elements=None):
    """Create a mock AccessibilityExtractor."""
    ext = MagicMock()
    ext._last_interactive = elements or []
    return ext


class TestVerifyAction:
    def test_url_changed(self):
        from fantoma.browser.page_state import verify_action
        page = _make_page(url="https://example.com/dashboard")
        result = verify_action(page, pre_url="https://example.com/login", pre_dom_hash="abc", dom_extractor=_make_dom_extractor())
        assert result["url_changed"] is True

    def test_url_unchanged(self):
        from fantoma.browser.page_state import verify_action
        page = _make_page(url="https://example.com/login")
        result = verify_action(page, pre_url="https://example.com/login", pre_dom_hash="abc", dom_extractor=_make_dom_extractor())
        assert result["url_changed"] is False

    def test_dom_changed(self):
        from fantoma.browser.page_state import verify_action
        page = _make_page(url="https://example.com/login")
        ext = _make_dom_extractor(elements=[{"role": "textbox", "name": "Code"}])
        result = verify_action(page, pre_url="https://example.com/login", pre_dom_hash="abc", dom_extractor=ext)
        # DOM hash will differ from "abc" because new elements exist
        assert result["new_elements"] == 1

    def test_error_found_in_page(self):
        from fantoma.browser.page_state import verify_action
        page = _make_page(url="https://example.com/login")
        # Simulate error detection returning errors
        page.evaluate.return_value = ["Invalid email address"]
        result = verify_action(page, pre_url="https://example.com/login", pre_dom_hash="abc", dom_extractor=_make_dom_extractor())
        assert result["error_found"] is not None
        assert "Invalid email" in result["error_found"]


class TestDetectErrors:
    def test_no_errors(self):
        from fantoma.browser.page_state import detect_errors
        page = _make_page()
        page.evaluate.return_value = []
        result = detect_errors(page)
        assert result == []

    def test_returns_error_strings(self):
        from fantoma.browser.page_state import detect_errors
        page = _make_page()
        page.evaluate.return_value = ["Invalid email address", "Password too short"]
        result = detect_errors(page)
        assert len(result) == 2
        assert "Invalid email address" in result

    def test_max_three_errors(self):
        from fantoma.browser.page_state import detect_errors
        page = _make_page()
        page.evaluate.return_value = ["Error 1", "Error 2", "Error 3", "Error 4", "Error 5"]
        result = detect_errors(page)
        assert len(result) == 3

    def test_handles_evaluate_failure(self):
        from fantoma.browser.page_state import detect_errors
        page = _make_page()
        page.evaluate.side_effect = Exception("Page crashed")
        result = detect_errors(page)
        assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_page_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fantoma.browser.page_state'`

- [ ] **Step 3: Implement page_state.py**

Create `fantoma/browser/page_state.py`:

```python
"""Post-action verification and inline error detection.

After every browser action, checks what actually happened:
- Did the URL change?
- Did the DOM change?
- Are there error messages on the page?
- How many new interactive elements appeared?

All checks are code-only — no LLM calls.
"""
import hashlib
import logging

log = logging.getLogger("fantoma.page_state")

# JS that scans the page for visible error messages.
# Checks: role="alert", aria-live="assertive", error/invalid CSS classes,
# aria-invalid="true", and common error text patterns.
_ERROR_DETECTION_JS = """() => {
    const errors = new Set();
    const MAX = 3;

    // 1. role="alert" or aria-live="assertive"
    const alerts = document.querySelectorAll('[role="alert"], [aria-live="assertive"]');
    for (const el of alerts) {
        const text = el.textContent?.trim();
        if (text && text.length < 200 && el.offsetParent !== null) {
            errors.add(text);
            if (errors.size >= MAX) return [...errors];
        }
    }

    // 2. Error CSS classes
    const errorClasses = document.querySelectorAll(
        '.error, .invalid, .warning, .danger, .alert-danger, .form-error, ' +
        '.field-error, .input-error, .validation-error, .error-message'
    );
    for (const el of errorClasses) {
        const text = el.textContent?.trim();
        if (text && text.length < 200 && text.length > 3 && el.offsetParent !== null) {
            errors.add(text);
            if (errors.size >= MAX) return [...errors];
        }
    }

    // 3. aria-invalid="true" — find their error descriptions
    const invalidInputs = document.querySelectorAll('[aria-invalid="true"]');
    for (const input of invalidInputs) {
        const describedBy = input.getAttribute('aria-describedby') || input.getAttribute('aria-errormessage');
        if (describedBy) {
            for (const id of describedBy.split(/\\s+/)) {
                const desc = document.getElementById(id);
                if (desc) {
                    const text = desc.textContent?.trim();
                    if (text && text.length < 200) {
                        errors.add(text);
                        if (errors.size >= MAX) return [...errors];
                    }
                }
            }
        }
    }

    // 4. Visible text pattern matching (last resort)
    const patterns = /invalid|incorrect|failed|try again|required field|already exists|too short|doesn't match|not found|wrong password|wrong email/i;
    const candidates = document.querySelectorAll('p, span, div, li, label');
    for (const el of candidates) {
        if (el.children.length > 2) continue;  // skip containers
        const text = el.textContent?.trim();
        if (text && text.length > 5 && text.length < 150 && el.offsetParent !== null) {
            if (patterns.test(text)) {
                errors.add(text);
                if (errors.size >= MAX) return [...errors];
            }
        }
    }

    return [...errors];
}"""


def detect_errors(page) -> list[str]:
    """Scan the page for visible error messages. Returns up to 3 error strings."""
    try:
        errors = page.evaluate(_ERROR_DETECTION_JS)
        if isinstance(errors, list):
            return errors[:3]
        return []
    except Exception as e:
        log.debug("Error detection failed: %s", e)
        return []


def verify_action(page, pre_url: str, pre_dom_hash: str, dom_extractor) -> dict:
    """Check what happened after a browser action.

    Args:
        page: Playwright page object
        pre_url: URL before the action
        pre_dom_hash: DOM hash before the action
        dom_extractor: AccessibilityExtractor (for element count)

    Returns:
        ActionOutcome dict with url_changed, error_found, new_elements, dom_changed
    """
    current_url = page.url
    url_changed = current_url != pre_url

    # Count new interactive elements
    new_elements = len(dom_extractor._last_interactive)

    # Check for errors
    errors = detect_errors(page)
    error_found = errors[0] if errors else None

    # Compute current DOM hash
    try:
        body_text = page.inner_text("body")[:2000]
        current_hash = hashlib.md5(body_text.encode()).hexdigest()[:12]
    except Exception:
        current_hash = "unknown"

    dom_changed = current_hash != pre_dom_hash

    return {
        "url_changed": url_changed,
        "error_found": error_found,
        "new_elements": new_elements,
        "dom_changed": dom_changed,
    }


def dom_hash(page) -> str:
    """Compute a short hash of the page body text for change detection."""
    try:
        body_text = page.inner_text("body")[:2000]
        return hashlib.md5(body_text.encode()).hexdigest()[:12]
    except Exception:
        return "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_page_state.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/page_state.py tests/test_page_state.py
git commit -m "feat: action verification and error detection (page_state.py)"
```

---

### Task 2: MutationObserver Change Tracking — `fantoma/browser/observer.py`

**Files:**
- Create: `fantoma/browser/observer.py`
- Create: `tests/test_observer.py`

- [ ] **Step 1: Write failing tests for observer**

Create `tests/test_observer.py`:

```python
"""Tests for MutationObserver injection and mutation collection."""
import pytest
from unittest.mock import MagicMock, call


class TestInjectObserver:
    def test_calls_evaluate(self):
        from fantoma.browser.observer import inject_observer
        page = MagicMock()
        inject_observer(page)
        page.evaluate.assert_called_once()
        # The JS should set up window.__fantoma_mutations
        js_code = page.evaluate.call_args[0][0]
        assert "__fantoma_mutations" in js_code

    def test_handles_evaluate_failure(self):
        from fantoma.browser.observer import inject_observer
        page = MagicMock()
        page.evaluate.side_effect = Exception("Detached")
        # Should not raise
        inject_observer(page)


class TestCollectMutations:
    def test_returns_empty_when_no_mutations(self):
        from fantoma.browser.observer import collect_mutations
        page = MagicMock()
        page.evaluate.return_value = {"added": [], "removed": [], "changed_attrs": [], "text_changes": []}
        result = collect_mutations(page)
        assert result["added"] == []
        assert result["text_changes"] == []

    def test_returns_mutation_summary(self):
        from fantoma.browser.observer import collect_mutations
        page = MagicMock()
        page.evaluate.return_value = {
            "added": ["div.error-message", "span.alert"],
            "removed": ["div.loading-spinner"],
            "changed_attrs": [{"element": "input#email", "attr": "aria-invalid", "value": "true"}],
            "text_changes": ["Error: Invalid email"],
        }
        result = collect_mutations(page)
        assert len(result["added"]) == 2
        assert "div.error-message" in result["added"]
        assert len(result["text_changes"]) == 1

    def test_handles_evaluate_failure(self):
        from fantoma.browser.observer import collect_mutations
        page = MagicMock()
        page.evaluate.side_effect = Exception("Page navigated")
        result = collect_mutations(page)
        assert result["added"] == []
        assert result["removed"] == []

    def test_caps_results(self):
        from fantoma.browser.observer import collect_mutations
        page = MagicMock()
        page.evaluate.return_value = {
            "added": [f"div.item-{i}" for i in range(50)],
            "removed": [],
            "changed_attrs": [],
            "text_changes": [f"Text {i}" for i in range(20)],
        }
        result = collect_mutations(page)
        assert len(result["added"]) <= 10
        assert len(result["text_changes"]) <= 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_observer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fantoma.browser.observer'`

- [ ] **Step 3: Implement observer.py**

Create `fantoma/browser/observer.py`:

```python
"""MutationObserver injection for precise DOM change tracking.

Injects a MutationObserver before each action, collects mutations after.
Reports what changed: added nodes, removed nodes, changed attributes, new text.

Limitations:
- Only fires in the same document (full navigations lose mutations — OK, we detect those via URL).
- Shadow DOM mutations skipped for v0.6.
- Observer disconnected after collection to prevent memory leaks.
"""
import logging

log = logging.getLogger("fantoma.observer")

_INJECT_JS = """() => {
    // Disconnect any previous observer
    if (window.__fantoma_observer) {
        window.__fantoma_observer.disconnect();
    }
    window.__fantoma_mutations = [];

    const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
            if (m.type === 'childList') {
                for (const node of m.addedNodes) {
                    if (node.nodeType === 1) {
                        const tag = node.tagName.toLowerCase();
                        const cls = node.className && typeof node.className === 'string'
                            ? '.' + node.className.split(/\\s+/)[0] : '';
                        window.__fantoma_mutations.push({type: 'added', value: tag + cls});
                    } else if (node.nodeType === 3 && node.textContent.trim()) {
                        window.__fantoma_mutations.push({type: 'text', value: node.textContent.trim().slice(0, 100)});
                    }
                }
                for (const node of m.removedNodes) {
                    if (node.nodeType === 1) {
                        const tag = node.tagName.toLowerCase();
                        const cls = node.className && typeof node.className === 'string'
                            ? '.' + node.className.split(/\\s+/)[0] : '';
                        window.__fantoma_mutations.push({type: 'removed', value: tag + cls});
                    }
                }
            } else if (m.type === 'attributes') {
                const el = m.target;
                const tag = el.tagName.toLowerCase();
                const id = el.id ? '#' + el.id : '';
                window.__fantoma_mutations.push({
                    type: 'attr',
                    element: tag + id,
                    attr: m.attributeName,
                    value: el.getAttribute(m.attributeName) || ''
                });
            }
        }
    });

    observer.observe(document.body, {
        childList: true,
        attributes: true,
        subtree: true,
        attributeFilter: ['aria-invalid', 'aria-hidden', 'class', 'disabled', 'hidden', 'style']
    });

    window.__fantoma_observer = observer;
}"""

_COLLECT_JS = """() => {
    if (window.__fantoma_observer) {
        window.__fantoma_observer.disconnect();
        window.__fantoma_observer = null;
    }

    const raw = window.__fantoma_mutations || [];
    window.__fantoma_mutations = [];

    const added = [];
    const removed = [];
    const changed_attrs = [];
    const text_changes = [];

    for (const m of raw) {
        if (m.type === 'added') added.push(m.value);
        else if (m.type === 'removed') removed.push(m.value);
        else if (m.type === 'attr') changed_attrs.push({element: m.element, attr: m.attr, value: m.value});
        else if (m.type === 'text') text_changes.push(m.value);
    }

    return {added, removed, changed_attrs, text_changes};
}"""

# Cap sizes to keep prompt short
_MAX_ADDED = 10
_MAX_REMOVED = 10
_MAX_ATTRS = 5
_MAX_TEXT = 5


def inject_observer(page) -> None:
    """Inject a MutationObserver that records changes. Call BEFORE the action."""
    try:
        page.evaluate(_INJECT_JS)
    except Exception as e:
        log.debug("Failed to inject observer: %s", e)


def collect_mutations(page) -> dict:
    """Collect recorded mutations. Call AFTER the action.

    Returns:
        dict with added, removed, changed_attrs, text_changes lists
    """
    try:
        result = page.evaluate(_COLLECT_JS)
        if not isinstance(result, dict):
            return _empty_result()
        # Cap sizes
        result["added"] = result.get("added", [])[:_MAX_ADDED]
        result["removed"] = result.get("removed", [])[:_MAX_REMOVED]
        result["changed_attrs"] = result.get("changed_attrs", [])[:_MAX_ATTRS]
        result["text_changes"] = result.get("text_changes", [])[:_MAX_TEXT]
        return result
    except Exception as e:
        log.debug("Failed to collect mutations: %s", e)
        return _empty_result()


def format_mutations(mutations: dict) -> str:
    """Format mutations into a compact string for the LLM prompt.

    Returns empty string if nothing interesting happened.
    """
    parts = []
    if mutations["added"]:
        parts.append(f"Added: {', '.join(mutations['added'][:5])}")
    if mutations["removed"]:
        parts.append(f"Removed: {', '.join(mutations['removed'][:5])}")
    if mutations["text_changes"]:
        parts.append(f"New text: {'; '.join(mutations['text_changes'][:3])}")
    if mutations["changed_attrs"]:
        attr_strs = [f"{a['element']}.{a['attr']}={a['value']}" for a in mutations["changed_attrs"][:3]]
        parts.append(f"Changed: {', '.join(attr_strs)}")
    return " | ".join(parts)


def _empty_result() -> dict:
    return {"added": [], "removed": [], "changed_attrs": [], "text_changes": []}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_observer.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/observer.py tests/test_observer.py
git commit -m "feat: MutationObserver change tracking (observer.py)"
```

---

### Task 3: Smart Element Pruning — `fantoma/dom/accessibility.py`

**Files:**
- Create: `tests/test_pruning.py`
- Modify: `fantoma/dom/accessibility.py`

- [ ] **Step 1: Write failing tests for pruning**

Create `tests/test_pruning.py`:

```python
"""Tests for smart element pruning — relevance-based scoring."""
import pytest


def _el(role, name):
    return {"role": role, "name": name, "state": "", "raw": {}}


class TestPruneElements:
    def test_keyword_match_scores_higher(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("link", "About Us"),
            _el("textbox", "Email address"),
            _el("button", "Subscribe"),
        ]
        result = prune_elements(elements, task="enter email address and subscribe", max_elements=2)
        names = [e["name"] for e in result]
        assert "Email address" in names
        assert "Subscribe" in names
        assert "About Us" not in names

    def test_form_inputs_score_higher(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("link", "Home"),
            _el("textbox", "Search"),
            _el("link", "Contact"),
        ]
        result = prune_elements(elements, task="find information", max_elements=2)
        names = [e["name"] for e in result]
        # textbox should score higher than generic links
        assert "Search" in names

    def test_nav_noise_penalized(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("button", "Notifications"),
            _el("button", "Settings"),
            _el("button", "Submit Order"),
        ]
        result = prune_elements(elements, task="submit the order", max_elements=1)
        assert result[0]["name"] == "Submit Order"

    def test_submit_patterns_boosted(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("link", "Privacy Policy"),
            _el("button", "Sign In"),
            _el("link", "Terms"),
        ]
        result = prune_elements(elements, task="log into the website", max_elements=1)
        assert result[0]["name"] == "Sign In"

    def test_respects_max_elements(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [_el("link", f"Link {i}") for i in range(20)]
        result = prune_elements(elements, task="anything", max_elements=5)
        assert len(result) == 5

    def test_empty_task_returns_all_up_to_max(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [_el("textbox", "Name"), _el("button", "Submit")]
        result = prune_elements(elements, task="", max_elements=15)
        assert len(result) == 2

    def test_reindexes_from_zero(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("link", "Irrelevant"),
            _el("textbox", "Email"),
            _el("button", "Login"),
        ]
        result = prune_elements(elements, task="login with email", max_elements=2)
        # After pruning, elements should be re-indexed 0, 1
        # (re-indexing happens in extract(), not prune_elements itself)
        assert len(result) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_pruning.py -v`
Expected: FAIL with `ImportError: cannot import name 'prune_elements'`

- [ ] **Step 3: Implement prune_elements in accessibility.py**

Add the `prune_elements()` function and update `extract()` to accept a `task` parameter. Add to `fantoma/dom/accessibility.py`:

```python
# --- Add these constants after NAV_NOISE ---

# Submit/action button patterns (boosted in pruning)
SUBMIT_PATTERNS = {
    "next", "continue", "sign in", "submit", "login",
    "search", "sign up", "register", "create", "confirm",
    "log in", "proceed", "send", "verify", "done",
}

# Stop words removed from task for keyword extraction
_STOP_WORDS = {
    "the", "a", "an", "to", "in", "on", "at", "for", "of", "and",
    "or", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "this", "that", "these",
    "those", "it", "its", "i", "my", "me", "we", "our", "you", "your",
    "go", "get", "use", "find", "with", "from", "into", "then",
}


def prune_elements(elements: list[dict], task: str = "", max_elements: int = 15) -> list[dict]:
    """Score and rank elements by relevance to the task. Returns top N.

    Scoring:
      +3  element name contains a task keyword
      +2  textbox/combobox/searchbox (form inputs)
      +2  name matches a submit pattern
      +1  checkbox or radio
      -2  name matches navigation noise
       0  baseline
    """
    # Extract task keywords
    task_lower = task.lower()
    words = task_lower.split()
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 1]

    scored = []
    for el in elements:
        score = 0
        name_lower = el.get("name", "").lower()
        role = el.get("role", "")

        # Keyword match
        for kw in keywords:
            if kw in name_lower.split():
                score += 3
                break  # one match is enough

        # Form input boost
        if role in ("textbox", "combobox", "searchbox"):
            score += 2

        # Submit pattern boost
        if any(p in name_lower for p in SUBMIT_PATTERNS):
            score += 2

        # Checkbox/radio boost
        if role in ("checkbox", "radio"):
            score += 1

        # Nav noise penalty
        if _is_nav_noise(name_lower):
            score -= 2

        scored.append((score, el))

    # Sort by score descending, then by original order for ties
    scored.sort(key=lambda x: x[0], reverse=True)
    return [el for _, el in scored[:max_elements]]
```

Then update the `extract()` method of `AccessibilityExtractor` and the `extract_aria()` function to accept a `task` parameter:

In `extract_aria()`, add `task: str = ""` parameter. At the end, before returning, call `prune_elements()`:

```python
def extract_aria(page, max_elements: int = None, max_headings: int = None, task: str = "") -> str:
    # ... existing code up to building interactive list ...

    _max_el = max_elements or MAX_ELEMENTS
    _max_hd = max_headings or MAX_HEADINGS

    if interactive:
        if task:
            shown = prune_elements(interactive, task, _max_el)
        else:
            shown = interactive[:_max_el]
        # ... rest unchanged ...
```

In `AccessibilityExtractor.extract()`, add `task: str = ""`:

```python
def extract(self, page, task: str = "") -> str:
    result = extract_aria(page, self._max_elements, self._max_headings, task=task)
    # ... rest unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_pruning.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_dom_extractor.py tests/test_form_login.py -v`
Expected: All existing tests still PASS (task="" is backward-compatible)

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/dom/accessibility.py tests/test_pruning.py
git commit -m "feat: smart element pruning — relevance-based scoring"
```

---

### Task 4: Tree Diffing for Dynamic Content — `fantoma/dom/accessibility.py`

**Files:**
- Create: `tests/test_tree_diff.py`
- Modify: `fantoma/dom/accessibility.py`

- [ ] **Step 1: Write failing tests for tree diffing**

Create `tests/test_tree_diff.py`:

```python
"""Tests for tree diffing — marking new elements with * prefix."""
import pytest


class TestTreeDiff:
    def test_new_elements_marked(self):
        from fantoma.dom.accessibility import mark_new_elements
        previous = [
            {"role": "button", "name": "Submit"},
            {"role": "textbox", "name": "Email"},
        ]
        current = [
            {"role": "button", "name": "Submit"},
            {"role": "textbox", "name": "Email"},
            {"role": "textbox", "name": "Verification code"},
            {"role": "button", "name": "Verify"},
        ]
        new_flags = mark_new_elements(previous, current)
        assert new_flags == [False, False, True, True]

    def test_all_new_on_first_page(self):
        from fantoma.dom.accessibility import mark_new_elements
        current = [
            {"role": "textbox", "name": "Email"},
            {"role": "button", "name": "Next"},
        ]
        new_flags = mark_new_elements([], current)
        # On first page (no previous), nothing is "new" — everything is just current
        assert new_flags == [False, False]

    def test_no_new_elements(self):
        from fantoma.dom.accessibility import mark_new_elements
        previous = [
            {"role": "button", "name": "Submit"},
            {"role": "textbox", "name": "Email"},
        ]
        current = [
            {"role": "button", "name": "Submit"},
            {"role": "textbox", "name": "Email"},
        ]
        new_flags = mark_new_elements(previous, current)
        assert new_flags == [False, False]

    def test_all_new_elements(self):
        from fantoma.dom.accessibility import mark_new_elements
        previous = [
            {"role": "textbox", "name": "Email"},
        ]
        current = [
            {"role": "textbox", "name": "Verification code"},
            {"role": "button", "name": "Verify"},
        ]
        new_flags = mark_new_elements(previous, current)
        assert new_flags == [True, True]


class TestParseInteractiveHandlesStarPrefix:
    def test_parse_star_prefix(self):
        from fantoma.dom.accessibility import AccessibilityExtractor
        output = '*[0] textbox "Verification code"\n[1] button "Submit"\n*[2] button "Verify"'
        elements = AccessibilityExtractor._parse_interactive_from_output(output)
        assert len(elements) == 3
        assert elements[0]["role"] == "textbox"
        assert elements[0]["name"] == "Verification code"
        assert elements[1]["role"] == "button"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_tree_diff.py -v`
Expected: FAIL with `ImportError: cannot import name 'mark_new_elements'`

- [ ] **Step 3: Implement tree diffing**

Add to `fantoma/dom/accessibility.py`:

```python
def mark_new_elements(previous: list[dict], current: list[dict]) -> list[bool]:
    """Compare current elements with previous by (role, name) tuple.

    Returns a list of booleans — True if element is new (not in previous).
    On first page (empty previous), all elements are marked False (nothing is "new").
    """
    if not previous:
        return [False] * len(current)

    prev_set = {(el.get("role", ""), el.get("name", "")) for el in previous}
    return [(el.get("role", ""), el.get("name", "")) not in prev_set for el in current]
```

Update `extract_aria()` to accept `previous_elements` and mark new elements:

```python
def extract_aria(page, max_elements=None, max_headings=None, task="", previous_elements=None) -> str:
    # ... after building shown list and before appending to output ...

    if interactive:
        if task:
            shown = prune_elements(interactive, task, _max_el)
        else:
            shown = interactive[:_max_el]

        new_flags = mark_new_elements(previous_elements or [], shown)

        output.append(f"Elements ({len(shown)} of {len(interactive)}):")
        for i, el in enumerate(shown):
            prefix = "*" if new_flags[i] else ""
            output.append(f'{prefix}[{i}] {el["role"]} "{el["name"]}"{el["state"]}')
    # ...
```

Update `_parse_interactive_from_output` to handle `*` prefix:

```python
@staticmethod
def _parse_interactive_from_output(output: str) -> list[dict]:
    elements = []
    for line in output.split("\n"):
        match = re.match(r'\*?\[(\d+)\]\s+(\w+)\s+"([^"]*)"', line)
        if match:
            elements.append({
                "index": int(match.group(1)),
                "role": match.group(2),
                "name": match.group(3),
            })
    return elements
```

Update `AccessibilityExtractor.extract()` to pass `_last_interactive` as previous_elements:

```python
def extract(self, page, task: str = "") -> str:
    previous = list(self._last_interactive)  # copy before overwriting
    result = extract_aria(page, self._max_elements, self._max_headings,
                          task=task, previous_elements=previous)
    # ... rest unchanged ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_tree_diff.py tests/test_pruning.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run existing tests for regressions**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_dom_extractor.py tests/test_form_login.py -v`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/dom/accessibility.py tests/test_tree_diff.py
git commit -m "feat: tree diffing — mark new elements with * prefix"
```

---

### Task 5: Script Caching for Repeat Visits — `fantoma/resilience/script_cache.py`

**Files:**
- Create: `fantoma/resilience/script_cache.py`
- Create: `tests/test_script_cache.py`

- [ ] **Step 1: Write failing tests for script cache**

Create `tests/test_script_cache.py`:

```python
"""Tests for script caching — save and replay action sequences."""
import os
import pytest
import tempfile


def _make_elements(names):
    """Create element list with (role, name) tuples."""
    return [{"role": "button", "name": n} for n in names]


@pytest.fixture
def cache(tmp_path):
    from fantoma.resilience.script_cache import ScriptCache
    db_path = str(tmp_path / "test_cache.db")
    return ScriptCache(db_path=db_path)


class TestScriptCacheSaveAndLookup:
    def test_save_and_lookup_exact_match(self, cache):
        elements = _make_elements(["Login", "Email", "Password"])
        actions = [
            {"action": "TYPE [1] \"user@test.com\"", "expected_url_pattern": None, "expected_elements": ["textbox:Email"]},
            {"action": "CLICK [0]", "expected_url_pattern": "/dashboard", "expected_elements": []},
        ]
        cache.save("example.com", elements, actions)
        result = cache.lookup("example.com", elements)
        assert result is not None
        assert len(result) == 2
        assert result[0]["action"] == "TYPE [1] \"user@test.com\""

    def test_lookup_no_match(self, cache):
        result = cache.lookup("notfound.com", _make_elements(["A", "B"]))
        assert result is None

    def test_fuzzy_match_above_threshold(self, cache):
        elements_v1 = _make_elements(["Login", "Email", "Password", "Remember me"])
        actions = [{"action": "CLICK [0]", "expected_url_pattern": None, "expected_elements": []}]
        cache.save("example.com", elements_v1, actions)

        # Same page but with an extra ad element (>80% overlap)
        elements_v2 = _make_elements(["Login", "Email", "Password", "Remember me", "Ad Banner"])
        result = cache.lookup("example.com", elements_v2)
        assert result is not None

    def test_fuzzy_match_below_threshold(self, cache):
        elements_v1 = _make_elements(["Login", "Email", "Password"])
        actions = [{"action": "CLICK [0]", "expected_url_pattern": None, "expected_elements": []}]
        cache.save("example.com", elements_v1, actions)

        # Completely different page (<80% overlap)
        elements_v2 = _make_elements(["Register", "Phone", "Country", "Submit", "Terms"])
        result = cache.lookup("example.com", elements_v2)
        assert result is None

    def test_overwrites_same_domain_and_structure(self, cache):
        elements = _make_elements(["Login", "Email"])
        cache.save("example.com", elements, [{"action": "CLICK [0]", "expected_url_pattern": None, "expected_elements": []}])
        cache.save("example.com", elements, [{"action": "TYPE [1] \"new\"", "expected_url_pattern": None, "expected_elements": []}])
        result = cache.lookup("example.com", elements)
        assert result[0]["action"] == "TYPE [1] \"new\""

    def test_credentials_stored_as_placeholders(self, cache):
        elements = _make_elements(["Login", "Email"])
        secrets = {"email": "user@test.com", "password": "secret123"}
        actions = [
            {"action": 'TYPE [1] "user@test.com"', "expected_url_pattern": None, "expected_elements": []},
            {"action": 'TYPE [2] "secret123"', "expected_url_pattern": None, "expected_elements": []},
        ]
        cache.save("example.com", elements, actions, sensitive_data=secrets)
        result = cache.lookup("example.com", elements)
        # Real values should be replaced with placeholders
        assert "<secret:email>" in result[0]["action"]
        assert "<secret:password>" in result[1]["action"]
        assert "user@test.com" not in result[0]["action"]
        assert "secret123" not in result[1]["action"]


class TestScriptCacheValidation:
    def test_rejects_long_sequences(self, cache):
        elements = _make_elements(["A"])
        actions = [{"action": f"CLICK [{i}]", "expected_url_pattern": None, "expected_elements": []} for i in range(25)]
        cache.save("example.com", elements, actions)
        # Sequences > 20 steps should not be cached
        result = cache.lookup("example.com", elements)
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_script_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fantoma.resilience.script_cache'`

- [ ] **Step 3: Implement script_cache.py**

Create `fantoma/resilience/script_cache.py`:

```python
"""Script cache — save and replay successful action sequences.

After a successful agent.run(), saves the action sequence keyed by domain +
page structure. On repeat visits, replays without LLM calls. Falls back to
LLM if replay diverges from expectations.

Storage: SQLite at ~/.local/share/fantoma/script_cache.db
Cache key: domain + sorted (role, name) tuples from initial page elements
Match: fuzzy overlap >80% (sites change minor elements between visits)
"""
import json
import logging
import os
import sqlite3

log = logging.getLogger("fantoma.script_cache")

_DEFAULT_DB = os.path.join(os.path.expanduser("~"), ".local", "share", "fantoma", "script_cache.db")
_MAX_STEPS = 20
_OVERLAP_THRESHOLD = 0.80


class ScriptCache:
    """SQLite-backed cache for action sequences."""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or _DEFAULT_DB
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                element_key TEXT NOT NULL,
                actions TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(domain, element_key)
            )
        """)
        self._conn.commit()

    def save(self, domain: str, elements: list[dict], actions: list[dict],
             sensitive_data: dict = None) -> bool:
        """Save an action sequence for a domain + page structure.

        Args:
            domain: Site domain
            elements: Initial page elements (list of dicts with role, name)
            actions: Ordered list of action dicts
            sensitive_data: If provided, real values are replaced with <secret:KEY> placeholders

        Returns:
            True if saved, False if rejected (too long, etc.)
        """
        if len(actions) > _MAX_STEPS:
            log.debug("Rejecting cache entry: %d steps > max %d", len(actions), _MAX_STEPS)
            return False

        # Replace credentials with placeholders
        if sensitive_data:
            actions = _mask_secrets(actions, sensitive_data)

        element_key = _make_key(elements)
        actions_json = json.dumps(actions)

        self._conn.execute(
            "INSERT OR REPLACE INTO scripts (domain, element_key, actions) VALUES (?, ?, ?)",
            (domain, element_key, actions_json),
        )
        self._conn.commit()
        log.info("Cached %d-step script for %s", len(actions), domain)
        return True

    def lookup(self, domain: str, elements: list[dict]) -> list[dict] | None:
        """Find a cached script for the given domain and page structure.

        Uses fuzzy matching: finds the entry with highest element overlap
        above the threshold (80%).

        Returns:
            List of action dicts, or None if no match.
        """
        cursor = self._conn.execute(
            "SELECT element_key, actions FROM scripts WHERE domain = ?",
            (domain,),
        )
        rows = cursor.fetchall()
        if not rows:
            return None

        current_set = _element_set(elements)
        if not current_set:
            return None

        best_match = None
        best_overlap = 0.0

        for element_key, actions_json in rows:
            cached_set = set(json.loads(element_key))
            # Compute overlap as Jaccard-like: intersection / max(len)
            intersection = len(current_set & cached_set)
            denominator = max(len(current_set), len(cached_set))
            if denominator == 0:
                continue
            overlap = intersection / denominator
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = actions_json

        if best_overlap >= _OVERLAP_THRESHOLD and best_match:
            actions = json.loads(best_match)
            log.info("Cache hit for %s (%.0f%% overlap, %d steps)",
                     domain, best_overlap * 100, len(actions))
            return actions

        return None

    def close(self):
        self._conn.close()


def _make_key(elements: list[dict]) -> str:
    """Create a sorted JSON key from element (role, name) tuples."""
    tuples = sorted((el.get("role", ""), el.get("name", "")) for el in elements)
    return json.dumps(tuples)


def _element_set(elements: list[dict]) -> set:
    """Create a set of (role, name) tuples for overlap comparison."""
    return {(el.get("role", ""), el.get("name", "")) for el in elements}


def _mask_secrets(actions: list[dict], secrets: dict) -> list[dict]:
    """Replace real credential values with <secret:KEY> placeholders."""
    masked = []
    for action in actions:
        a = dict(action)
        text = a.get("action", "")
        for key, value in secrets.items():
            if value and value in text:
                text = text.replace(value, f"<secret:{key}>")
        a["action"] = text
        masked.append(a)
    return masked
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_script_cache.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/resilience/script_cache.py tests/test_script_cache.py
git commit -m "feat: script caching for repeat visits (SQLite)"
```

---

### Task 6: Observation Masking & Prompt Update — `fantoma/executor.py` + `fantoma/llm/prompts.py`

This task changes the history/prompt model in executor.py and updates the REACTIVE_SYSTEM prompt.

**Files:**
- Modify: `fantoma/executor.py`
- Modify: `fantoma/llm/prompts.py`

- [ ] **Step 1: Update REACTIVE_SYSTEM prompt in prompts.py**

Replace `REACTIVE_SYSTEM` in `fantoma/llm/prompts.py` with:

```python
REACTIVE_SYSTEM = """\
You control a browser. Your job is to COMPLETE the task, not just observe the page.

Pick 1-5 actions from this list (one per line):
CLICK [number]
TYPE [number] "text"
SELECT [number] "option"
SCROLL down
SCROLL up
NAVIGATE https://example.com
PRESS Enter
SEARCH_PAGE "text to find"
FIND "css selector"
DONE

Rules:
- Match [number] to the element list shown after the task.
- Elements marked with * are NEW (just appeared from your last action) — focus on these.
- You may return multiple actions (one per line) to execute in sequence.
- To fill a form: TYPE each field, then CLICK submit — all in one response.
- After typing in a search field, add PRESS Enter.
- NAVIGATE and DONE end the sequence — any actions after them are ignored.
- Only say DONE when the task is fully COMPLETED (form submitted, data extracted, action taken).
- Do NOT say DONE just because you can see a form or page — you must interact with it first.
- SEARCH_PAGE finds text on the current page (like Ctrl+F). Free, no scrolling needed.
- FIND runs a CSS selector query and returns matching elements. Free.
- If secrets are available, use them with <secret:name> syntax (e.g., TYPE [3] "<secret:email>").
- After each action you'll see an outcome (URL change, errors, new elements). Use this feedback.
- If you see an error message after your action, adjust your approach — don't repeat the same action.
- Reply with ONLY action lines, nothing else.\
"""
```

- [ ] **Step 2: Modify executor.py — observation masking**

In `Executor.__init__()`, change the compaction defaults:

```python
# Replace:
self._compact_threshold = 30
self._compact_keep_recent = 6

# With:
self._compact_threshold = 50        # observation masking: keep more history
self._compact_keep_recent = 10      # keep more recent steps verbatim
self._action_outcomes: list[str] = []  # "Step N: ACTION → outcome"
self._max_history = 50              # cap action history entries
```

In `execute_reactive()`, update the prompt construction. Replace the section that builds `user_msg` (lines ~262-272 in current code):

```python
# Build user message with observation masking
user_msg = f"Task: {task}\n\n{dom_text}"

# Action history: verbatim outcomes (capped at _max_history)
if self._action_outcomes:
    recent_outcomes = self._action_outcomes[-self._max_history:]
    history_text = "\n".join(f"  {s}" for s in recent_outcomes[-15:])
    user_msg += f"\n\nAction history:\n{history_text}"

# Compacted memory (fallback for very long tasks)
if self._compacted_memory:
    user_msg += f"\n\n[Earlier progress summary:\n{self._compacted_memory}]"

if self._secrets:
    secret_list = ", ".join(f"<secret:{k}>" for k in self._secrets.keys())
    user_msg += f"\n\nAvailable secrets: {secret_list}"
if failed:
    user_msg += f"\n\nFailed (don't repeat): {', '.join(failed)}"
```

Update `_compact_history()` to use `_action_outcomes` and only trigger at 40% of estimated context:

```python
def _compact_history(self):
    """Compact action history only when approaching context window limits.

    Observation masking: keep action outcomes verbatim, only compact when
    history exceeds 40% of estimated context window (~2 chars/token).
    Default context: 49K tokens for Hermes = ~98K chars. 40% = ~39K chars.
    """
    history_text = "\n".join(self._action_outcomes)
    estimated_chars = len(history_text)
    # 49K context * 2 chars/token * 0.4 threshold
    char_threshold = 49000 * 2 * 0.4

    if estimated_chars < char_threshold:
        return  # No compaction needed

    from fantoma.llm.prompts import COMPACTION_SYSTEM

    # Keep recent, compact old
    old = self._action_outcomes[:-self._compact_keep_recent]
    recent = self._action_outcomes[-self._compact_keep_recent:]
    old_text = "\n".join(old)

    try:
        summary = self.llm.chat(
            [{"role": "system", "content": COMPACTION_SYSTEM},
             {"role": "user", "content": f"Steps completed so far:\n{old_text}"}],
            max_tokens=300,
        )
        if summary:
            self._compacted_memory = summary.strip()
            self._action_outcomes = recent
            log.info("History compacted: %d entries → summary + %d recent",
                     len(old), len(recent))
    except Exception as e:
        log.warning("History compaction failed: %s", e)
```

- [ ] **Step 3: Run existing tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/full_signup_test.py --ignore=tests/live_reddit_test.py --ignore=tests/real_signup_test.py --ignore=tests/real_site_test.py --ignore=tests/scenario_test_deepseek.py`
Expected: All existing tests still PASS

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/executor.py fantoma/llm/prompts.py
git commit -m "feat: observation masking — keep action outcomes, drop old DOM snapshots"
```

---

### Task 7: Integration — Wire Everything into executor.py

This task wires features 1-5 into the reactive loop in executor.py.

**Files:**
- Modify: `fantoma/executor.py`
- Modify: `fantoma/browser/engine.py`
- Modify: `fantoma/browser/form_login.py`
- Modify: `fantoma/agent.py`

- [ ] **Step 1: Add main_world_eval to engine.py**

In `fantoma/browser/engine.py`, `_start_camoufox()` method, both Camoufox constructor calls need `main_world_eval=True`:

```python
# In the persistent context block:
cm_kwargs = dict(
    persistent_context=True,
    user_data_dir=self.profile_dir,
    headless=self.headless,
    humanize=True,
    os=["linux"],
    proxy=proxy,
    main_world_eval=True,
    **stealth_config,
)

# In the non-persistent block:
cm_kwargs = dict(
    headless=self.headless,
    humanize=True,
    os=["linux"],
    proxy=proxy,
    main_world_eval=True,
    **stealth_config,
)
```

- [ ] **Step 2: Wire verification + observer + outcomes into execute_reactive()**

In `fantoma/executor.py`, add imports at the top:

```python
from fantoma.browser.page_state import verify_action, dom_hash as compute_dom_hash
from fantoma.browser.observer import inject_observer, collect_mutations, format_mutations
```

In `execute_reactive()`, update the CLICK handling block (the main action execution section). For each action that isn't SCROLL/WAIT/TYPE/NAVIGATE/DONE, add observer injection before and verification after:

```python
# Before executing the action (just before `execute_action` call):
inject_observer(page)
pre_action_url = self.browser.get_url()
pre_action_hash = compute_dom_hash(page)

# After executing and checking page change, build outcome string:
mutations = collect_mutations(page)
outcome = verify_action(page, pre_action_url, pre_action_hash, self.dom)

# Build outcome string
outcome_parts = []
if outcome["url_changed"]:
    outcome_parts.append(f"URL changed to {self.browser.get_url()}")
if outcome["error_found"]:
    outcome_parts.append(f'error: "{outcome["error_found"]}"')
if outcome["new_elements"] > 0:
    outcome_parts.append(f"{outcome['new_elements']} new elements")
mut_str = format_mutations(mutations)
if mut_str:
    outcome_parts.append(mut_str)
if not outcome_parts:
    outcome_parts.append("no visible change")

outcome_str = f"Step {step_num}: {action[:40]} → {', '.join(outcome_parts)}"
self._action_outcomes.append(outcome_str)
```

Also add observer + outcome tracking for TYPE actions:

```python
# In the TYPE block, after successful type:
outcome_str = f"Step {step_num}: TYPE → typed into field"
self._action_outcomes.append(outcome_str)
```

And for NAVIGATE:

```python
outcome_str = f"Step {step_num}: NAVIGATE → loaded {self.browser.get_url()}"
self._action_outcomes.append(outcome_str)
```

Update the step_history tracking at the end of the loop body to use `_action_outcomes` instead of `_step_history`:

```python
# Replace the old step_history block:
if actions_batch:
    self._compact_history()
```

Pass task to `dom.extract()`:

```python
# Replace all calls to self.dom.extract(page) in execute_reactive() with:
dom_text = self.dom.extract(page, task=task)
```

- [ ] **Step 3: Wire error detection into form_login.py**

In `fantoma/browser/form_login.py`, after the submit click + wait (line ~381), add error detection:

```python
# After time.sleep(step_delay) and before verification check:
from fantoma.browser.page_state import detect_errors
errors = detect_errors(page)
if errors:
    log.info("Step %d: form errors detected: %s", step + 1, errors)
    return {
        "success": False,
        "steps": step + 1,
        "url": page.url,
        "fields_filled": fields_filled,
        "errors": errors,
    }
```

- [ ] **Step 4: Include error messages in AgentResult**

In `fantoma/agent.py`, update the `login()` method. After calling `form_login()` (line ~264), check for errors:

```python
# After form_login() returns, if there are errors, include them:
if result.get("errors"):
    log.warning("Login form errors: %s", result["errors"])
    # Continue — errors don't necessarily mean total failure
    # (e.g., "email already exists" on signup means we should try login instead)
```

In the failure return paths in `login()`, include errors in the AgentResult.error field:

```python
# When returning a failed AgentResult, if result has errors:
error_msg = result.get("errors", [""])[0] if result.get("errors") else "Login failed"
return AgentResult(success=False, data=result, steps_taken=result.get("steps", 0), error=error_msg)
```

- [ ] **Step 5: Wire script cache into execute_reactive()**

In `fantoma/executor.py`, add import:

```python
from fantoma.resilience.script_cache import ScriptCache
```

In `Executor.__init__()`, add:

```python
self._script_cache = ScriptCache()
```

At the start of `execute_reactive()`, before the main loop, add cache lookup:

```python
# Script cache: check for cached action sequence
from urllib.parse import urlparse
domain = urlparse(self.browser.get_url()).netloc
initial_elements = self.dom._last_interactive

cached_actions = self._script_cache.lookup(domain, initial_elements)
if cached_actions:
    log.info("Script cache hit for %s — attempting replay", domain)
    replay_result = self._replay_cached(cached_actions, task, page)
    if replay_result:
        return replay_result
    log.info("Replay failed — falling back to LLM")
```

At the end of `execute_reactive()`, before the successful return in the DONE block, add cache save:

```python
# Save to cache if successful and short enough
if step_num <= 20:
    try:
        actions_for_cache = [
            {"action": o.split(" → ")[0].split(": ", 1)[1] if ": " in o else o,
             "expected_url_pattern": None, "expected_elements": []}
            for o in self._action_outcomes
        ]
        self._script_cache.save(domain, initial_elements, actions_for_cache,
                                sensitive_data=self._secrets)
    except Exception as e:
        log.debug("Cache save failed: %s", e)
```

Add the `_replay_cached()` method to Executor:

```python
def _replay_cached(self, actions: list[dict], task: str, page) -> 'AgentResult':
    """Replay a cached action sequence. Returns AgentResult on success, None on failure."""
    from fantoma.agent import AgentResult
    from fantoma.browser.page_state import verify_action, dom_hash as compute_dom_hash

    steps_detail = []
    for i, action_entry in enumerate(actions):
        action = action_entry["action"]

        # Inject secrets
        if self._secrets:
            action = self._inject_secrets(action, self._secrets)

        pre_url = self.browser.get_url()
        pre_hash = compute_dom_hash(page)

        self._total_actions += 1
        executed = execute_action(action, self.browser, self.dom)
        if not executed:
            log.info("Replay step %d failed to execute: %s", i + 1, action[:40])
            return None

        wait_for_network_idle(self.browser, timeout=self.config.timeouts.network_idle)

        # Verify outcome
        outcome = verify_action(page, pre_url, pre_hash, self.dom)

        # If expected URL pattern exists, check it
        expected_url = action_entry.get("expected_url_pattern")
        if expected_url and not outcome["url_changed"]:
            log.info("Replay step %d: expected URL change to %s, didn't happen", i + 1, expected_url)
            return None

        steps_detail.append({
            "step": i + 1, "action": action[:40],
            "success": True, "url": self.browser.get_url(),
        })

    # Replay complete
    dom_text = self.dom.extract(page, task=task)
    data = self._extract_result(task, dom_text)
    return AgentResult(
        success=True, data=data, steps_taken=len(actions),
        steps_detail=steps_detail, escalations=0,
    )
```

- [ ] **Step 6: Run all tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/full_signup_test.py --ignore=tests/live_reddit_test.py --ignore=tests/real_signup_test.py --ignore=tests/real_site_test.py --ignore=tests/scenario_test_deepseek.py`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/executor.py fantoma/browser/engine.py fantoma/browser/form_login.py fantoma/agent.py
git commit -m "feat: wire navigation intelligence into reactive loop"
```

---

### Task 8: Version Bump & Final Verification

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Bump version to 0.6.0**

In `pyproject.toml`, change:

```toml
version = "0.6.0"
```

- [ ] **Step 2: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/full_signup_test.py --ignore=tests/live_reddit_test.py --ignore=tests/real_signup_test.py --ignore=tests/real_site_test.py --ignore=tests/scenario_test_deepseek.py`
Expected: All tests PASS (unit tests — no live browser needed)

- [ ] **Step 3: Verify imports work**

Run: `cd /home/workspace/workbench/fantoma && python -c "from fantoma.browser.page_state import verify_action, detect_errors; from fantoma.browser.observer import inject_observer, collect_mutations; from fantoma.resilience.script_cache import ScriptCache; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add pyproject.toml
git commit -m "release: v0.6.0 — navigation intelligence"
```
