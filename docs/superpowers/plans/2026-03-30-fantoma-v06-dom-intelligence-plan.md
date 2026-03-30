# Fantoma v0.6.0 Phase 2 — Code-First DOM Intelligence

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Five code-only features that improve what the LLM sees and how reliably it communicates: structured output, DOM deduplication, iframe extraction, adaptive waits, and inline field state. Zero additional LLM calls.

**Architecture:** Five independent features. Structured output changes the LLM→code interface (client.py + action_parser.py). DOM dedup and field state enrich the accessibility extractor. Iframe extraction adds a new module that feeds into the extractor. Adaptive waits extend the existing observer.py. Each feature is testable in isolation.

**Tech Stack:** Python 3.10+, Playwright, httpx, json (stdlib), pytest

**Validated against:** browser-use source (GitHub), Playwright docs (frames API, aria_snapshot), MutationObserver debounce patterns (MDN, Playwright issues)

**Branch:** `feat/v0.6-navigation-intelligence` (continues from Phase 1)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `fantoma/llm/client.py` | Add `response_format` support to `chat()` |
| `fantoma/llm/structured.py` | **New** — JSON schema for action output, parse/validate |
| `fantoma/action_parser.py` | Add structured JSON fallback path |
| `fantoma/dom/accessibility.py` | DOM dedup + inline field state |
| `fantoma/dom/frames.py` | **New** — iframe ARIA extraction and merge |
| `fantoma/browser/observer.py` | Add `wait_for_dom_stable()` |
| `fantoma/executor.py` | Wire structured output, frames, adaptive waits |
| `tests/test_structured.py` | **New** — structured output parsing |
| `tests/test_dedup.py` | **New** — DOM deduplication |
| `tests/test_frames.py` | **New** — iframe extraction |
| `tests/test_adaptive_wait.py` | **New** — debounced DOM stability wait |
| `tests/test_field_state.py` | **New** — inline field state |

---

### Task 1: Structured LLM Output — `fantoma/llm/structured.py`

Replaces free-text action parsing with JSON schema. The LLM returns `{"action": "CLICK", "index": 3}` instead of `CLICK [3]`. llama-server supports `response_format` with JSON schema. Falls back to text parsing if JSON fails (backward-compatible).

**Files:**
- Create: `fantoma/llm/structured.py`
- Create: `tests/test_structured.py`
- Modify: `fantoma/llm/client.py`
- Modify: `fantoma/action_parser.py`
- Modify: `fantoma/executor.py`

- [ ] **Step 1: Write failing tests for structured output**

Create `tests/test_structured.py`:

```python
"""Tests for structured LLM action output — JSON schema parsing."""
import json
import pytest


class TestActionSchema:
    def test_schema_is_valid_json_schema(self):
        from fantoma.llm.structured import ACTION_SCHEMA
        assert "type" in ACTION_SCHEMA
        assert ACTION_SCHEMA["type"] == "object"
        assert "actions" in ACTION_SCHEMA["properties"]

    def test_schema_has_required_fields(self):
        from fantoma.llm.structured import ACTION_SCHEMA
        assert "required" in ACTION_SCHEMA
        assert "actions" in ACTION_SCHEMA["required"]


class TestParseStructured:
    def test_parse_click(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "CLICK", "index": 3}]}'
        result = parse_structured(raw)
        assert result == ["CLICK [3]"]

    def test_parse_type(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "TYPE", "index": 0, "text": "hello@test.com"}]}'
        result = parse_structured(raw)
        assert result == ['TYPE [0] "hello@test.com"']

    def test_parse_multiple_actions(self):
        from fantoma.llm.structured import parse_structured
        raw = json.dumps({"actions": [
            {"action": "TYPE", "index": 1, "text": "user@test.com"},
            {"action": "TYPE", "index": 2, "text": "password123"},
            {"action": "CLICK", "index": 3},
        ]})
        result = parse_structured(raw)
        assert len(result) == 3
        assert result[0] == 'TYPE [1] "user@test.com"'
        assert result[2] == "CLICK [3]"

    def test_parse_navigate(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "NAVIGATE", "url": "https://example.com"}]}'
        result = parse_structured(raw)
        assert result == ["NAVIGATE https://example.com"]

    def test_parse_scroll(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "SCROLL", "direction": "down"}]}'
        result = parse_structured(raw)
        assert result == ["SCROLL down"]

    def test_parse_done(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "DONE"}]}'
        result = parse_structured(raw)
        assert result == ["DONE"]

    def test_parse_press(self):
        from fantoma.llm.structured import parse_structured
        raw = '{"actions": [{"action": "PRESS", "key": "Enter"}]}'
        result = parse_structured(raw)
        assert result == ["PRESS Enter"]

    def test_invalid_json_returns_none(self):
        from fantoma.llm.structured import parse_structured
        result = parse_structured("not json at all")
        assert result is None

    def test_missing_actions_key_returns_none(self):
        from fantoma.llm.structured import parse_structured
        result = parse_structured('{"thinking": "hmm"}')
        assert result is None

    def test_caps_at_five_actions(self):
        from fantoma.llm.structured import parse_structured
        raw = json.dumps({"actions": [{"action": "SCROLL", "direction": "down"}] * 10})
        result = parse_structured(raw)
        assert len(result) == 5

    def test_stops_at_done(self):
        from fantoma.llm.structured import parse_structured
        raw = json.dumps({"actions": [
            {"action": "CLICK", "index": 1},
            {"action": "DONE"},
            {"action": "CLICK", "index": 2},
        ]})
        result = parse_structured(raw)
        assert len(result) == 2
        assert result[1] == "DONE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_structured.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fantoma.llm.structured'`

- [ ] **Step 3: Implement structured.py**

Create `fantoma/llm/structured.py`:

```python
"""Structured LLM output — JSON schema for action responses.

Defines the JSON schema sent to llama-server via response_format,
and parses the structured JSON response into action strings that
the existing action_parser.py can execute.

Falls back gracefully: if JSON parsing fails, returns None so the
caller can fall back to text-based parsing.
"""
import json
import logging

log = logging.getLogger("fantoma.structured")

# JSON schema for the LLM's action response.
# llama-server accepts this via response_format.type = "json_schema".
ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["CLICK", "TYPE", "SELECT", "SCROLL",
                                 "NAVIGATE", "PRESS", "WAIT", "DONE",
                                 "SEARCH_PAGE", "FIND"],
                    },
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                    "url": {"type": "string"},
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "key": {"type": "string"},
                },
                "required": ["action"],
            },
            "minItems": 1,
            "maxItems": 5,
        },
    },
    "required": ["actions"],
}

# Sequence terminators — stop processing after these
_TERMINATORS = {"NAVIGATE", "DONE"}
_MAX_ACTIONS = 5


def parse_structured(raw: str) -> list[str] | None:
    """Parse a structured JSON action response into action strings.

    Returns a list of action strings like ["CLICK [3]", "TYPE [0] \"hello\""],
    or None if the response is not valid structured JSON.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    actions_list = data.get("actions")
    if not actions_list or not isinstance(actions_list, list):
        return None

    result = []
    for entry in actions_list[:_MAX_ACTIONS]:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action", "").upper()
        if not action:
            continue

        if action == "CLICK":
            idx = entry.get("index", 0)
            result.append(f"CLICK [{idx}]")
        elif action == "TYPE":
            idx = entry.get("index", 0)
            text = entry.get("text", "")
            result.append(f'TYPE [{idx}] "{text}"')
        elif action == "SELECT":
            idx = entry.get("index", 0)
            text = entry.get("text", "")
            result.append(f'SELECT [{idx}] "{text}"')
        elif action == "SCROLL":
            direction = entry.get("direction", "down")
            result.append(f"SCROLL {direction}")
        elif action == "NAVIGATE":
            url = entry.get("url", "")
            result.append(f"NAVIGATE {url}")
        elif action == "PRESS":
            key = entry.get("key", "Enter")
            result.append(f"PRESS {key}")
        elif action == "SEARCH_PAGE":
            text = entry.get("text", "")
            result.append(f'SEARCH_PAGE "{text}"')
        elif action == "FIND":
            text = entry.get("text", "")
            result.append(f'FIND "{text}"')
        elif action in ("WAIT", "DONE"):
            result.append(action)
        else:
            continue

        if action in _TERMINATORS:
            break

    return result if result else None


def get_response_format() -> dict:
    """Return the response_format dict for the LLM API call.

    Compatible with llama-server and OpenAI-compatible endpoints
    that support response_format.type = "json_schema".
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "browser_actions",
            "strict": True,
            "schema": ACTION_SCHEMA,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_structured.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Add response_format support to LLMClient.chat()**

In `fantoma/llm/client.py`, add a `response_format` parameter to `chat()`:

Change the signature from:
```python
def chat(self, messages, temperature=0.3, max_tokens=2048) -> str:
```
To:
```python
def chat(self, messages, temperature=0.3, max_tokens=2048, response_format=None) -> str:
```

In the payload construction, after `"max_tokens": max_tokens,` add:
```python
        if response_format:
            payload["response_format"] = response_format
```

- [ ] **Step 6: Wire structured output into executor.py**

In `fantoma/executor.py`, in `execute_reactive()`, update the LLM call block. After the `raw = self.llm.chat(...)` call, add structured parsing before text parsing:

```python
            # Try structured output first, fall back to text parsing
            from fantoma.llm.structured import parse_structured, get_response_format

            raw = self.llm.chat(
                [{"role": "system", "content": REACTIVE_SYSTEM},
                 {"role": "user", "content": user_msg}],
                max_tokens=300,
                response_format=get_response_format(),
            )
            raw = (raw or "").strip()
            if not raw:
                log.warning("Step %d: LLM returned empty action", step_num)
                continue

            # Parse: try structured JSON first, fall back to text
            actions_batch = parse_structured(raw)
            if actions_batch is None:
                actions_batch = parse_actions(raw, max_actions=5)
```

- [ ] **Step 7: Run all tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/full_signup_test.py --ignore=tests/live_reddit_test.py --ignore=tests/real_signup_test.py --ignore=tests/real_site_test.py --ignore=tests/scenario_test_deepseek.py`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/llm/structured.py fantoma/llm/client.py fantoma/executor.py tests/test_structured.py
git commit -m "feat: structured JSON output — schema-constrained LLM responses with text fallback"
```

---

### Task 2: DOM Element Deduplication — `fantoma/dom/accessibility.py`

Remove duplicate interactive elements with identical (role, name) tuples. Sites often repeat the same link/button in nav, footer, and main content. Dedup before pruning so the LLM sees unique elements only.

**Files:**
- Create: `tests/test_dedup.py`
- Modify: `fantoma/dom/accessibility.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_dedup.py`:

```python
"""Tests for DOM element deduplication."""
import pytest


def _el(role, name, state=""):
    return {"role": role, "name": name, "state": state, "raw": {}}


class TestDedupElements:
    def test_removes_exact_duplicates(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("link", "Home"),
            _el("button", "Search"),
            _el("link", "Home"),  # duplicate
            _el("link", "Home"),  # duplicate
        ]
        result = dedup_elements(elements)
        assert len(result) == 2
        names = [e["name"] for e in result]
        assert names.count("Home") == 1

    def test_keeps_different_roles_same_name(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("link", "Login"),
            _el("button", "Login"),
        ]
        result = dedup_elements(elements)
        assert len(result) == 2

    def test_keeps_different_names_same_role(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("link", "Home"),
            _el("link", "About"),
        ]
        result = dedup_elements(elements)
        assert len(result) == 2

    def test_preserves_order_first_seen(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("link", "A"),
            _el("link", "B"),
            _el("link", "A"),  # duplicate
            _el("link", "C"),
        ]
        result = dedup_elements(elements)
        names = [e["name"] for e in result]
        assert names == ["A", "B", "C"]

    def test_empty_list(self):
        from fantoma.dom.accessibility import dedup_elements
        assert dedup_elements([]) == []

    def test_no_duplicates(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [_el("link", "A"), _el("button", "B"), _el("textbox", "C")]
        result = dedup_elements(elements)
        assert len(result) == 3

    def test_textbox_duplicates_kept_if_different_state(self):
        from fantoma.dom.accessibility import dedup_elements
        elements = [
            _el("textbox", "Email", state=' (value: "user@test.com")'),
            _el("textbox", "Email", state=""),
        ]
        # Textboxes with the same name but different state are likely different fields
        # (e.g., login form vs search bar). Keep both.
        result = dedup_elements(elements)
        assert len(result) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_dedup.py -v`
Expected: FAIL — `ImportError: cannot import name 'dedup_elements'`

- [ ] **Step 3: Implement dedup_elements**

Add to `fantoma/dom/accessibility.py`, after `mark_new_elements()`:

```python
def dedup_elements(elements: list[dict]) -> list[dict]:
    """Remove duplicate interactive elements by (role, name, state) tuple.

    Keeps the first occurrence. Sites repeat the same link/button in nav,
    footer, and main content — this removes the noise.

    Textboxes with the same name but different state (value) are kept
    as separate fields (e.g., two "Email" fields on different forms).
    """
    seen = set()
    result = []
    for el in elements:
        key = (el.get("role", ""), el.get("name", ""), el.get("state", ""))
        if key not in seen:
            seen.add(key)
            result.append(el)
    return result
```

Then wire it into `extract_aria()` — call `dedup_elements()` on the `interactive` list BEFORE pruning. In the `if interactive:` block, add dedup before the pruning step:

```python
    if interactive:
        interactive = dedup_elements(interactive)
        if task:
            shown = prune_elements(interactive, task, _max_el)
        else:
            shown = interactive[:_max_el]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_dedup.py tests/test_pruning.py tests/test_dom_extractor.py -v`
Expected: All tests PASS (dedup + no regressions)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/dom/accessibility.py tests/test_dedup.py
git commit -m "feat: DOM element deduplication — remove repeated nav/footer elements"
```

---

### Task 3: Iframe ARIA Extraction — `fantoma/dom/frames.py`

Enumerate all iframes on the page, extract ARIA snapshots from each, merge into the main element list. Payment forms, embedded logins, and consent dialogs live inside iframes — Fantoma was blind to these.

**Files:**
- Create: `fantoma/dom/frames.py`
- Create: `tests/test_frames.py`
- Modify: `fantoma/dom/accessibility.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_frames.py`:

```python
"""Tests for iframe ARIA extraction and merge."""
import pytest
from unittest.mock import MagicMock, PropertyMock


def _make_frame(url="https://example.com/iframe", name="login-frame",
                snapshot="- textbox \"Email\"\n- button \"Submit\"",
                is_main=False):
    """Create a mock Playwright Frame."""
    frame = MagicMock()
    type(frame).url = PropertyMock(return_value=url)
    type(frame).name = PropertyMock(return_value=name)
    frame.locator.return_value.aria_snapshot.return_value = snapshot
    frame.parent_frame = None if is_main else MagicMock()
    return frame


def _make_page_with_frames(frames):
    """Create a mock page with frame list."""
    page = MagicMock()
    main_frame = _make_frame(url="https://example.com", name="", is_main=True,
                             snapshot="- button \"Login\"\n- link \"Home\"")
    page.main_frame = main_frame
    page.frames = [main_frame] + frames
    return page


class TestExtractFrameElements:
    def test_extracts_from_single_iframe(self):
        from fantoma.dom.frames import extract_frame_elements
        frame = _make_frame(snapshot="- textbox \"Card Number\"\n- button \"Pay\"")
        result = extract_frame_elements(frame)
        assert len(result) == 2
        assert result[0]["role"] == "textbox"
        assert result[0]["name"] == "Card Number"
        assert result[0]["_frame"] == "login-frame"

    def test_empty_snapshot_returns_empty(self):
        from fantoma.dom.frames import extract_frame_elements
        frame = _make_frame(snapshot="")
        result = extract_frame_elements(frame)
        assert result == []

    def test_snapshot_failure_returns_empty(self):
        from fantoma.dom.frames import extract_frame_elements
        frame = MagicMock()
        type(frame).name = PropertyMock(return_value="broken")
        frame.locator.return_value.aria_snapshot.side_effect = Exception("Detached")
        result = extract_frame_elements(frame)
        assert result == []


class TestCollectAllFrameElements:
    def test_collects_from_all_child_frames(self):
        from fantoma.dom.frames import collect_all_frame_elements
        f1 = _make_frame(name="frame1", snapshot="- textbox \"Email\"")
        f2 = _make_frame(name="frame2", snapshot="- button \"Pay\"")
        page = _make_page_with_frames([f1, f2])
        result = collect_all_frame_elements(page)
        assert len(result) == 2
        names = [e["name"] for e in result]
        assert "Email" in names
        assert "Pay" in names

    def test_skips_main_frame(self):
        from fantoma.dom.frames import collect_all_frame_elements
        page = _make_page_with_frames([])
        result = collect_all_frame_elements(page)
        assert result == []

    def test_caps_at_max_frames(self):
        from fantoma.dom.frames import collect_all_frame_elements
        frames = [_make_frame(name=f"f{i}", snapshot=f'- button "Btn{i}"') for i in range(20)]
        page = _make_page_with_frames(frames)
        result = collect_all_frame_elements(page, max_frames=5)
        # Should process at most 5 frames
        assert len(result) <= 5

    def test_skips_about_blank_frames(self):
        from fantoma.dom.frames import collect_all_frame_elements
        blank = _make_frame(url="about:blank", name="blank")
        real = _make_frame(name="real", snapshot="- button \"OK\"")
        page = _make_page_with_frames([blank, real])
        result = collect_all_frame_elements(page)
        assert len(result) == 1
        assert result[0]["name"] == "OK"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_frames.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fantoma.dom.frames'`

- [ ] **Step 3: Implement frames.py**

Create `fantoma/dom/frames.py`:

```python
"""Iframe ARIA extraction — find and extract elements from iframes.

Enumerates child frames on the page, extracts ARIA snapshots from each,
and returns interactive elements tagged with their source frame.

Payment forms, embedded logins, and consent dialogs live inside iframes.
Without this, Fantoma is blind to them.

Limitations:
- Closed shadow DOM iframes are not accessible (Playwright limitation).
- Cross-origin iframes work in Chromium (CDP level access) but may fail in Firefox.
- Max 5 frames processed by default to avoid slowdowns on ad-heavy pages.
"""
import logging
import re

log = logging.getLogger("fantoma.frames")

# ARIA roles that represent interactive elements (same as accessibility.py)
_INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "searchbox",
    "checkbox", "radio", "slider", "switch", "tab",
    "menuitem", "option", "spinbutton",
}

_MAX_FRAMES = 5
_SKIP_URLS = {"about:blank", "", "data:,", "about:srcdoc"}


def extract_frame_elements(frame) -> list[dict]:
    """Extract interactive elements from a single frame via ARIA snapshot.

    Returns list of element dicts with role, name, state, and _frame (frame name).
    """
    try:
        snapshot = frame.locator("body").aria_snapshot()
    except Exception as e:
        log.debug("Frame ARIA snapshot failed for '%s': %s", frame.name, e)
        return []

    if not snapshot or not snapshot.strip():
        return []

    frame_name = frame.name or frame.url.split("/")[-1][:20]
    elements = []

    for line in snapshot.split("\n"):
        line = line.strip().lstrip("- ")
        if not line:
            continue

        match = re.match(r'(\w+)\s*"([^"]*)"(?:\s*\[(.+?)\])?', line)
        if not match:
            continue

        role = match.group(1)
        name = match.group(2)
        if role not in _INTERACTIVE_ROLES or not name:
            continue

        state = ""
        attrs_str = match.group(3) or ""
        if attrs_str:
            if "checked" in attrs_str:
                state = " [checked]"
            elif "disabled" in attrs_str:
                state = " [disabled]"

        elements.append({
            "role": role,
            "name": name,
            "state": state,
            "raw": {},
            "_frame": frame_name,
        })

    return elements


def collect_all_frame_elements(page, max_frames: int = _MAX_FRAMES) -> list[dict]:
    """Collect interactive elements from all child iframes on the page.

    Skips the main frame (already extracted by AccessibilityExtractor).
    Skips about:blank and empty frames.
    Caps at max_frames to avoid slowdowns on ad-heavy pages.

    Returns list of element dicts tagged with _frame name.
    """
    main_frame = page.main_frame
    elements = []
    frames_processed = 0

    for frame in page.frames:
        if frame == main_frame:
            continue
        if frame.url in _SKIP_URLS:
            continue
        if frames_processed >= max_frames:
            break

        frame_elements = extract_frame_elements(frame)
        if frame_elements:
            elements.extend(frame_elements)
            frames_processed += 1
            log.info("Frame '%s': %d elements", frame.name or frame.url[:30], len(frame_elements))

    return elements
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_frames.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Wire frames into AccessibilityExtractor.extract()**

In `fantoma/dom/accessibility.py`, in `AccessibilityExtractor.extract()`, after extracting main page elements and before dedup/pruning, merge iframe elements:

```python
    def extract(self, page, task: str = "") -> str:
        previous = list(self._last_interactive)
        result = extract_aria(page, self._max_elements, self._max_headings,
                              task=task, previous_elements=previous)
        if not result or "Elements: none found" in result:
            # ... existing fallback code ...

        self._last_interactive = self._parse_interactive_from_output(result)
        if self._last_interactive:
            self._last_interactive = self._filter_occluded(page, self._last_interactive)

        # Merge iframe elements into the element list
        from fantoma.dom.frames import collect_all_frame_elements
        iframe_elements = collect_all_frame_elements(page)
        if iframe_elements:
            self._last_interactive.extend(iframe_elements)

        return result
```

Note: iframe elements are added to `_last_interactive` for click/type access via `get_element_by_index`, but they appear in the ARIA output only if we also modify `extract_aria()` to include them. For now, just extending the internal list is sufficient — the LLM can reference them by index. In a future iteration, the iframe elements can be appended to the output text.

Actually, for the LLM to see them, we need to append them to the output. Add after the `return result` line in `extract()`:

```python
        # Append iframe elements to output
        if iframe_elements:
            lines = result.split("\n")
            # Find the "Elements (N of M):" line and update count
            iframe_section = [f"\nIframe elements ({len(iframe_elements)}):"]
            base_idx = len(self._last_interactive) - len(iframe_elements)
            for i, el in enumerate(iframe_elements):
                frame_tag = f" [{el['_frame']}]" if el.get("_frame") else ""
                iframe_section.append(
                    f'[{base_idx + i}] {el["role"]} "{el["name"]}"{el["state"]}{frame_tag}'
                )
            result = result + "\n".join(iframe_section)

        return result
```

- [ ] **Step 6: Update get_element_by_index for iframe elements**

In `AccessibilityExtractor.get_element_by_index()`, add a check: if the element has a `_frame` key, look up the element in that frame instead of the main page:

```python
    def get_element_by_index(self, page, index: int):
        if index < 0 or index >= len(self._last_interactive):
            return None

        el = self._last_interactive[index]
        role = el["role"]
        name = el["name"]

        # If element is from an iframe, search in that frame
        if el.get("_frame"):
            return self._find_in_frame(page, el)

        # ... existing main-page lookup code ...
```

Add helper method:

```python
    def _find_in_frame(self, page, el):
        """Find an element inside an iframe by frame name and role/name."""
        frame_name = el["_frame"]
        for frame in page.frames:
            if frame.name == frame_name or frame.url.split("/")[-1][:20] == frame_name:
                try:
                    locator = frame.get_by_role(el["role"], name=el["name"])
                    if locator.count() > 0:
                        return locator.first.element_handle()
                except Exception:
                    pass
        return None
```

- [ ] **Step 7: Run all tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/full_signup_test.py --ignore=tests/live_reddit_test.py --ignore=tests/real_signup_test.py --ignore=tests/real_site_test.py --ignore=tests/scenario_test_deepseek.py`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/dom/frames.py fantoma/dom/accessibility.py tests/test_frames.py
git commit -m "feat: iframe ARIA extraction — elements from child frames visible to LLM"
```

---

### Task 4: Adaptive Wait — DOM Stability Detection

Replace fixed `wait_for_network_idle` with a debounced MutationObserver wait. Stops when DOM stops changing for 300ms. Falls back to network idle if observer fails. Uses the existing observer.py infrastructure.

**Files:**
- Create: `tests/test_adaptive_wait.py`
- Modify: `fantoma/browser/observer.py`
- Modify: `fantoma/executor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_adaptive_wait.py`:

```python
"""Tests for adaptive DOM stability wait."""
import pytest
from unittest.mock import MagicMock, patch


class TestWaitForDomStable:
    def test_calls_evaluate_with_js(self):
        from fantoma.browser.observer import wait_for_dom_stable
        page = MagicMock()
        page.evaluate.return_value = True  # DOM was stable
        result = wait_for_dom_stable(page, timeout=5000, debounce=300)
        assert result is True
        page.evaluate.assert_called_once()
        js = page.evaluate.call_args[0][0]
        assert "MutationObserver" in js

    def test_returns_false_on_timeout(self):
        from fantoma.browser.observer import wait_for_dom_stable
        page = MagicMock()
        page.evaluate.return_value = False  # Timed out
        result = wait_for_dom_stable(page, timeout=1000, debounce=300)
        assert result is False

    def test_returns_true_on_exception(self):
        from fantoma.browser.observer import wait_for_dom_stable
        page = MagicMock()
        page.evaluate.side_effect = Exception("Page navigated")
        # On exception, assume page changed (navigation) — return True
        result = wait_for_dom_stable(page, timeout=5000)
        assert result is True

    def test_default_params(self):
        from fantoma.browser.observer import wait_for_dom_stable
        page = MagicMock()
        page.evaluate.return_value = True
        wait_for_dom_stable(page)
        js = page.evaluate.call_args[0][0]
        # Should contain the timeout and debounce values
        assert "5000" in js or "timeout" in js.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_adaptive_wait.py -v`
Expected: FAIL — `ImportError: cannot import name 'wait_for_dom_stable'`

- [ ] **Step 3: Implement wait_for_dom_stable**

Add to `fantoma/browser/observer.py`:

```python
_DOM_STABLE_JS = """(args) => {
    const timeout = args[0];
    const debounce = args[1];
    return new Promise((resolve) => {
        let timer;
        const timeoutId = setTimeout(() => {
            if (observer) observer.disconnect();
            resolve(false);
        }, timeout);

        const observer = new MutationObserver(() => {
            clearTimeout(timer);
            timer = setTimeout(() => {
                observer.disconnect();
                clearTimeout(timeoutId);
                resolve(true);
            }, debounce);
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            characterData: true
        });

        // Start debounce immediately (resolve if no mutations at all)
        timer = setTimeout(() => {
            observer.disconnect();
            clearTimeout(timeoutId);
            resolve(true);
        }, debounce);
    });
}"""


def wait_for_dom_stable(page, timeout: int = 5000, debounce: int = 300) -> bool:
    """Wait until the DOM stops changing for `debounce` ms.

    Uses a MutationObserver that resets a timer on every mutation.
    Resolves True when DOM is stable, False on hard timeout.
    Returns True on exception (assume navigation occurred).

    Args:
        page: Playwright page object
        timeout: Hard timeout in ms (default 5000)
        debounce: Quiet period in ms (default 300)

    Returns:
        True if DOM stabilized, False if hard timeout hit
    """
    try:
        return page.evaluate(_DOM_STABLE_JS, [timeout, debounce])
    except Exception as e:
        log.debug("DOM stability wait failed: %s — assuming navigation", e)
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_adaptive_wait.py tests/test_observer.py -v`
Expected: All tests PASS

- [ ] **Step 5: Wire into executor.py**

In `fantoma/executor.py`, add import:
```python
from fantoma.browser.observer import inject_observer, collect_mutations, format_mutations, wait_for_dom_stable
```

In `execute_reactive()`, after CLICK actions where we currently call `wait_for_navigation` (in `_check_page_change`), add DOM stability as the primary wait. Modify `_check_page_change()`:

```python
    def _check_page_change(self, page, before, dom_hash: str, action: str, step_num: int) -> bool:
        """Wait for page to settle and check if it changed after an action."""
        # Primary: wait for DOM to stabilize (debounced MutationObserver)
        wait_for_dom_stable(page, timeout=5000, debounce=300)

        # Fallback: wait for any in-flight navigation
        try:
            wait_for_navigation(self.browser, timeout=3000)
        except Exception:
            pass

        try:
            after = self.diff.snapshot(page)
            changed = self.diff.changed(before, after)
            after_hash = self.memory.hash_dom(self.dom.extract(page))
        except Exception as nav_err:
            if "context was destroyed" in str(nav_err) or "navigat" in str(nav_err).lower():
                log.info("Step %d: page navigated (context rebuilt)", step_num)
                self.memory.record(action, dom_hash, "navigated", True, step_num)
                return True
            raise

        self.memory.record(action, dom_hash, after_hash, changed, step_num)
        return changed
```

- [ ] **Step 6: Run all tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/full_signup_test.py --ignore=tests/live_reddit_test.py --ignore=tests/real_signup_test.py --ignore=tests/real_site_test.py --ignore=tests/scenario_test_deepseek.py`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/observer.py fantoma/executor.py tests/test_adaptive_wait.py
git commit -m "feat: adaptive wait — debounced MutationObserver DOM stability detection"
```

---

### Task 5: Inline Field State — Validation & Error Context

Show `aria-invalid` state and error text inline with each element in the ARIA output. Instead of the LLM seeing `[3] textbox "Email"`, it sees `[3] textbox "Email" [invalid: "Please enter a valid email"]`. Pure code — reads existing ARIA attributes.

**Files:**
- Create: `tests/test_field_state.py`
- Modify: `fantoma/dom/accessibility.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_field_state.py`:

```python
"""Tests for inline field state — validation context in ARIA output."""
import pytest


class TestParseAriaLineWithState:
    def test_invalid_attribute_parsed(self):
        from fantoma.dom.accessibility import _parse_aria_line
        result = _parse_aria_line('textbox "Email" [invalid]')
        assert result is not None
        assert result.get("invalid") is True

    def test_required_attribute_parsed(self):
        from fantoma.dom.accessibility import _parse_aria_line
        result = _parse_aria_line('textbox "Email" [required]')
        assert result is not None
        assert result.get("required") is True

    def test_value_attribute_parsed(self):
        from fantoma.dom.accessibility import _parse_aria_line
        result = _parse_aria_line('textbox "Email" [value="user@test.com"]')
        assert result is not None
        assert result.get("value") == "user@test.com"


class TestEnrichFieldState:
    def test_invalid_field_shows_state(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "textbox", "name": "Email", "state": "", "raw": {"invalid": True}}
        result = enrich_field_state(el)
        assert "[invalid]" in result

    def test_required_field_shows_state(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "textbox", "name": "Email", "state": "", "raw": {"required": True}}
        result = enrich_field_state(el)
        assert "[required]" in result

    def test_invalid_with_description_shows_error(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "textbox", "name": "Email", "state": "",
              "raw": {"invalid": True}, "_error": "Please enter a valid email"}
        result = enrich_field_state(el)
        assert "invalid" in result
        assert "Please enter a valid email" in result

    def test_no_state_returns_empty(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "button", "name": "Submit", "state": "", "raw": {}}
        result = enrich_field_state(el)
        assert result == ""

    def test_value_shown_for_filled_field(self):
        from fantoma.dom.accessibility import enrich_field_state
        el = {"role": "textbox", "name": "Email", "state": "",
              "raw": {"value": "user@test.com"}}
        result = enrich_field_state(el)
        assert "user@test.com" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_field_state.py -v`
Expected: FAIL — `ImportError: cannot import name 'enrich_field_state'`

- [ ] **Step 3: Implement enrich_field_state**

Add to `fantoma/dom/accessibility.py`:

```python
def enrich_field_state(el: dict) -> str:
    """Build a state string from element attributes.

    Shows validation state (invalid, required) and error descriptions
    inline with the element, so the LLM sees why a field is failing.

    Returns a state string like ' [invalid: "Please enter a valid email"]'
    or empty string if no relevant state.
    """
    parts = []
    raw = el.get("raw", {})

    if raw.get("invalid"):
        error_text = el.get("_error", "")
        if error_text:
            parts.append(f'invalid: "{error_text}"')
        else:
            parts.append("invalid")

    if raw.get("required"):
        parts.append("required")

    if raw.get("checked"):
        parts.append("checked")

    if raw.get("disabled"):
        parts.append("disabled")

    if raw.get("value"):
        val = raw["value"]
        if len(val) > 30:
            val = val[:27] + "..."
        parts.append(f'value="{val}"')

    if not parts:
        return ""
    return " [" + ", ".join(parts) + "]"
```

- [ ] **Step 4: Wire enrich_field_state into extract_aria output**

In `extract_aria()`, in the element output loop, replace the existing state with enriched state:

Change:
```python
            output.append(f'{prefix}[{i}] {el["role"]} "{el["name"]}"{el["state"]}')
```

To:
```python
            state = enrich_field_state(el) or el["state"]
            output.append(f'{prefix}[{i}] {el["role"]} "{el["name"]}"{state}')
```

Also update the interactive element building code (in `extract_aria()`, around where elements are parsed) to preserve `invalid`, `required`, `value` from ARIA attributes into the `raw` dict. The existing `_parse_aria_line()` already parses these attributes — we just need to pass the full parsed dict as `raw`:

The current code builds:
```python
            interactive.append({
                "role": role,
                "name": name,
                "state": state,
                "raw": parsed,
            })
```

This already passes `parsed` as `raw`, which contains `invalid`, `required`, `value`, etc. from `_parse_aria_line()`. So `enrich_field_state` can already read `el["raw"]["invalid"]`, `el["raw"]["required"]`, etc. No change needed here.

- [ ] **Step 5: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_field_state.py tests/test_pruning.py tests/test_tree_diff.py tests/test_dom_extractor.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/dom/accessibility.py tests/test_field_state.py
git commit -m "feat: inline field state — validation errors visible in ARIA output"
```

---

### Task 6: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/full_signup_test.py --ignore=tests/live_reddit_test.py --ignore=tests/real_signup_test.py --ignore=tests/real_site_test.py --ignore=tests/scenario_test_deepseek.py`
Expected: All tests PASS

- [ ] **Step 2: Verify all new imports**

Run: `cd /home/workspace/workbench/fantoma && python -c "from fantoma.llm.structured import ACTION_SCHEMA, parse_structured, get_response_format; from fantoma.dom.frames import extract_frame_elements, collect_all_frame_elements; from fantoma.dom.accessibility import dedup_elements, enrich_field_state; from fantoma.browser.observer import wait_for_dom_stable; print('All Phase 2 imports OK')"`
Expected: "All Phase 2 imports OK"

- [ ] **Step 3: Verify version still 0.6.0**

Run: `cd /home/workspace/workbench/fantoma && grep version pyproject.toml | head -1`
Expected: `version = "0.6.0"`

- [ ] **Step 4: Commit (if any cleanup needed)**

Only if tests required fixes.
