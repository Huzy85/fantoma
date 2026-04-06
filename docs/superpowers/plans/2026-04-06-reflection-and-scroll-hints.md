# Reflection Model + Scroll Hints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reflection fields (EVAL/MEMORY/GOAL) to the agent loop, content-mode extraction on DONE, and viewport scroll hints to ARIA output.

**Architecture:** Three independent changes in two source files. Reflection restructures the LLM prompt and history in `agent.py`. Scroll hints add a JS helper and output formatting in `accessibility.py`. Content-mode extraction upgrades `_extract_answer()` in `agent.py`. All changes are additive; existing APIs unchanged.

**Tech Stack:** Python 3.10+, Playwright (page.evaluate for scroll info), pytest with unittest.mock

**Spec:** `docs/superpowers/specs/2026-04-06-reflection-and-scroll-hints-design.md`

**Test runner:** `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/<file> -q --no-header`

---

### Task 1: Scroll Info Helper

**Files:**
- Modify: `fantoma/dom/accessibility.py` (add `get_scroll_info` function after line 73, before `prune_elements`)
- Test: `tests/test_scroll_hints.py` (create)

- [ ] **Step 1: Write failing tests for get_scroll_info**

Create `tests/test_scroll_hints.py`:

```python
"""Tests for viewport scroll hints in ARIA extraction."""

from unittest.mock import MagicMock
import pytest

from fantoma.dom.accessibility import get_scroll_info


def _make_page(scroll_y=0, inner_height=800, scroll_height=2400):
    """Mock page with evaluate() returning scroll metrics."""
    page = MagicMock()
    def eval_fn(js):
        return {
            "pixels_above": scroll_y,
            "pixels_below": max(0, scroll_height - (inner_height + scroll_y)),
            "pages_above": round(scroll_y / inner_height, 1) if inner_height else 0,
            "pages_below": round(max(0, scroll_height - (inner_height + scroll_y)) / inner_height, 1) if inner_height else 0,
        }
    page.evaluate.side_effect = eval_fn
    return page


class TestGetScrollInfo:

    def test_at_top_of_page(self):
        page = _make_page(scroll_y=0, inner_height=800, scroll_height=2400)
        info = get_scroll_info(page)
        assert info["pixels_above"] == 0
        assert info["pixels_below"] == 1600
        assert info["pages_above"] == 0
        assert info["pages_below"] == 2.0

    def test_scrolled_midway(self):
        page = _make_page(scroll_y=800, inner_height=800, scroll_height=2400)
        info = get_scroll_info(page)
        assert info["pixels_above"] == 800
        assert info["pixels_below"] == 800
        assert info["pages_above"] == 1.0
        assert info["pages_below"] == 1.0

    def test_at_bottom_of_page(self):
        page = _make_page(scroll_y=1600, inner_height=800, scroll_height=2400)
        info = get_scroll_info(page)
        assert info["pixels_above"] == 1600
        assert info["pixels_below"] == 0
        assert info["pages_below"] == 0

    def test_short_page_no_scroll(self):
        page = _make_page(scroll_y=0, inner_height=800, scroll_height=600)
        info = get_scroll_info(page)
        assert info["pixels_above"] == 0
        assert info["pixels_below"] == 0

    def test_evaluate_failure_returns_none(self):
        page = MagicMock()
        page.evaluate.side_effect = Exception("JS error")
        assert get_scroll_info(page) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_scroll_hints.py -q --no-header`

Expected: FAIL with `ImportError: cannot import name 'get_scroll_info'`

- [ ] **Step 3: Implement get_scroll_info**

Add to `fantoma/dom/accessibility.py` after the `_STOP_WORDS` set (after line 73, before `prune_elements`):

```python
def get_scroll_info(page) -> dict | None:
    """Get viewport scroll position metrics via JavaScript.

    Returns dict with pixels_above, pixels_below, pages_above, pages_below.
    Returns None on any error (JS eval failure, headless quirks, etc.).
    """
    try:
        return page.evaluate("""() => {
            const vh = window.innerHeight;
            const ph = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight || 0);
            const sy = window.scrollY || window.pageYOffset || 0;
            const below = Math.max(0, ph - (vh + sy));
            return {
                pixels_above: Math.round(sy),
                pixels_below: Math.round(below),
                pages_above: vh > 0 ? +(sy / vh).toFixed(1) : 0,
                pages_below: vh > 0 ? +(below / vh).toFixed(1) : 0,
            }
        }()""")
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_scroll_hints.py -q --no-header`

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add fantoma/dom/accessibility.py tests/test_scroll_hints.py && git commit -m "feat: add get_scroll_info helper for viewport scroll metrics"
```

---

### Task 2: Scroll Hints in ARIA Output

**Files:**
- Modify: `fantoma/dom/accessibility.py` — `extract_aria()` function (lines 342-404)
- Modify: `tests/test_scroll_hints.py` — add formatting tests

- [ ] **Step 1: Write failing tests for scroll hint formatting**

Append to `tests/test_scroll_hints.py`:

```python
from fantoma.dom.accessibility import extract_aria, format_scroll_hints


class TestFormatScrollHints:

    def test_at_top_with_content_below(self):
        above, below = format_scroll_hints({"pixels_above": 0, "pixels_below": 1600, "pages_above": 0, "pages_below": 2.0})
        assert above == "[Top of page]"
        assert "1600 pixels below" in below
        assert "2.0 pages" in below

    def test_at_bottom_with_content_above(self):
        above, below = format_scroll_hints({"pixels_above": 1600, "pixels_below": 0, "pages_above": 2.0, "pages_below": 0})
        assert "1600 pixels above" in above
        assert below == "[End of page]"

    def test_midway(self):
        above, below = format_scroll_hints({"pixels_above": 800, "pixels_below": 800, "pages_above": 1.0, "pages_below": 1.0})
        assert "800 pixels above" in above
        assert "800 pixels below" in below

    def test_short_page(self):
        above, below = format_scroll_hints({"pixels_above": 0, "pixels_below": 3, "pages_above": 0, "pages_below": 0})
        assert above == "[Top of page]"
        assert below == "[End of page]"

    def test_none_returns_empty(self):
        above, below = format_scroll_hints(None)
        assert above == ""
        assert below == ""


class TestExtractAriaScrollHints:

    def test_scroll_hints_appear_in_output(self):
        """extract_aria output includes scroll context when page has content below."""
        page = MagicMock()
        page.title.return_value = "Test"
        page.url = "https://example.com"
        page.locator.return_value.aria_snapshot.return_value = '- button "Submit"'
        # Mock evaluate for scroll info
        page.evaluate.return_value = {
            "pixels_above": 0,
            "pixels_below": 1600,
            "pages_above": 0,
            "pages_below": 2.0,
        }
        result = extract_aria(page)
        assert "[Top of page]" in result
        assert "1600 pixels below" in result

    def test_no_scroll_hints_on_error(self):
        """extract_aria works without scroll hints when JS fails."""
        page = MagicMock()
        page.title.return_value = "Test"
        page.url = "https://example.com"
        page.locator.return_value.aria_snapshot.return_value = '- button "Submit"'
        page.evaluate.side_effect = Exception("JS error")
        result = extract_aria(page)
        assert "Submit" in result
        assert "[Top of page]" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_scroll_hints.py -q --no-header`

Expected: FAIL with `ImportError: cannot import name 'format_scroll_hints'`

- [ ] **Step 3: Implement format_scroll_hints and wire into extract_aria**

Add `format_scroll_hints` to `fantoma/dom/accessibility.py` right after `get_scroll_info`:

```python
def format_scroll_hints(info: dict | None) -> tuple[str, str]:
    """Format scroll info into header/footer hint lines.

    Returns (above_hint, below_hint). Both empty strings if info is None.
    """
    if info is None:
        return "", ""

    THRESHOLD = 4

    if info["pixels_above"] <= THRESHOLD:
        above = "[Top of page]"
    else:
        above = f"... {info['pixels_above']} pixels above ({info['pages_above']} pages) - scroll up for more ..."

    if info["pixels_below"] <= THRESHOLD:
        below = "[End of page]"
    else:
        below = f"... {info['pixels_below']} pixels below ({info['pages_below']} pages) - scroll down for more ..."

    return above, below
```

Then modify `extract_aria()`. After the line `output.append("")` (line 346) and before `if interactive:` (line 348), add scroll hint retrieval and the above hint. After the final heading block, add the below hint.

Find this block in `extract_aria` (around lines 342-346):

```python
    # Build output
    output = []
    output.append(f"Page: {title}")
    output.append(f"URL: {url}")
    output.append("")
```

Replace with:

```python
    # Build output
    output = []
    output.append(f"Page: {title}")
    output.append(f"URL: {url}")
    output.append("")

    # Scroll context hints
    scroll_info = get_scroll_info(page)
    above_hint, below_hint = format_scroll_hints(scroll_info)
    if above_hint:
        output.append(above_hint)
        output.append("")
```

Then at the very end of the function, before the `return "\n".join(output)`, add:

```python
    if below_hint:
        output.append("")
        output.append(below_hint)

    return "\n".join(output)
```

Remove the existing `return "\n".join(output)` that was there before.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_scroll_hints.py -q --no-header`

Expected: 10 passed

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_paint_order.py -q --no-header`

Expected: 13 passed (paint order tests still work since they mock page.evaluate)

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add fantoma/dom/accessibility.py tests/test_scroll_hints.py && git commit -m "feat: viewport scroll hints in ARIA output"
```

---

### Task 3: Reflection Parsing

**Files:**
- Modify: `fantoma/agent.py` — add `_parse_reflection` function (after `_parse_actions`, around line 126)
- Test: `tests/test_reflection.py` (create)

- [ ] **Step 1: Write failing tests for reflection parsing**

Create `tests/test_reflection.py`:

```python
"""Tests for agent reflection parsing and history formatting."""

import pytest

from fantoma.agent import _parse_reflection, _parse_actions


class TestParseReflection:

    def test_full_reflection_with_actions(self):
        raw = """EVAL: Clicked search button, results loaded.
MEMORY: On results page, found 3 items. Need cheapest.
GOAL: Click sort by price dropdown.

CLICK [5]"""
        reflection, remainder = _parse_reflection(raw)
        assert reflection["eval"] == "Clicked search button, results loaded."
        assert reflection["memory"] == "On results page, found 3 items. Need cheapest."
        assert reflection["goal"] == "Click sort by price dropdown."
        actions = _parse_actions(remainder)
        assert len(actions) == 1
        assert actions[0] == ("click", {"element_id": 5})

    def test_no_reflection_just_actions(self):
        raw = "CLICK [3]\nTYPE [1] \"hello\""
        reflection, remainder = _parse_reflection(raw)
        assert reflection["eval"] == ""
        assert reflection["memory"] == ""
        assert reflection["goal"] == ""
        actions = _parse_actions(remainder)
        assert len(actions) == 2

    def test_partial_reflection(self):
        raw = """GOAL: Find the submit button.

CLICK [2]"""
        reflection, remainder = _parse_reflection(raw)
        assert reflection["eval"] == ""
        assert reflection["goal"] == "Find the submit button."
        actions = _parse_actions(remainder)
        assert len(actions) == 1

    def test_multiline_memory(self):
        raw = """EVAL: Search completed.
MEMORY: Found 5 recipes. Best candidate is item 3 with 4.8 stars.
GOAL: Click item 3 to verify prep time.

CLICK [3]"""
        reflection, remainder = _parse_reflection(raw)
        assert "5 recipes" in reflection["memory"]
        assert "4.8 stars" in reflection["memory"]

    def test_empty_input(self):
        reflection, remainder = _parse_reflection("")
        assert reflection["eval"] == ""
        assert reflection["memory"] == ""
        assert reflection["goal"] == ""
        assert remainder == ""

    def test_done_after_reflection(self):
        raw = """EVAL: All criteria verified. Recipe has 4.5 stars and 20min prep.
MEMORY: Found target recipe on allrecipes.com.
GOAL: Task complete.

DONE"""
        reflection, remainder = _parse_reflection(raw)
        assert "4.5 stars" in reflection["eval"]
        actions = _parse_actions(remainder)
        assert actions == [("done", {})]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_reflection.py -q --no-header`

Expected: FAIL with `ImportError: cannot import name '_parse_reflection'`

- [ ] **Step 3: Implement _parse_reflection**

Add to `fantoma/agent.py` after the `_parse_actions` function (after line 125):

```python
def _parse_reflection(raw: str) -> tuple[dict, str]:
    """Extract EVAL/MEMORY/GOAL lines from LLM response.

    Returns (reflection_dict, remainder_for_action_parsing).
    Reflection fields default to empty string if not found.
    """
    reflection = {"eval": "", "memory": "", "goal": ""}
    if not raw:
        return reflection, ""

    lines = raw.strip().split("\n")
    action_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("EVAL:"):
            reflection["eval"] = stripped[5:].strip()
            action_start = i + 1
        elif stripped.startswith("MEMORY:"):
            reflection["memory"] = stripped[7:].strip()
            action_start = i + 1
        elif stripped.startswith("GOAL:"):
            reflection["goal"] = stripped[5:].strip()
            action_start = i + 1
        elif stripped == "":
            action_start = i + 1
            continue
        else:
            # First non-reflection, non-blank line = start of actions
            action_start = i
            break

    remainder = "\n".join(lines[action_start:])
    return reflection, remainder
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_reflection.py -q --no-header`

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add fantoma/agent.py tests/test_reflection.py && git commit -m "feat: add _parse_reflection for EVAL/MEMORY/GOAL extraction"
```

---

### Task 4: Wire Reflection into Agent Loop

**Files:**
- Modify: `fantoma/agent.py` — REACTIVE_PROMPT, history handling in `run()`, `_extract_answer()`

- [ ] **Step 1: Write failing tests for history formatting**

Append to `tests/test_reflection.py`:

```python
from fantoma.agent import _format_history


class TestFormatHistory:

    def test_single_step(self):
        history = [{"step": 1, "eval": "", "memory": "Starting task.", "goal": "Find search box.", "actions": "CLICK [2] -> OK", "url": "https://example.com"}]
        result = _format_history(history)
        assert "Step 1" in result
        assert "Find search box" in result
        assert "CLICK [2]" in result

    def test_multiple_steps(self):
        history = [
            {"step": 1, "eval": "", "memory": "Starting.", "goal": "Search.", "actions": "TYPE [1] -> OK", "url": "https://a.com"},
            {"step": 2, "eval": "Search worked.", "memory": "On results.", "goal": "Click first.", "actions": "CLICK [3] -> OK", "url": "https://a.com/results"},
        ]
        result = _format_history(history)
        assert "Step 1" in result
        assert "Step 2" in result
        assert "Search worked" in result

    def test_truncates_to_20(self):
        history = [{"step": i, "eval": "OK", "memory": "M", "goal": "G", "actions": "CLICK [1] -> OK", "url": "https://a.com"} for i in range(1, 30)]
        result = _format_history(history)
        assert "Step 10" not in result  # Oldest dropped
        assert "Step 29" in result      # Recent kept

    def test_empty_history(self):
        assert _format_history([]) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_reflection.py::TestFormatHistory -q --no-header`

Expected: FAIL with `ImportError: cannot import name '_format_history'`

- [ ] **Step 3: Implement _format_history**

Add to `fantoma/agent.py` after `_parse_reflection`:

```python
def _format_history(history: list[dict], max_steps: int = 20) -> str:
    """Format structured history for LLM context. One line per step, last N steps."""
    if not history:
        return ""
    recent = history[-max_steps:]
    lines = []
    for h in recent:
        parts = []
        if h.get("goal"):
            parts.append(f"GOAL: {h['goal']}")
        parts.append(h.get("actions", ""))
        if h.get("eval"):
            parts.append(f"EVAL: {h['eval']}")
        domain = h.get("url", "").split("//")[-1].split("/")[0] if h.get("url") else ""
        prefix = f"Step {h['step']}"
        if domain:
            prefix += f" ({domain})"
        lines.append(f"{prefix}: {' | '.join(parts)}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_reflection.py -q --no-header`

Expected: 10 passed

- [ ] **Step 5: Update REACTIVE_PROMPT**

In `fantoma/agent.py`, replace the `REACTIVE_PROMPT` string (lines 34-59) with:

```python
REACTIVE_PROMPT = """\
You control a browser. Your job is to COMPLETE the task, not just observe the page.

Before picking actions, reflect:
EVAL: One sentence — did your last action work? (Skip on first step.)
MEMORY: What you've found so far and what's left to do. Be specific.
GOAL: What you'll do next and why.

Then pick 1-5 actions (one per line):
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
- Elements marked with * are NEW (just appeared from your last action).
- To fill a form: TYPE each field, then CLICK submit, all in one response.
- After typing in a search field, add PRESS Enter.
- NAVIGATE and DONE end the sequence.
- Only say DONE when the task is FULLY completed and all criteria are met.
- If the task has multiple parts, verify EACH part before saying DONE.
- If secrets are available, use them with <secret:name> syntax.
- Reply with ONLY reflection lines + action lines, nothing else.\
"""
```

- [ ] **Step 6: Update the run() method to use structured history and reflection**

In `fantoma/agent.py`, modify the `run()` method. Replace the `history = []` initialisation and the main loop body.

Replace line 159:
```python
        history = []
```
With:
```python
        history = []  # list of dicts: {step, eval, memory, goal, actions, url}
        last_memory = ""
```

Replace the LLM message building block (lines 187-191):
```python
                # Build LLM messages
                messages = [{"role": "system", "content": REACTIVE_PROMPT}]
                if history:
                    messages.append({"role": "assistant", "content": "\n".join(history[-10:])})
                messages.append({"role": "user", "content": f"Task: {task}\n\nPage ({state['url']}):\n{aria}"})
```
With:
```python
                # Build LLM messages
                messages = [{"role": "system", "content": REACTIVE_PROMPT}]
                formatted = _format_history(history)
                if formatted:
                    messages.append({"role": "assistant", "content": formatted})
                messages.append({"role": "user", "content": f"Task: {task}\n\nPage ({state['url']}):\n{aria}"})
```

Replace the LLM response parsing block (lines 194-200):
```python
                # Ask LLM
                raw = self._llm.chat(messages, max_tokens=500)
                if not raw:
                    continue

                actions = _parse_actions(raw)
                if not actions:
                    continue
```
With:
```python
                # Ask LLM
                raw = self._llm.chat(messages, max_tokens=500)
                if not raw:
                    continue

                reflection, action_text = _parse_reflection(raw)
                actions = _parse_actions(action_text)
                if not actions:
                    continue
                last_memory = reflection.get("memory", "")
```

Replace the history append line (line 226):
```python
                    history.append(f"Step {step_num}: {action_desc} → {outcome}")
```
With:
```python
                    # Build step record on first action of step
                    if not any(h.get("step") == step_num for h in history):
                        history.append({
                            "step": step_num,
                            "eval": reflection.get("eval", ""),
                            "memory": reflection.get("memory", ""),
                            "goal": reflection.get("goal", ""),
                            "actions": f"{action_desc} -> {outcome}",
                            "url": state.get("url", ""),
                        })
                    else:
                        # Append additional actions to existing step record
                        history[-1]["actions"] += f", {action_desc} -> {outcome}"
```

Replace the DONE handler (lines 203-207):
```python
                    if action_type == "done":
                        data = self._extract_answer(task, state)
                        return AgentResult(success=True, data=data, steps_taken=step_num,
                                           steps_detail=steps_detail,
                                           escalations=self.escalation.total_escalations)
```
With:
```python
                    if action_type == "done":
                        data = self._extract_answer(task, state, memory=last_memory)
                        return AgentResult(success=True, data=data, steps_taken=step_num,
                                           steps_detail=steps_detail,
                                           escalations=self.escalation.total_escalations)
```

Replace the loop detection block (lines 234-243):
```python
                # Loop detection: last 5 actions identical
                if len(history) >= 5 and len(set(history[-5:])) == 1:
```
With:
```python
                # Loop detection: last 5 action strings identical
                if len(history) >= 5 and len(set(h["actions"] for h in history[-5:])) == 1:
```

- [ ] **Step 7: Update _extract_answer to use content mode + memory**

Replace the existing `_extract_answer` method (lines 285-294) with:

```python
    def _extract_answer(self, task: str, state: dict, memory: str = "") -> str:
        """Extract a concise answer using content-mode page text and agent memory."""
        try:
            page = self.fantoma._engine.get_page()
            content = self.fantoma._dom.extract_content(page)
            agent_context = f"\n\nAgent found: {memory}" if memory else ""
            messages = [
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": f"Task: {task}{agent_context}\n\nPage content:\n{content}"},
            ]
            return self._llm.chat(messages, max_tokens=1000) or ""
        except Exception:
            return state.get("aria_tree", "")[:2000]
```

- [ ] **Step 8: Run all tests**

Run: `cd /home/workspace/workbench/fantoma && timeout 30 python -m pytest tests/test_reflection.py tests/test_scroll_hints.py tests/test_paint_order.py -q --no-header`

Expected: All pass (10 reflection + 10 scroll + 13 paint order = 33 tests)

- [ ] **Step 9: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add fantoma/agent.py tests/test_reflection.py && git commit -m "feat: reflection model in agent loop with content-mode extraction on DONE"
```

---

### Task 5: Integration Smoke Test

**Files:**
- No new files. Manual verification against the Docker container.

- [ ] **Step 1: Rebuild Docker container with changes**

```bash
cd /home/workspace/workbench/fantoma && docker cp fantoma/agent.py fantoma-browser:/app/fantoma/agent.py && docker cp fantoma/dom/accessibility.py fantoma-browser:/app/fantoma/dom/accessibility.py && docker exec fantoma-browser find /app -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null; true
```

- [ ] **Step 2: Smoke test — run a simple task**

```bash
curl -s -X POST http://localhost:7860/run -H "Content-Type: application/json" -d '{"task": "What is the top story on Hacker News right now?", "url": "https://news.ycombinator.com"}' | python3 -m json.tool
```

Expected: JSON response with `"success": true` and `"data"` containing the actual top story title, not raw ARIA tree.

- [ ] **Step 3: Verify scroll hints in state output**

```bash
curl -s -X POST http://localhost:7860/start -H "Content-Type: application/json" -d '{"url": "https://news.ycombinator.com"}'
curl -s http://localhost:7860/state | python3 -m json.tool | head -20
```

Expected: `aria_tree` field contains `[Top of page]` and `... pixels below ... scroll down for more ...`

- [ ] **Step 4: Stop session and clean up**

```bash
curl -s -X POST http://localhost:7860/stop
```

- [ ] **Step 5: Final commit with all changes**

```bash
cd /home/workspace/workbench/fantoma && git add -A && git status
```

If any uncommitted changes remain, commit them:
```bash
git commit -m "chore: integration smoke test verified"
```
