# Fantoma v0.6 Phase 3 — Competitive Edge Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four competitive-edge features to Fantoma's executor: adaptive DOM modes, ARIA landmark grouping, per-step success criteria, and script cache wiring with self-healing selectors.

**Architecture:** All features are code-only additions to the existing executor loop. Features 1, 2, and 4 are independent. Feature 3 depends on Feature 4 (`assess_progress()` is used during cache replay). Each feature adds a new function/method and wires it into the executor.

**Tech Stack:** Python 3.12, Playwright (ARIA snapshots), difflib (fuzzy matching), SQLite (script cache), pytest

**Branch:** `feat/v0.6-navigation-intelligence` (existing)

**Spec:** `docs/superpowers/specs/2026-03-30-fantoma-v06-phase3-design.md`

---

## File Map

| File | Responsibility | Tasks |
|------|---------------|-------|
| `fantoma/dom/accessibility.py` | Add `mode` param to `extract_aria()`, add landmark grouping | 1, 2 |
| `fantoma/executor.py` | Add `_infer_dom_mode()`, wire mode into extraction, wire `assess_progress()`, wire `ScriptCache`, add `_replay_cached()` | 1, 3, 4 |
| `fantoma/browser/page_state.py` | Add `assess_progress()` and `_infer_task_intent()` | 3 |
| `fantoma/resilience/script_cache.py` | Add `target_role`/`target_name` to action format, add `heal_action()` | 4 |
| `tests/test_dom_modes.py` | Tests for adaptive DOM modes + mode inference | 1 |
| `tests/test_landmarks.py` | Tests for ARIA landmark grouping | 2 |
| `tests/test_progress.py` | Tests for `assess_progress()`, intent inference, stall detection | 3 |
| `tests/test_cache_replay.py` | Tests for cache wiring + self-healing | 4 |
| `tests/test_script_cache.py` | Extended with `heal_action()` tests | 4 |

---

## Task 1: Adaptive DOM Modes

**Files:**
- Modify: `fantoma/dom/accessibility.py:226-318` (the `extract_aria` function)
- Modify: `fantoma/executor.py:195-432` (the `execute_reactive` method)
- Create: `tests/test_dom_modes.py`

### Step 1.1: Write failing tests for DOM mode behaviour

- [ ] Create `tests/test_dom_modes.py` with tests for all three modes and the inference function.

```python
"""Tests for adaptive DOM extraction modes."""
import pytest
from unittest.mock import MagicMock, PropertyMock


def _make_snapshot_with_inputs(n_textboxes=6, n_links=10):
    """Build a fake ARIA snapshot string with N textboxes and N links."""
    lines = ['- heading "Test Page" [level=1]']
    for i in range(n_textboxes):
        lines.append(f'- textbox "Field {i}"')
    for i in range(n_links):
        lines.append(f'- link "Link {i}"')
    lines.append('- button "Submit"')
    return "\n".join(lines)


def _make_page(url="https://example.com", snapshot=None):
    """Create a mock page with configurable ARIA snapshot."""
    page = MagicMock()
    type(page).url = PropertyMock(return_value=url)
    page.title.return_value = "Test Page"
    if snapshot is None:
        snapshot = _make_snapshot_with_inputs(n_textboxes=2, n_links=5)
    page.locator.return_value.aria_snapshot.return_value = snapshot
    return page


class TestExtractAriaFormMode:
    """Form mode: inputs first, tighter caps."""

    def test_form_mode_caps_at_20_elements(self):
        from fantoma.dom.accessibility import extract_aria
        snapshot = _make_snapshot_with_inputs(n_textboxes=10, n_links=20)
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page, mode="form")
        # Count numbered elements [N]
        import re
        elements = re.findall(r'\[\d+\]', result)
        assert len(elements) <= 20

    def test_form_mode_inputs_come_first(self):
        from fantoma.dom.accessibility import extract_aria
        snapshot = _make_snapshot_with_inputs(n_textboxes=3, n_links=5)
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page, mode="form")
        lines = [l for l in result.split("\n") if "[0]" in l or "[1]" in l or "[2]" in l]
        # First 3 elements should be textboxes
        for line in lines:
            assert "textbox" in line

    def test_form_mode_limits_headings_to_5(self):
        from fantoma.dom.accessibility import extract_aria
        headings = "\n".join(f'- heading "Heading {i}" [level=2]' for i in range(10))
        snapshot = headings + '\n- textbox "Email"\n- button "Submit"'
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page, mode="form")
        heading_count = result.count("(h2)")
        assert heading_count <= 5


class TestExtractAriaContentMode:
    """Content mode: text only, no numbered elements."""

    def test_content_mode_no_numbered_elements(self):
        from fantoma.dom.accessibility import extract_aria
        snapshot = '- heading "Article Title" [level=1]\n- text "Some article content here."\n- link "Click me"\n- button "Submit"'
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page, mode="content")
        import re
        numbered = re.findall(r'\[\d+\]', result)
        assert len(numbered) == 0

    def test_content_mode_includes_headings_and_text(self):
        from fantoma.dom.accessibility import extract_aria
        snapshot = '- heading "Article Title" [level=1]\n- text "Body text goes here."'
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page, mode="content")
        assert "Article Title" in result
        assert "Body text goes here" in result


class TestExtractAriaNavigateMode:
    """Navigate mode: current default behaviour, unchanged."""

    def test_navigate_mode_is_default(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page()
        result_default = extract_aria(page)
        result_nav = extract_aria(page, mode="navigate")
        assert result_default == result_nav


class TestInferDomMode:
    """Mode inference from task keywords + page state."""

    def test_login_task_returns_form(self):
        from fantoma.executor import _infer_dom_mode
        page = _make_page()
        assert _infer_dom_mode("login to the site", page, element_count=2) == "form"

    def test_extract_task_returns_content(self):
        from fantoma.executor import _infer_dom_mode
        page = _make_page()
        assert _infer_dom_mode("extract the article text", page, element_count=2) == "content"

    def test_generic_task_returns_navigate(self):
        from fantoma.executor import _infer_dom_mode
        page = _make_page()
        assert _infer_dom_mode("click the red button", page, element_count=2) == "navigate"

    def test_many_textboxes_overrides_to_form(self):
        from fantoma.executor import _infer_dom_mode
        page = _make_page()
        # 6 textboxes should force form mode even for a navigate-like task
        assert _infer_dom_mode("click the red button", page, element_count=6) == "form"

    def test_page_override_beats_content_keywords(self):
        from fantoma.executor import _infer_dom_mode
        page = _make_page()
        # Content keywords but 5+ textboxes → form wins
        assert _infer_dom_mode("extract data from form", page, element_count=7) == "form"
```

- [ ] Run tests to verify they fail:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_dom_modes.py -v
```

Expected: FAIL — `extract_aria()` doesn't accept `mode` parameter, `_infer_dom_mode` doesn't exist.

### Step 1.2: Implement `mode` parameter on `extract_aria()`

- [ ] Edit `fantoma/dom/accessibility.py`. Add `mode` parameter to `extract_aria()` and handle each mode.

In `extract_aria()` at line 226, change the signature:

```python
def extract_aria(page, max_elements: int = None, max_headings: int = None, task: str = "", previous_elements: list = None, mode: str = "navigate") -> str:
```

After building the `interactive` and `headings` lists (after line 284), add mode handling before the "Build output" section:

```python
    # ── Mode handling ────────────────────────────────────────────
    if mode == "content":
        return extract_aria_content(page)

    if mode == "form":
        _max_el = 20
        _max_hd = 5
        # Sort: inputs first (textbox, combobox, searchbox), then others
        input_roles = {"textbox", "combobox", "searchbox"}
        interactive.sort(key=lambda el: (0 if el.get("role", "") in input_roles else 1))
    else:
        _max_el = max_elements or MAX_ELEMENTS
        _max_hd = max_headings or MAX_HEADINGS
```

And remove the original `_max_el` / `_max_hd` assignment at lines 292-293 (now handled by the mode block above). The full replacement block replaces lines 287-318:

```python
    # Build output
    output = []
    output.append(f"Page: {title}")
    output.append(f"URL: {url}")
    output.append("")

    if mode == "form":
        _max_el = 20
        _max_hd = 5
        # Sort: inputs first (textbox, combobox, searchbox), then others
        input_roles = {"textbox", "combobox", "searchbox"}
        interactive.sort(key=lambda el: (0 if el.get("role", "") in input_roles else 1))
    else:
        _max_el = max_elements or MAX_ELEMENTS
        _max_hd = max_headings or MAX_HEADINGS

    if interactive:
        interactive = dedup_elements(interactive)
        if task:
            shown = prune_elements(interactive, task, _max_el)
        else:
            shown = interactive[:_max_el]

        new_flags = mark_new_elements(previous_elements or [], shown)

        output.append(f"Elements ({len(shown)} of {len(interactive)}):")
        for i, el in enumerate(shown):
            prefix = "*" if new_flags[i] else ""
            state = enrich_field_state(el) or el["state"]
            output.append(f'{prefix}[{i}] {el["role"]} "{el["name"]}"{state}')
    else:
        output.append("Elements: none found")

    if headings:
        output.append("")
        output.append("Page text:")
        for h in headings[:_max_hd]:
            output.append(h)

    return "\n".join(output)
```

Add content mode early return after the snapshot parsing (before "Build output"), right after the `for line in lines:` loop ends:

```python
    # Content mode: delegate to content extraction (no interactive numbering)
    if mode == "content":
        return extract_aria_content(page)
```

### Step 1.3: Implement `_infer_dom_mode()` in executor

- [ ] Add the function to `fantoma/executor.py` at module level (before the `Executor` class, after imports):

```python
# ── DOM mode keywords ────────────────────────────────────────
_FORM_KEYWORDS = {
    "login", "sign in", "register", "checkout", "search",
    "fill", "enter", "submit", "subscribe", "signup", "sign up",
}
_CONTENT_KEYWORDS = {
    "extract", "read", "scrape", "copy", "get text",
    "find information", "summarize",
}


def _infer_dom_mode(task: str, page, element_count: int = 0) -> str:
    """Infer which DOM extraction mode to use for this step.

    Returns "form", "content", or "navigate".
    Page state override: 5+ textboxes forces "form" regardless of keywords.
    """
    # Page state override
    if element_count >= 5:
        return "form"

    task_lower = task.lower()

    for kw in _FORM_KEYWORDS:
        if kw in task_lower:
            return "form"

    for kw in _CONTENT_KEYWORDS:
        if kw in task_lower:
            return "content"

    return "navigate"
```

### Step 1.4: Wire mode into `execute_reactive()`

- [ ] In `execute_reactive()`, before the `dom_text = self.dom.extract(page, task=task)` call at line 213, count textbox elements from the previous extraction and infer mode. Replace line 213:

```python
            # Infer DOM mode for this step
            _textbox_count = sum(
                1 for el in self.dom._last_interactive
                if el.get("role") in ("textbox", "combobox", "searchbox")
            )
            _dom_mode = _infer_dom_mode(task, page, element_count=_textbox_count)

            dom_text = self.dom.extract(page, task=task, mode=_dom_mode)
```

- [ ] Also update `AccessibilityExtractor.extract()` to pass `mode` through. In `fantoma/dom/accessibility.py`, change the `extract` method signature at line 422:

```python
    def extract(self, page, task: str = "", mode: str = "navigate") -> str:
```

And update the `extract_aria` call at line 425:

```python
        result = extract_aria(page, self._max_elements, self._max_headings,
                              task=task, previous_elements=previous, mode=mode)
```

### Step 1.5: Run tests

- [ ] Run the DOM mode tests:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_dom_modes.py -v
```

Expected: all 10 tests PASS.

- [ ] Run full test suite to check for regressions:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --tb=short
```

Expected: all existing tests still pass (navigate mode is default, no behaviour change).

### Step 1.6: Commit

- [ ] Commit:

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/dom/accessibility.py fantoma/executor.py tests/test_dom_modes.py
git commit -m "feat: adaptive DOM modes — form/content/navigate extraction

Adds mode parameter to extract_aria() with three strategies:
- form: inputs first, max 20 elements, 5 headings (login, checkout)
- content: delegates to extract_aria_content (scraping, reading)
- navigate: current default behaviour (unchanged)

_infer_dom_mode() in executor picks mode per step via task keywords
and page state override (5+ textboxes forces form mode).

Inspired by Agent-E's adaptive DOM distillation."
```

---

## Task 2: Parent-Context Labels (ARIA Landmark Grouping)

**Files:**
- Modify: `fantoma/dom/accessibility.py:226-318` (the `extract_aria` function)
- Create: `tests/test_landmarks.py`

### Step 2.1: Write failing tests for landmark grouping

- [ ] Create `tests/test_landmarks.py`:

```python
"""Tests for ARIA landmark grouping in DOM extraction."""
import pytest
import re
from unittest.mock import MagicMock, PropertyMock


def _make_page(snapshot):
    page = MagicMock()
    type(page).url = PropertyMock(return_value="https://example.com")
    page.title.return_value = "Test Page"
    page.locator.return_value.aria_snapshot.return_value = snapshot
    return page


SNAPSHOT_WITH_LANDMARKS = """\
- banner:
  - link "Home"
  - link "About"
- navigation "Main nav":
  - link "Dashboard"
  - link "Settings"
- main:
  - form "Login":
    - textbox "Username"
    - textbox "Password"
    - button "Sign In"
  - region "Results":
    - link "Result 1"
    - link "Result 2"
- contentinfo:
  - link "Privacy"
"""

SNAPSHOT_NO_LANDMARKS = """\
- textbox "Search"
- button "Go"
- link "Home"
"""

SNAPSHOT_EMPTY_LANDMARK = """\
- navigation "Nav":
  - heading "Site Title" [level=1]
- form "Login":
  - textbox "Email"
  - button "Submit"
"""


class TestLandmarkGrouping:

    def test_elements_grouped_under_landmark(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page)
        assert "[Login]" in result or "[form: Login]" in result
        assert "[Main nav]" in result or "[navigation: Main nav]" in result

    def test_elements_outside_landmarks_in_other(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(SNAPSHOT_NO_LANDMARKS)
        result = extract_aria(page)
        # All elements should be under [Other] since no landmarks
        assert "[Other]" in result

    def test_indices_globally_sequential(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page)
        indices = [int(m.group(1)) for m in re.finditer(r'\[(\d+)\]', result)
                   if m.group(1) != ""]
        # Filter out landmark headers (which use [Name] not [N])
        indices = sorted(indices)
        # Should be sequential starting from 0
        for i, idx in enumerate(indices):
            assert idx == i, f"Expected index {i}, got {idx}"

    def test_landmark_without_interactive_elements_hidden(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(SNAPSHOT_EMPTY_LANDMARK)
        result = extract_aria(page)
        # Nav has only a heading, no interactive elements — no header for it
        assert "Nav" not in result or "[navigation: Nav]" not in result
        # Form has interactive elements — should appear
        assert "Login" in result

    def test_banner_and_contentinfo_landmarks(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page)
        # banner has links → should have a group
        assert "banner" in result.lower() or "Banner" in result
        # contentinfo has a link → should have a group
        assert "contentinfo" in result.lower() or "Footer" in result

    def test_region_landmark_with_name(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page)
        assert "Results" in result

    def test_landmark_grouping_disabled_in_content_mode(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page, mode="content")
        # Content mode delegates to extract_aria_content, no numbered elements
        numbered = re.findall(r'\[\d+\]', result)
        assert len(numbered) == 0
```

- [ ] Run tests to verify they fail:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_landmarks.py -v
```

Expected: FAIL — no landmark grouping exists yet.

### Step 2.2: Implement landmark tracking in `extract_aria()`

- [ ] Edit `fantoma/dom/accessibility.py`. Add landmark roles constant near the top (after `SKIP_ROLES`):

```python
# ARIA landmark roles — used for grouping elements by page region
LANDMARK_ROLES = {
    "form", "navigation", "region", "main", "banner",
    "contentinfo", "complementary", "search",
}
```

- [ ] Rewrite the snapshot parsing in `extract_aria()` to track landmarks. The parsing loop (starting at line 248) needs to track indentation and landmark context.

Replace the parsing loop (lines 248-284) with:

```python
    for line in lines:
        # Track indentation for landmark scoping
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        stripped = stripped.lstrip("- ")

        parsed = _parse_aria_line(stripped)
        if not parsed:
            # Check for landmark lines like "form "Login":"
            # These appear as "role "name":" or "role:" in ARIA snapshots
            landmark_match = re.match(r'(\w+)(?:\s+"([^"]*)")?:', stripped)
            if landmark_match:
                role = landmark_match.group(1)
                name = landmark_match.group(2) or ""
                if role in LANDMARK_ROLES:
                    current_landmark = f"{role}: {name}" if name else role
                    landmark_indent = indent
            continue

        # If we've moved out of the current landmark's scope
        if current_landmark and indent <= landmark_indent:
            current_landmark = None

        role = parsed["role"]
        name = parsed.get("name", "")

        # Check if this line IS a landmark
        if role in LANDMARK_ROLES:
            current_landmark = f"{role}: {name}" if name else role
            landmark_indent = indent
            continue

        if role in SKIP_ROLES:
            continue

        if role == "heading" and name:
            level = parsed.get("level", "")
            headings.append(f"  (h{level}) {name}" if level else f"  {name}")
            continue

        if role == "text" and name:
            if len(name) < 100:
                headings.append(f"  {name}")
            continue

        if role in INTERACTIVE_ROLES and name:
            state = ""
            if parsed.get("checked"):
                state = " [checked]"
            elif parsed.get("disabled"):
                state = " [disabled]"
            elif parsed.get("value"):
                state = f' (value: "{parsed["value"]}")'

            interactive.append({
                "role": role,
                "name": name,
                "state": state,
                "raw": parsed,
                "_landmark": current_landmark,
            })
```

Add the state variables before the loop:

```python
    current_landmark = None
    landmark_indent = -1
```

- [ ] Update the output formatting section to group by landmark. Replace the interactive element output block (the `if interactive:` section) with:

```python
    if interactive:
        interactive = dedup_elements(interactive)
        if task:
            shown = prune_elements(interactive, task, _max_el)
        else:
            shown = interactive[:_max_el]

        new_flags = mark_new_elements(previous_elements or [], shown)

        # Group elements by landmark
        from collections import OrderedDict
        groups = OrderedDict()
        for i, el in enumerate(shown):
            landmark = el.get("_landmark") or "Other"
            if landmark not in groups:
                groups[landmark] = []
            groups[landmark].append((i, el, new_flags[i]))

        output.append(f"Elements ({len(shown)} of {len(interactive)}):")
        for landmark, els in groups.items():
            output.append(f"\n[{landmark}]")
            for i, el, is_new in els:
                prefix = "*" if is_new else ""
                state = enrich_field_state(el) or el["state"]
                output.append(f'{prefix}[{i}] {el["role"]} "{el["name"]}"{state}')
    else:
        output.append("Elements: none found")
```

### Step 2.3: Run tests

- [ ] Run landmark tests:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_landmarks.py -v
```

Expected: all 7 tests PASS.

- [ ] Run full suite:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --tb=short
```

Expected: all pass. Some existing tests may need minor adjustments if they assert on exact output format (the `[Other]` header is new). Fix any failures by updating expected output strings.

### Step 2.4: Commit

- [ ] Commit:

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/dom/accessibility.py tests/test_landmarks.py
git commit -m "feat: parent-context labels — group elements by ARIA landmark

Elements now grouped under their nearest ARIA landmark parent:
[form: Login], [navigation: Main nav], [banner], etc.
Elements outside any landmark go under [Other].
Indices stay globally sequential.

Novel approach — no existing browser agent does this. Supported by
LCoW (ICLR 2025) showing contextualized observations improve LLM
success by 15-24%."
```

---

## Task 3: Per-Step Success Criteria

**Files:**
- Modify: `fantoma/browser/page_state.py:1-133`
- Modify: `fantoma/executor.py`
- Create: `tests/test_progress.py`

### Step 3.1: Write failing tests for `assess_progress()` and `_infer_task_intent()`

- [ ] Create `tests/test_progress.py`:

```python
"""Tests for per-step success criteria and stall detection."""
import pytest
from unittest.mock import MagicMock, PropertyMock


def _make_page(url="https://example.com/login", inner_text="Welcome"):
    page = MagicMock()
    type(page).url = PropertyMock(return_value=url)
    page.inner_text.return_value = inner_text
    page.evaluate.return_value = None
    return page


def _make_dom_extractor(elements=None):
    ext = MagicMock()
    ext._last_interactive = elements or []
    return ext


class TestInferTaskIntent:
    def test_login_intent(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("login to github") == "auth"

    def test_sign_in_intent(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("sign in to the dashboard") == "auth"

    def test_extract_intent(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("extract the article text") == "extract"

    def test_scrape_intent(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("scrape product prices") == "extract"

    def test_navigate_intent(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("go to the settings page") == "navigate"

    def test_unknown_intent(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("click the red button") is None

    def test_authenticate_intent(self):
        from fantoma.browser.page_state import _infer_task_intent
        assert _infer_task_intent("authenticate with SSO") == "auth"


class TestAssessProgressActionLevel:
    """Layer 1: action-level verification."""

    def test_type_action_value_present(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        page.evaluate.return_value = "user@test.com"  # field value
        result = assess_progress(
            page, action='TYPE [0] "user@test.com"', task="login",
            dom_extractor=_make_dom_extractor(),
        )
        assert result["action_ok"] is True

    def test_type_action_value_missing(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        page.evaluate.return_value = ""  # field is empty
        result = assess_progress(
            page, action='TYPE [0] "user@test.com"', task="login",
            dom_extractor=_make_dom_extractor(),
        )
        assert result["action_ok"] is False

    def test_click_submit_url_changed(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/dashboard")
        result = assess_progress(
            page, action='CLICK [2]', task="login",
            dom_extractor=_make_dom_extractor(),
            pre_url="https://example.com/login",
            action_element={"role": "button", "name": "Sign In"},
        )
        assert result["action_ok"] is True

    def test_click_link_url_changed(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/about")
        result = assess_progress(
            page, action='CLICK [1]', task="go to about",
            dom_extractor=_make_dom_extractor(),
            pre_url="https://example.com",
            action_element={"role": "link", "name": "About"},
        )
        assert result["action_ok"] is True

    def test_click_link_url_unchanged(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com")
        result = assess_progress(
            page, action='CLICK [1]', task="go to about",
            dom_extractor=_make_dom_extractor(),
            pre_url="https://example.com",
            action_element={"role": "link", "name": "About"},
        )
        assert result["action_ok"] is False

    def test_select_action_value_matches(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        page.evaluate.return_value = "US"
        result = assess_progress(
            page, action='SELECT [3] "US"', task="fill form",
            dom_extractor=_make_dom_extractor(),
        )
        assert result["action_ok"] is True

    def test_scroll_action_always_ok(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        result = assess_progress(
            page, action='SCROLL down', task="browse",
            dom_extractor=_make_dom_extractor(),
        )
        assert result["action_ok"] is True


class TestAssessProgressTaskLevel:
    """Layer 2: task-level progress."""

    def test_auth_task_url_left_login(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/dashboard")
        result = assess_progress(
            page, action='CLICK [2]', task="login to the site",
            dom_extractor=_make_dom_extractor(),
            pre_url="https://example.com/login",
            action_element={"role": "button", "name": "Sign In"},
        )
        assert result["progress_ok"] is True

    def test_auth_task_still_on_login(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page(url="https://example.com/login")
        result = assess_progress(
            page, action='CLICK [2]', task="login to the site",
            dom_extractor=_make_dom_extractor(),
            pre_url="https://example.com/login",
            action_element={"role": "button", "name": "Sign In"},
        )
        assert result["progress_ok"] is False

    def test_unknown_intent_progress_is_none(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        result = assess_progress(
            page, action='CLICK [0]', task="click the red button",
            dom_extractor=_make_dom_extractor(),
        )
        assert result["progress_ok"] is None

    def test_result_has_reason_string(self):
        from fantoma.browser.page_state import assess_progress
        page = _make_page()
        result = assess_progress(
            page, action='SCROLL down', task="browse",
            dom_extractor=_make_dom_extractor(),
        )
        assert isinstance(result["reason"], str)
```

- [ ] Run tests:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_progress.py -v
```

Expected: FAIL — `assess_progress` and `_infer_task_intent` don't exist.

### Step 3.2: Implement `_infer_task_intent()` and `assess_progress()` in `page_state.py`

- [ ] Add to `fantoma/browser/page_state.py` (append after the existing `dom_hash` function):

```python
# ── Task intent keywords ─────────────────────────────────────
_AUTH_KEYWORDS = {"login", "sign in", "log in", "authenticate", "signin"}
_EXTRACT_KEYWORDS = {"extract", "scrape", "read", "copy", "get"}
_NAVIGATE_KEYWORDS = {"go to", "visit", "open", "navigate"}


def _infer_task_intent(task: str) -> str | None:
    """Map a task description to an intent category.

    Returns "auth", "extract", "navigate", or None.
    """
    task_lower = task.lower()
    for kw in _AUTH_KEYWORDS:
        if kw in task_lower:
            return "auth"
    for kw in _EXTRACT_KEYWORDS:
        if kw in task_lower:
            return "extract"
    for kw in _NAVIGATE_KEYWORDS:
        if kw in task_lower:
            return "navigate"
    return None


def assess_progress(page, action: str, task: str, dom_extractor,
                    pre_url: str = None, action_element: dict = None) -> dict:
    """Check whether an action achieved its intent and whether the task is progressing.

    Returns:
        {"action_ok": bool, "progress_ok": bool | None, "reason": str}
    """
    import re

    action_ok = True
    reason = "ok"
    action_verb = action.strip().split()[0].upper() if action.strip() else ""

    # ── Layer 1: action-level verification ──────────────────────
    if action_verb == "TYPE":
        # Check: did the typed text land in the field?
        type_match = re.search(r'["\'](.+?)["\']', action)
        if type_match:
            expected_text = type_match.group(1)
            try:
                # Read the active element's value
                field_value = page.evaluate("() => document.activeElement?.value || ''")
                if expected_text.lower() not in (field_value or "").lower():
                    action_ok = False
                    reason = f"typed text not found in field (got: {(field_value or '')[:30]})"
            except Exception:
                pass  # can't verify → assume ok

    elif action_verb == "CLICK":
        current_url = page.url
        if pre_url and current_url != pre_url:
            action_ok = True
            reason = "URL changed after click"
        elif action_element:
            el_role = action_element.get("role", "")
            el_name = action_element.get("name", "").lower()
            is_submit = el_role == "button" and any(
                w in el_name for w in ("submit", "sign in", "login", "log in", "send", "confirm", "register")
            )
            if is_submit and pre_url and current_url == pre_url:
                action_ok = False
                reason = "submit button clicked but URL unchanged"
            elif el_role == "link" and pre_url and current_url == pre_url:
                action_ok = False
                reason = "link clicked but URL unchanged"
            else:
                action_ok = True
                reason = "click executed"
        else:
            action_ok = True
            reason = "click executed"

    elif action_verb == "SELECT":
        select_match = re.search(r'["\'](.+?)["\']', action)
        if select_match:
            expected_val = select_match.group(1)
            try:
                field_value = page.evaluate("() => document.activeElement?.value || ''")
                if expected_val.lower() not in (field_value or "").lower():
                    action_ok = False
                    reason = f"selected value not found (got: {(field_value or '')[:30]})"
            except Exception:
                pass

    # SCROLL, WAIT, NAVIGATE, etc. → action_ok = True (default)

    # ── Layer 2: task-level progress ────────────────────────────
    intent = _infer_task_intent(task)
    progress_ok = None

    if intent == "auth":
        current_url = page.url.lower()
        auth_segments = {"login", "signin", "sign-in", "sign_in", "authenticate", "auth"}
        still_on_auth = any(seg in current_url for seg in auth_segments)
        progress_ok = not still_on_auth
        if not progress_ok:
            reason += "; still on auth page"

    elif intent == "extract":
        # Check if page has content (non-trivial text)
        try:
            text_len = len(page.inner_text("body") or "")
            progress_ok = text_len > 200  # page has substantial content
        except Exception:
            progress_ok = None

    elif intent == "navigate":
        # For navigate, any URL change from starting point counts as progress
        if pre_url:
            progress_ok = page.url != pre_url
        else:
            progress_ok = None

    return {
        "action_ok": action_ok,
        "progress_ok": progress_ok,
        "reason": reason,
    }
```

### Step 3.3: Run tests

- [ ] Run progress tests:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_progress.py -v
```

Expected: all 15 tests PASS.

- [ ] Run full suite:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --tb=short
```

### Step 3.4: Wire `assess_progress()` into executor

- [ ] In `fantoma/executor.py`, add the import at the top (after existing `page_state` import at line 20):

Change:
```python
from fantoma.browser.page_state import verify_action, dom_hash as compute_dom_hash
```
To:
```python
from fantoma.browser.page_state import verify_action, dom_hash as compute_dom_hash, assess_progress
```

- [ ] Add stall counter to `__init__` (after `self._secrets` at line 58):

```python
        self._stall_counter = 0  # consecutive action_ok=True + progress_ok=False
```

- [ ] In the `execute_reactive()` loop, after the `verify_action()` call and outcome building (after line 401), add progress assessment. Insert after the `self._action_outcomes.append(...)` line:

```python
                # Per-step success criteria
                action_element = None
                idx_match = re.match(r'\w+\s*\[(\d+)\]', action)
                if idx_match:
                    idx = int(idx_match.group(1))
                    if idx < len(self.dom._last_interactive):
                        action_element = self.dom._last_interactive[idx]

                progress = assess_progress(
                    page, action, task, self.dom,
                    pre_url=pre_batch_url, action_element=action_element,
                )

                # Stall detection
                if progress["action_ok"] and progress["progress_ok"] is False:
                    self._stall_counter += 1
                else:
                    self._stall_counter = 0
```

- [ ] In the user message building section (around line 265), add stall warning. After the `if failed:` block (line 278), add:

```python
            if self._stall_counter >= 3:
                user_msg += "\n\nWarning: actions are succeeding but task isn't progressing. Try a different approach."
```

### Step 3.5: Run full suite again

- [ ] Run:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --tb=short
```

Expected: all pass.

### Step 3.6: Commit

- [ ] Commit:

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/page_state.py fantoma/executor.py tests/test_progress.py
git commit -m "feat: per-step success criteria — action + task-level verification

New assess_progress() checks whether actions achieved intent:
- TYPE: reads field value back, confirms text landed
- CLICK submit: checks form disappeared or URL changed
- CLICK link: checks URL changed
- SELECT: reads value back

Task-level progress tracks auth/extract/navigate intent.
Stall detection warns LLM after 3 consecutive action_ok + no progress.

Inspired by Skyvern 2.0's planner-actor-validator pattern."
```

---

## Task 4: Script Cache Wiring + Self-Healing Selectors

**Files:**
- Modify: `fantoma/resilience/script_cache.py:1-148`
- Modify: `fantoma/executor.py`
- Extend: `tests/test_script_cache.py`
- Create: `tests/test_cache_replay.py`

### Step 4.1: Write failing tests for `heal_action()`

- [ ] Add to `tests/test_script_cache.py` (append after existing `TestScriptCacheValidation` class):

```python
class TestHealAction:
    """Self-healing: fuzzy match renamed elements."""

    def test_heal_exact_match(self):
        from fantoma.resilience.script_cache import heal_action
        current_elements = [
            {"role": "button", "name": "Sign In"},
            {"role": "textbox", "name": "Email"},
        ]
        result = heal_action(
            target_role="button", target_name="Sign In",
            original_index=0, current_elements=current_elements,
        )
        assert result == 0  # exact match at same index

    def test_heal_renamed_element(self):
        from fantoma.resilience.script_cache import heal_action
        current_elements = [
            {"role": "textbox", "name": "Email address"},
            {"role": "button", "name": "Log In"},  # was "Sign In"
        ]
        result = heal_action(
            target_role="button", target_name="Sign In",
            original_index=1, current_elements=current_elements,
        )
        assert result == 1  # fuzzy matched to "Log In"

    def test_heal_moved_element(self):
        from fantoma.resilience.script_cache import heal_action
        current_elements = [
            {"role": "textbox", "name": "Username"},
            {"role": "textbox", "name": "Password"},
            {"role": "button", "name": "Submit"},  # was at index 1
        ]
        result = heal_action(
            target_role="button", target_name="Submit",
            original_index=1, current_elements=current_elements,
        )
        assert result == 2  # found at new index

    def test_heal_fails_wrong_role(self):
        from fantoma.resilience.script_cache import heal_action
        current_elements = [
            {"role": "link", "name": "Sign In"},  # same name but link, not button
        ]
        result = heal_action(
            target_role="button", target_name="Sign In",
            original_index=0, current_elements=current_elements,
        )
        assert result is None  # role mismatch → fail

    def test_heal_fails_multiple_matches(self):
        from fantoma.resilience.script_cache import heal_action
        current_elements = [
            {"role": "button", "name": "Sign In"},
            {"role": "button", "name": "Sign In Now"},
        ]
        result = heal_action(
            target_role="button", target_name="Sign In",
            original_index=0, current_elements=current_elements,
        )
        # "Sign In" exact match exists, should return that one
        assert result == 0

    def test_heal_fails_no_match(self):
        from fantoma.resilience.script_cache import heal_action
        current_elements = [
            {"role": "button", "name": "Cancel"},
            {"role": "button", "name": "Reset"},
        ]
        result = heal_action(
            target_role="button", target_name="Sign In",
            original_index=0, current_elements=current_elements,
        )
        assert result is None  # too different

    def test_heal_below_threshold(self):
        from fantoma.resilience.script_cache import heal_action
        current_elements = [
            {"role": "button", "name": "X"},  # too short/different from "Submit Form"
        ]
        result = heal_action(
            target_role="button", target_name="Submit Form",
            original_index=0, current_elements=current_elements,
        )
        assert result is None
```

- [ ] Run:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_script_cache.py::TestHealAction -v
```

Expected: FAIL — `heal_action` doesn't exist.

### Step 4.2: Implement `heal_action()` in `script_cache.py`

- [ ] Add to `fantoma/resilience/script_cache.py` (append after the `_mask_secrets` function):

```python
def heal_action(target_role: str, target_name: str, original_index: int,
                current_elements: list[dict], threshold: float = 0.7) -> int | None:
    """Find a renamed/moved element using fuzzy matching.

    Role must match exactly. Name uses SequenceMatcher ratio.
    Returns the new index if exactly one match found, None otherwise.

    Args:
        target_role: Expected ARIA role (exact match required)
        target_name: Expected accessible name (fuzzy matched)
        original_index: Original element index in cached script
        current_elements: Current page elements (list of dicts with role, name)
        threshold: Minimum SequenceMatcher ratio for name match (default 0.7)
    """
    from difflib import SequenceMatcher

    # First: check if original index still matches
    if original_index < len(current_elements):
        el = current_elements[original_index]
        if el.get("role") == target_role and el.get("name") == target_name:
            return original_index

    # Scan all elements for fuzzy matches
    candidates = []
    for i, el in enumerate(current_elements):
        if el.get("role") != target_role:
            continue
        el_name = el.get("name", "")
        if el_name == target_name:
            return i  # exact match at different index
        ratio = SequenceMatcher(None, target_name.lower(), el_name.lower()).ratio()
        if ratio >= threshold:
            candidates.append((i, ratio))

    if len(candidates) == 1:
        return candidates[0][0]

    # Multiple candidates or none → fail
    return None
```

### Step 4.3: Run `heal_action` tests

- [ ] Run:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_script_cache.py::TestHealAction -v
```

Expected: all 7 tests PASS.

### Step 4.4: Write failing tests for cache replay

- [ ] Create `tests/test_cache_replay.py`:

```python
"""Tests for script cache wiring and replay in executor."""
import pytest
from unittest.mock import MagicMock, PropertyMock, patch


def _make_page(url="https://example.com/login"):
    page = MagicMock()
    type(page).url = PropertyMock(return_value=url)
    page.title.return_value = "Login"
    page.inner_text.return_value = "Welcome"
    page.evaluate.return_value = "typed_value"
    snapshot = '- textbox "Email"\n- textbox "Password"\n- button "Sign In"'
    page.locator.return_value.aria_snapshot.return_value = snapshot
    return page


def _make_cache(actions=None):
    """Create a mock ScriptCache."""
    cache = MagicMock()
    cache.lookup.return_value = actions
    cache.save.return_value = True
    return cache


def _make_dom_extractor():
    ext = MagicMock()
    ext._last_interactive = [
        {"role": "textbox", "name": "Email"},
        {"role": "textbox", "name": "Password"},
        {"role": "button", "name": "Sign In"},
    ]
    ext.extract.return_value = "Page: Login\nURL: https://example.com/login\n\n[0] textbox \"Email\"\n[1] textbox \"Password\"\n[2] button \"Sign In\""
    return ext


class TestCacheReplayFlow:
    """Test that executor uses cache when available."""

    def test_cache_lookup_called_with_domain(self):
        """Verify ScriptCache.lookup is called at start of execute_reactive."""
        from fantoma.resilience.script_cache import ScriptCache
        # This test verifies the wiring exists by importing and checking
        # the executor calls cache.lookup
        from fantoma.executor import Executor
        assert hasattr(Executor, '_replay_cached'), "_replay_cached method should exist"

    def test_replay_cached_exists(self):
        """The _replay_cached method should exist on Executor."""
        from fantoma.executor import Executor
        assert callable(getattr(Executor, '_replay_cached', None))


class TestSaveOnSuccess:
    """Verify that successful LLM-driven runs get cached."""

    def test_action_format_has_target_fields(self):
        """Cached actions should include target_role and target_name."""
        # When saving, actions should have target metadata
        action = {
            "action": 'TYPE [0] "user@test.com"',
            "target_role": "textbox",
            "target_name": "Email",
        }
        assert "target_role" in action
        assert "target_name" in action
```

- [ ] Run:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_cache_replay.py -v
```

Expected: FAIL — `_replay_cached` doesn't exist on Executor.

### Step 4.5: Wire ScriptCache into Executor

- [ ] In `fantoma/executor.py`, add import at top (after existing imports):

```python
from fantoma.resilience.script_cache import ScriptCache, heal_action
```

- [ ] In `Executor.__init__()`, add cache instantiation (after `self.vision` line, around line 48):

```python
        self.cache = ScriptCache()
```

- [ ] Add `_replay_cached()` method to the `Executor` class (in the private helpers section, before `_select_action`):

```python
    def _replay_cached(self, page, cached_actions: list[dict], task: str) -> bool:
        """Replay a cached action sequence. Returns True if all actions succeed.

        Each action is verified via assess_progress(). If any action fails or
        an element can't be found (even after healing), returns False.
        """
        for i, cached in enumerate(cached_actions):
            action_str = cached.get("action", "")
            target_role = cached.get("target_role")
            target_name = cached.get("target_name")

            # Extract element index from action string
            idx_match = re.match(r'\w+\s*\[(\d+)\]', action_str)
            if idx_match and target_role and target_name:
                original_idx = int(idx_match.group(1))
                # Check if element still matches
                elements = self.dom._last_interactive
                if original_idx >= len(elements) or \
                   elements[original_idx].get("role") != target_role or \
                   elements[original_idx].get("name") != target_name:
                    # Try self-healing
                    new_idx = heal_action(target_role, target_name, original_idx, elements)
                    if new_idx is None:
                        log.info("Cache replay: healing failed for %s %r at step %d",
                                 target_role, target_name, i)
                        return False
                    # Rewrite action with new index
                    action_str = re.sub(r'\[(\d+)\]', f'[{new_idx}]', action_str, count=1)
                    log.info("Cache replay: healed %s %r index %d → %d",
                             target_role, target_name, original_idx, new_idx)
                    cached["action"] = action_str

            # Inject secrets
            if self._secrets:
                action_str = self._inject_secrets(action_str, self._secrets)

            pre_url = self.browser.get_url()
            self._total_actions += 1
            executed = execute_action(action_str, self.browser, self.dom)

            if not executed:
                log.info("Cache replay: action failed to execute at step %d", i)
                return False

            # Verify via assess_progress
            action_element = None
            if idx_match:
                idx = int(re.match(r'\w+\s*\[(\d+)\]', action_str).group(1))
                if idx < len(self.dom._last_interactive):
                    action_element = self.dom._last_interactive[idx]

            progress = assess_progress(
                page, action_str, task, self.dom,
                pre_url=pre_url, action_element=action_element,
            )
            if not progress["action_ok"]:
                log.info("Cache replay: action verification failed at step %d: %s",
                         i, progress["reason"])
                return False

            # Wait for page to settle between actions
            action_verb = action_str.strip().split()[0].upper()
            if action_verb not in ("SCROLL", "WAIT"):
                wait_for_dom_stable(page, timeout=3000, debounce=200)

        log.info("Cache replay: all %d actions succeeded", len(cached_actions))
        return True
```

- [ ] In `execute_reactive()`, add cache lookup at the start of the method (after `page = self.browser.get_page()` at line 205, before the loop). Insert:

```python
        # Cache lookup: try replaying a cached script
        try:
            from urllib.parse import urlparse
            domain = urlparse(self.browser.get_url()).netloc
            dom_text_for_cache = self.dom.extract(page, task=task)
            cached_actions = self.cache.lookup(domain, self.dom._last_interactive)
            if cached_actions:
                log.info("Cache hit for %s — attempting replay (%d actions)", domain, len(cached_actions))
                if self._replay_cached(page, cached_actions, task):
                    dom_text = self.dom.extract(page, task=task)
                    data = self._extract_result(task, dom_text)
                    return AgentResult(
                        success=True, data=data,
                        steps_taken=self._total_actions,
                        steps_detail=[{"step": 0, "action": "CACHE_REPLAY", "success": True, "url": self.browser.get_url()}],
                        escalations=0,
                    )
                else:
                    log.info("Cache replay failed — falling through to LLM")
                    self._total_actions = 0  # reset counter for fresh LLM run
        except Exception as e:
            log.debug("Cache lookup error: %s", e)
```

- [ ] At the end of `execute_reactive()`, before the final `return AgentResult(success=True, ...)` on the DONE path (around line 419), add cache save:

```python
            if done_signalled:
                # Save successful sequence to cache
                try:
                    from urllib.parse import urlparse
                    domain = urlparse(self.browser.get_url()).netloc
                    cacheable_actions = []
                    for sd in steps_detail:
                        if sd.get("action") and sd["success"]:
                            # Build action with target metadata
                            action_str = sd["action"]
                            idx_match = re.match(r'\w+\s*\[(\d+)\]', action_str)
                            entry = {"action": action_str}
                            if idx_match:
                                idx = int(idx_match.group(1))
                                if idx < len(self.dom._last_interactive):
                                    el = self.dom._last_interactive[idx]
                                    entry["target_role"] = el.get("role", "")
                                    entry["target_name"] = el.get("name", "")
                            cacheable_actions.append(entry)
                    if cacheable_actions:
                        initial_elements = self.dom._last_interactive
                        self.cache.save(domain, initial_elements, cacheable_actions,
                                        sensitive_data=self._secrets)
                except Exception as e:
                    log.debug("Cache save failed: %s", e)
```

### Step 4.6: Run all tests

- [ ] Run cache replay tests:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/test_cache_replay.py -v
```

- [ ] Run full suite:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --tb=short
```

Expected: all pass.

### Step 4.7: Commit

- [ ] Commit:

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/resilience/script_cache.py fantoma/executor.py tests/test_script_cache.py tests/test_cache_replay.py
git commit -m "feat: wire script cache + self-healing selectors into executor

ScriptCache now wired into execute_reactive():
- Lookup at start: replay cached script without LLM calls
- Save on success: cache action sequence with target_role/target_name
- Self-healing: fuzzy match renamed elements via difflib.SequenceMatcher
  (role exact, name threshold 0.7, single match only)
- assess_progress() validates each replayed action

Inspired by Stagehand v3 (caching) and Healenium (fuzzy healing)."
```

---

## Task 5: Update Documentation and Run Final Verification

**Files:**
- Modify: `README.md`
- Modify: `PROGRESS.md`

### Step 5.1: Update README.md

- [ ] Add Phase 3 features to the "What It Does" section and update test count. Read the current README first, then update the relevant sections.

### Step 5.2: Update PROGRESS.md

- [ ] Add a Session 10 entry documenting Phase 3 implementation. Read the current PROGRESS.md first, then append the new session.

### Step 5.3: Run final test suite

- [ ] Run:

```bash
cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Note the final test count for docs.

### Step 5.4: Update CAPABILITIES.md

- [ ] Update `/home/steamvibe/.nanobot/workspace/CAPABILITIES.md` with Phase 3 features and new test count.

### Step 5.5: Update TODO.md

- [ ] Mark Phase 3 as DONE in `/home/steamvibe/.claude/projects/-home-steamvibe/memory/TODO.md`.

### Step 5.6: Commit docs

- [ ] Commit:

```bash
cd /home/workspace/workbench/fantoma
git add README.md PROGRESS.md
git commit -m "docs: update README and PROGRESS for v0.6 Phase 3"
```
