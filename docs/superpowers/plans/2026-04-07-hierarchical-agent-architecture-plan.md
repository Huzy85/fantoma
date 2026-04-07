# Hierarchical Agent Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Fantoma's single-loop agent with a planner/navigator architecture to fix the 80% loop failure rate on WebVoyager, raising the score from 22.1% to 40%+ with cheap models.

**Architecture:** Thin planner decomposes tasks into subtasks (never sees DOM). Navigator executes one subtask at a time against filtered DOM with mutation feedback. StateTracker detects stagnation and triggers planner re-planning with checkpoint backtracking.

**Tech Stack:** Python 3.12, Playwright (existing), httpx (existing LLM client), dataclasses

**Spec:** `docs/superpowers/specs/2026-04-07-hierarchical-agent-architecture-design.md`

---

### Task 1: StateTracker

**Files:**
- Create: `fantoma/state_tracker.py`
- Create: `tests/test_state_tracker.py`

- [ ] **Step 1: Write failing tests for StateTracker**

```python
# tests/test_state_tracker.py
import pytest
from fantoma.state_tracker import StateTracker


class TestFingerprint:
    def test_not_stagnant_with_different_content(self):
        t = StateTracker()
        t.add("https://a.com", "content one", "click({ID})")
        t.add("https://a.com", "content two", "click({ID})")
        t.add("https://a.com", "content three", "click({ID})")
        assert t.is_stagnant() is False

    def test_stagnant_after_3_identical(self):
        t = StateTracker()
        t.add("https://a.com", "same content", "click({ID})")
        t.add("https://a.com", "same content", "scroll(down)")
        t.add("https://a.com", "same content", "click({ID})")
        assert t.is_stagnant() is True

    def test_not_stagnant_with_fewer_than_3(self):
        t = StateTracker()
        t.add("https://a.com", "same", "click({ID})")
        t.add("https://a.com", "same", "click({ID})")
        assert t.is_stagnant() is False

    def test_url_change_breaks_stagnation(self):
        t = StateTracker()
        t.add("https://a.com", "same", "click({ID})")
        t.add("https://b.com", "same", "click({ID})")
        t.add("https://a.com", "same", "click({ID})")
        assert t.is_stagnant() is False


class TestCycleDetection:
    def test_not_cycling_with_varied_actions(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "click({'element_id': 1})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c3", "type_text({'element_id': 2, 'text': 'hello'})")
        t.add("https://a.com", "c4", "click({'element_id': 3})")
        assert t.is_cycling() is False

    def test_cycling_same_action_4_times(self):
        t = StateTracker()
        for i in range(4):
            t.add("https://a.com", f"c{i}", "click({'element_id': 5}) -> OK")
        assert t.is_cycling() is True

    def test_cycling_alternating_pattern(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'}) -> OK")
        t.add("https://a.com", "c2", "click({'element_id': 3}) -> OK")
        t.add("https://a.com", "c3", "scroll({'direction': 'down'}) -> OK")
        t.add("https://a.com", "c4", "click({'element_id': 7}) -> FAILED")
        assert t.is_cycling() is True

    def test_normalises_element_ids(self):
        """Different element IDs for same action type should still detect cycles."""
        t = StateTracker()
        t.add("https://a.com", "c1", "click({'element_id': 1}) -> OK")
        t.add("https://a.com", "c2", "click({'element_id': 5}) -> OK")
        t.add("https://a.com", "c3", "click({'element_id': 9}) -> FAILED")
        t.add("https://a.com", "c4", "click({'element_id': 2}) -> OK")
        assert t.is_cycling() is True

    def test_not_cycling_with_fewer_than_4(self):
        t = StateTracker()
        for i in range(3):
            t.add("https://a.com", f"c{i}", "click({'element_id': 1})")
        assert t.is_cycling() is False


class TestScrollLimit:
    def test_no_limit_under_3(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is False

    def test_limit_at_3(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c3", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is True

    def test_non_scroll_resets_counter(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c3", "click({'element_id': 1})")
        t.add("https://a.com", "c4", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is False

    def test_url_change_resets_counter(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "scroll({'direction': 'down'})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        t.add("https://b.com", "c3", "scroll({'direction': 'down'})")
        assert t.scroll_limit_hit() is False


class TestShouldStop:
    def test_returns_reason(self):
        t = StateTracker()
        for i in range(3):
            t.add("https://a.com", "same", f"scroll({{'direction': 'down'}})")
        stop, reason = t.should_stop()
        assert stop is True
        assert reason == "scroll_limit"

    def test_no_stop_when_healthy(self):
        t = StateTracker()
        t.add("https://a.com", "c1", "click({'element_id': 1})")
        t.add("https://a.com", "c2", "scroll({'direction': 'down'})")
        stop, reason = t.should_stop()
        assert stop is False
        assert reason == ""


class TestReset:
    def test_reset_clears_all(self):
        t = StateTracker()
        for i in range(4):
            t.add("https://a.com", "same", "click({'element_id': 1})")
        assert t.is_cycling() is True
        t.reset()
        assert t.is_cycling() is False
        assert t.is_stagnant() is False
        assert t.scroll_limit_hit() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_state_tracker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fantoma.state_tracker'`

- [ ] **Step 3: Implement StateTracker**

```python
# fantoma/state_tracker.py
"""DOM fingerprinting and stagnation detection for the navigator loop."""

import hashlib
import re
from collections import deque


class StateTracker:
    """Tracks page state changes to detect stagnation, action cycles, and scroll loops.

    Used by the navigator to know when to give up on a subtask and
    return control to the planner for re-planning.
    """

    def __init__(self, window: int = 6):
        self.fingerprints: deque[str] = deque(maxlen=window)
        self.action_norms: deque[str] = deque(maxlen=window)
        self._scroll_count: int = 0
        self._scroll_url: str = ""

    def add(self, url: str, content: str, action_str: str) -> None:
        """Record a step. Call after every action."""
        fp = hashlib.md5(f"{url}|{content[:800]}".encode()).hexdigest()
        self.fingerprints.append(fp)

        norm = re.sub(r"\{'element_id':\s*\d+\}", "{ID}", action_str)
        norm = re.sub(r"\s*->\s*(OK|FAILED|ERROR)", "", norm)
        self.action_norms.append(norm.strip())

        if "scroll(" in action_str:
            if url == self._scroll_url:
                self._scroll_count += 1
            else:
                self._scroll_count = 1
                self._scroll_url = url
        else:
            self._scroll_count = 0

    def is_stagnant(self) -> bool:
        """DOM fingerprint unchanged for 3 consecutive steps."""
        return len(self.fingerprints) >= 3 and len(set(list(self.fingerprints)[-3:])) == 1

    def is_cycling(self) -> bool:
        """Last 4 normalised actions have <= 2 unique values."""
        if len(self.action_norms) < 4:
            return False
        last4 = list(self.action_norms)[-4:]
        return len(set(last4)) <= 2

    def scroll_limit_hit(self) -> bool:
        """3+ consecutive scrolls on same URL."""
        return self._scroll_count >= 3

    def should_stop(self) -> tuple[bool, str]:
        """Check all conditions. Returns (should_stop, reason)."""
        if self.scroll_limit_hit():
            return True, "scroll_limit"
        if self.is_cycling():
            return True, "action_cycle"
        if self.is_stagnant():
            return True, "dom_stagnant"
        return False, ""

    def reset(self) -> None:
        """Clear state for a new subtask."""
        self.fingerprints.clear()
        self.action_norms.clear()
        self._scroll_count = 0
        self._scroll_url = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_state_tracker.py -v`
Expected: All 15 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add fantoma/state_tracker.py tests/test_state_tracker.py && git commit -m "feat: add StateTracker for DOM fingerprinting and stagnation detection"
```

---

### Task 2: Planner

**Files:**
- Create: `fantoma/planner.py`
- Create: `tests/test_planner.py`

- [ ] **Step 1: Write failing tests for Planner**

```python
# tests/test_planner.py
import pytest
from unittest.mock import MagicMock
from fantoma.planner import Planner, Subtask, Checkpoint


class TestSubtaskParsing:
    def _make_planner(self, llm_response: str) -> Planner:
        llm = MagicMock()
        llm.chat.return_value = llm_response
        return Planner(llm)

    def test_parses_numbered_subtasks(self):
        p = self._make_planner(
            '1. instruction: Click the search box and type "AI" | mode: interact | done_when: Search results appear\n'
            '2. instruction: Read the first result title | mode: read | done_when: Title text is extracted'
        )
        result = p.decompose("Find AI articles", "Page: Home\nURL: https://example.com")
        assert len(result) == 2
        assert result[0].instruction == 'Click the search box and type "AI"'
        assert result[0].mode == "interact"
        assert result[0].done_when == "Search results appear"
        assert result[1].mode == "read"

    def test_mode_defaults_to_find(self):
        p = self._make_planner(
            '1. instruction: Look around the page | done_when: Found the link'
        )
        result = p.decompose("Find something", "Page: Home\nURL: https://a.com")
        assert result[0].mode == "find"

    def test_empty_response_returns_single_find_subtask(self):
        p = self._make_planner("")
        result = p.decompose("Do something", "Page: Home\nURL: https://a.com")
        assert len(result) == 1
        assert result[0].mode == "find"
        assert "Do something" in result[0].instruction

    def test_caps_at_5_subtasks(self):
        lines = "\n".join(
            f'{i}. instruction: Step {i} | mode: find | done_when: Done {i}'
            for i in range(1, 8)
        )
        p = self._make_planner(lines)
        result = p.decompose("Big task", "Page: Home\nURL: https://a.com")
        assert len(result) <= 5


class TestModeMapping:
    def test_planner_modes_map_to_dom_modes(self):
        assert Planner.MODE_MAP["find"] == "navigate"
        assert Planner.MODE_MAP["interact"] == "form"
        assert Planner.MODE_MAP["read"] == "content"


class TestReplan:
    def _make_planner(self, llm_response: str) -> Planner:
        llm = MagicMock()
        llm.chat.return_value = llm_response
        return Planner(llm)

    def test_replan_returns_new_subtasks(self):
        p = self._make_planner(
            '1. instruction: Try searching instead | mode: interact | done_when: Results visible'
        )
        failed = Subtask("Click the menu", "interact", "Menu opens")
        result = p.replan("Find info", [], failed, "Page: Home\nURL: https://a.com")
        assert result is not None
        assert len(result) == 1

    def test_replan_returns_none_after_3_attempts(self):
        p = self._make_planner(
            '1. instruction: Try again | mode: find | done_when: Found it'
        )
        failed = Subtask("Click menu", "interact", "Menu opens")
        for _ in range(3):
            p.replan("Find info", [], failed, "Page: Home\nURL: https://a.com")
        result = p.replan("Find info", [], failed, "Page: Home\nURL: https://a.com")
        assert result is None

    def test_replan_includes_failed_context_in_prompt(self):
        llm = MagicMock()
        llm.chat.return_value = '1. instruction: New approach | mode: find | done_when: Done'
        p = Planner(llm)
        failed = Subtask("Click the broken menu", "interact", "Menu opens")
        p.replan("Find info", [], failed, "Page: Error\nURL: https://a.com")
        call_args = llm.chat.call_args[0][0]
        prompt_text = " ".join(m["content"] for m in call_args)
        assert "Click the broken menu" in prompt_text


class TestSummarise:
    def test_passes_all_results_to_llm(self):
        llm = MagicMock()
        llm.chat.return_value = "The answer is 42"
        p = Planner(llm)
        from fantoma.navigator import NavigatorResult
        completed = [
            (Subtask("Step 1", "interact", "Done"), NavigatorResult("done", "Found page A", 3, [], "https://a.com")),
            (Subtask("Step 2", "read", "Done"), NavigatorResult("done", "Price is $42", 2, [], "https://a.com/price")),
        ]
        result = p.summarise("What is the price?", completed)
        assert result == "The answer is 42"
        call_args = llm.chat.call_args[0][0]
        prompt_text = " ".join(m["content"] for m in call_args)
        assert "Found page A" in prompt_text
        assert "Price is $42" in prompt_text


class TestCheckpoint:
    def test_checkpoint_fields(self):
        c = Checkpoint(url="https://a.com", subtask=Subtask("Do X", "find", "Done"), result_summary="Found X")
        assert c.url == "https://a.com"
        assert c.subtask.instruction == "Do X"
        assert c.result_summary == "Found X"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fantoma.planner'`

- [ ] **Step 3: Implement Planner**

```python
# fantoma/planner.py
"""Task decomposition and re-planning for the hierarchical agent."""

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("fantoma.planner")


@dataclass
class Subtask:
    instruction: str
    mode: str       # "interact" | "read" | "find"
    done_when: str


@dataclass
class Checkpoint:
    url: str
    subtask: Subtask
    result_summary: str


DECOMPOSE_SYSTEM = """\
You break web tasks into 2-5 concrete steps.
For each step, provide:
- instruction: what to do (name specific elements, URLs, search terms)
- mode: "interact" (forms, buttons), "read" (extract info), "find" (locate something)
- done_when: how to verify completion

Rules:
- Be specific. "Click the search box and type 'quantum computing'" not "search for it".
- If the task asks to extract information, the last step should be mode "read".
- If you need to search, specify the search term explicitly.
- Return a numbered list, one step per line, in this format:
  1. instruction: ... | mode: ... | done_when: ...\
"""

REPLAN_ADDITION = """\
The previous approach failed on this step: {failed_instruction}
Completed so far: {completed_summary}
Current page: {page_summary}

You MUST try a completely different strategy. Options:
- Navigate directly to a URL instead of clicking through menus
- Use search functionality instead of browsing categories
- Simplify the goal — extract partial information and move on
- Try a different section of the site

Previously failed strategies: {failed_strategies}\
"""

SUMMARISE_SYSTEM = """\
You are extracting the answer to a web task from data gathered across multiple pages.
Address every criterion in the task explicitly.
Be specific and complete — vague answers will fail evaluation.\
"""

_VALID_MODES = {"interact", "read", "find"}


class Planner:
    MODE_MAP = {"find": "navigate", "interact": "form", "read": "content"}

    def __init__(self, llm):
        self._llm = llm
        self._replan_count = 0
        self._max_replans = 3
        self._failed_strategies: list[str] = []

    def decompose(self, task: str, page_summary: str) -> list[Subtask]:
        messages = [
            {"role": "system", "content": DECOMPOSE_SYSTEM},
            {"role": "user", "content": f"Task: {task}\n\nCurrent page:\n{page_summary}"},
        ]
        raw = self._llm.chat(messages, max_tokens=500)
        subtasks = _parse_subtasks(raw)
        if not subtasks:
            subtasks = [Subtask(instruction=task, mode="find", done_when="Task is complete")]
        return subtasks[:5]

    def replan(self, task: str, completed: list, failed: "Subtask", page_summary: str) -> list["Subtask"] | None:
        self._replan_count += 1
        if self._replan_count > self._max_replans:
            return None

        self._failed_strategies.append(failed.instruction)
        completed_summary = "; ".join(s.instruction for s, _ in completed) if completed else "Nothing completed yet"

        addition = REPLAN_ADDITION.format(
            failed_instruction=failed.instruction,
            completed_summary=completed_summary,
            page_summary=page_summary,
            failed_strategies="; ".join(self._failed_strategies),
        )

        messages = [
            {"role": "system", "content": DECOMPOSE_SYSTEM + "\n\n" + addition},
            {"role": "user", "content": f"Task: {task}\n\nCurrent page:\n{page_summary}"},
        ]
        raw = self._llm.chat(messages, max_tokens=500)
        subtasks = _parse_subtasks(raw)
        if not subtasks:
            return None
        return subtasks[:5]

    def summarise(self, task: str, completed: list) -> str:
        gathered = []
        for subtask, result in completed:
            gathered.append(f"Step: {subtask.instruction}\nResult: {result.data}")
        all_data = "\n\n".join(gathered)

        messages = [
            {"role": "system", "content": SUMMARISE_SYSTEM},
            {"role": "user", "content": f"Task: {task}\n\nData gathered:\n{all_data}"},
        ]
        return self._llm.chat(messages, max_tokens=1000) or ""


def _parse_subtasks(raw: str) -> list[Subtask]:
    """Parse numbered subtask lines from LLM output."""
    if not raw:
        return []
    subtasks = []
    for line in raw.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Strip leading number: "1. " or "1) "
        line = re.sub(r"^\d+[\.\)]\s*", "", line)

        instruction = ""
        mode = "find"
        done_when = ""

        # Parse pipe-separated fields
        parts = line.split("|")
        for part in parts:
            part = part.strip()
            lower = part.lower()
            if lower.startswith("instruction:"):
                instruction = part.split(":", 1)[1].strip()
            elif lower.startswith("mode:"):
                m = part.split(":", 1)[1].strip().lower()
                if m in _VALID_MODES:
                    mode = m
            elif lower.startswith("done_when:"):
                done_when = part.split(":", 1)[1].strip()

        # Fallback: if no pipe format, treat entire line as instruction
        if not instruction:
            instruction = line
            done_when = "Step complete"

        if instruction:
            subtasks.append(Subtask(instruction=instruction, mode=mode, done_when=done_when))
    return subtasks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_planner.py -v`
Expected: FAIL on `TestSummarise` because `fantoma.navigator.NavigatorResult` doesn't exist yet. That's expected — we'll fix it in Task 3.

For now, verify the other test classes pass:
Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_planner.py -v -k "not TestSummarise"`
Expected: All tests except TestSummarise PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add fantoma/planner.py tests/test_planner.py && git commit -m "feat: add Planner for task decomposition and re-planning"
```

---

### Task 3: Navigator

**Files:**
- Create: `fantoma/navigator.py`
- Create: `tests/test_navigator.py`

- [ ] **Step 1: Write failing tests for Navigator**

```python
# tests/test_navigator.py
import pytest
from unittest.mock import MagicMock, patch
from fantoma.navigator import Navigator, NavigatorResult, _parse_actions, MODE_MAP
from fantoma.planner import Subtask
from fantoma.state_tracker import StateTracker


class TestParseActions:
    """Moved from agent.py — verify action parsing still works."""

    def test_click(self):
        result = _parse_actions("CLICK [3]")
        assert result == [("click", {"element_id": 3})]

    def test_type(self):
        result = _parse_actions('TYPE [5] "hello world"')
        assert result == [("type_text", {"element_id": 5, "text": "hello world"})]

    def test_select(self):
        result = _parse_actions('SELECT [2] "Option A"')
        assert result == [("select", {"element_id": 2, "value": "Option A"})]

    def test_scroll_down(self):
        result = _parse_actions("SCROLL down")
        assert result == [("scroll", {"direction": "down"})]

    def test_navigate(self):
        result = _parse_actions("NAVIGATE https://example.com")
        assert result == [("navigate", {"url": "https://example.com"})]

    def test_press(self):
        result = _parse_actions("PRESS Enter")
        assert result == [("press_key", {"key": "Enter"})]

    def test_done(self):
        result = _parse_actions("DONE")
        assert result == [("done", {})]

    def test_multiple_actions(self):
        result = _parse_actions("CLICK [1]\nTYPE [2] \"test\"\nPRESS Enter")
        assert len(result) == 3
        assert result[0] == ("click", {"element_id": 1})
        assert result[1] == ("type_text", {"element_id": 2, "text": "test"})
        assert result[2] == ("press_key", {"key": "Enter"})

    def test_done_terminates_sequence(self):
        result = _parse_actions("CLICK [1]\nDONE\nCLICK [2]")
        assert len(result) == 2
        assert result[1] == ("done", {})

    def test_max_5_actions(self):
        lines = "\n".join(f"CLICK [{i}]" for i in range(10))
        result = _parse_actions(lines)
        assert len(result) == 5

    def test_empty_returns_empty(self):
        assert _parse_actions("") == []
        assert _parse_actions(None) == []


class TestModeMap:
    def test_all_planner_modes_mapped(self):
        assert MODE_MAP["find"] == "navigate"
        assert MODE_MAP["interact"] == "form"
        assert MODE_MAP["read"] == "content"


class TestNavigatorResult:
    def test_fields(self):
        r = NavigatorResult(status="done", data="Found it", steps_taken=3, steps_detail=[], final_url="https://a.com")
        assert r.status == "done"
        assert r.data == "Found it"
        assert r.steps_taken == 3


class TestDomainDrift:
    def test_detects_drift(self):
        nav = Navigator()
        assert nav._is_domain_drift("https://www.espn.com/scores", "www.espn.co.uk") is True

    def test_no_drift_same_domain(self):
        nav = Navigator()
        assert nav._is_domain_drift("https://www.amazon.com/results", "www.amazon.com") is False

    def test_no_drift_subdomain(self):
        nav = Navigator()
        assert nav._is_domain_drift("https://www.amazon.com/results", "amazon.com") is False

    def test_no_drift_without_start_domain(self):
        nav = Navigator()
        assert nav._is_domain_drift("https://www.example.com", "") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_navigator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fantoma.navigator'`

- [ ] **Step 3: Implement Navigator**

```python
# fantoma/navigator.py
"""Single-subtask execution loop for the hierarchical agent."""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from fantoma.browser.observer import collect_mutations, format_mutations
from fantoma.planner import Subtask
from fantoma.state_tracker import StateTracker

log = logging.getLogger("fantoma.navigator")

MODE_MAP = {"find": "navigate", "interact": "form", "read": "content"}


@dataclass
class NavigatorResult:
    status: str         # "done" | "stagnant" | "failed" | "max_steps"
    data: str
    steps_taken: int
    steps_detail: list
    final_url: str


NAVIGATOR_SYSTEM = """\
You control a browser to complete one specific task.

Subtask: {instruction}
Done when: {done_when}

Pick 1-5 actions (one per line):
CLICK [number]
TYPE [number] "text"
SELECT [number] "option"
SCROLL down|up
NAVIGATE https://...
PRESS Enter
DONE

Rules:
- Match [number] to the element list shown below.
- To fill a form: TYPE each field, then CLICK submit, all in one response.
- After typing in a search field, add PRESS Enter.
- NAVIGATE and DONE end the sequence.
- Say DONE only when the done_when condition is met.
- Read the Content section first — if it contains the answer, say DONE immediately.
- Reply with ONLY action lines, nothing else.\
"""

EXTRACT_ON_DONE = """\
You are extracting the answer from a web page.
Address every criterion in the task explicitly.
Be specific and complete. Include names, numbers, URLs where relevant.\
"""


def _parse_actions(raw: str) -> list[tuple[str, dict]]:
    """Parse LLM response into (action_type, params) tuples."""
    results = []
    for line in (raw or "").strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        m = re.match(r'CLICK\s*\[?(\d+)\]?', line, re.IGNORECASE)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'TYPE\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("type_text", {"element_id": int(m.group(1)), "text": m.group(2)}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'SELECT\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("select", {"element_id": int(m.group(1)), "value": m.group(2)}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'SCROLL\s*(UP|DOWN)', line, re.IGNORECASE)
        if m:
            results.append(("scroll", {"direction": m.group(1).lower()}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'NAVIGATE\s+["\']?(https?://\S+?)["\']?\s*$', line, re.IGNORECASE)
        if m:
            results.append(("navigate", {"url": m.group(1)}))
            break

        m = re.match(r'PRESS\s+(\w+)', line, re.IGNORECASE)
        if m:
            results.append(("press_key", {"key": m.group(1)}))
            if len(results) >= 5:
                break
            continue

        if re.match(r'DONE', line, re.IGNORECASE):
            results.append(("done", {}))
            break

        m = re.search(r'\[(\d+)\]', line)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))
            if len(results) >= 5:
                break

    return results


class Navigator:
    """Executes a single subtask against the browser."""

    def execute(
        self,
        subtask: Subtask,
        fantoma,
        llm,
        tracker: StateTracker,
        max_steps: int = 15,
        start_domain: str = "",
        sensitive_data: dict = None,
    ) -> NavigatorResult:
        steps_detail = []
        sensitive_data = sensitive_data or {}
        dom_mode = MODE_MAP.get(subtask.mode, "navigate")
        change_line = "First step"
        last_content = ""

        for step_num in range(1, max_steps + 1):
            page = fantoma._engine.get_page()
            current_url = page.url

            # Get filtered DOM
            aria = fantoma._dom.extract(page, task=subtask.instruction, mode=dom_mode)
            for name, value in sensitive_data.items():
                aria = aria.replace(value, f"<secret:{name}>")

            # Get page content for state tracking
            try:
                last_content = fantoma._dom.extract_content(page)[:800]
            except Exception:
                last_content = ""

            # Build prompt
            system = NAVIGATOR_SYSTEM.format(
                instruction=subtask.instruction,
                done_when=subtask.done_when,
            )
            user_msg = f"Change: {change_line}\n\nPage ({current_url}):\n{aria}"

            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]

            raw = llm.chat(messages, max_tokens=300)
            if not raw:
                continue

            actions = _parse_actions(raw)
            if not actions:
                continue

            for action_type, params in actions:
                if action_type == "done":
                    data = self._extract_answer(subtask, fantoma, llm)
                    return NavigatorResult(
                        status="done", data=data, steps_taken=step_num,
                        steps_detail=steps_detail, final_url=current_url,
                    )

                for name, value in sensitive_data.items():
                    if "text" in params:
                        params["text"] = params["text"].replace(f"<secret:{name}>", value)

                method = getattr(fantoma, action_type)
                action_desc = f"{action_type}({params})"
                try:
                    result = method(**params)
                    outcome = "OK" if result["success"] else "FAILED"
                except Exception as e:
                    log.warning("Action %s failed: %s", action_desc, e)
                    result = {"success": False}
                    outcome = "ERROR"

                steps_detail.append({
                    "step": step_num, "action": action_desc,
                    "success": result.get("success", False),
                    "url": fantoma._engine.get_page().url,
                })

                # Collect mutations immediately after action
                try:
                    mutations = collect_mutations(fantoma._engine.get_page())
                    change_line = format_mutations(mutations)
                    if not change_line:
                        change_line = "No changes detected"
                except Exception:
                    change_line = "No changes detected"

                # Update state tracker
                tracker.add(
                    fantoma._engine.get_page().url,
                    last_content,
                    f"{action_desc} -> {outcome}",
                )

                if not result.get("success", False):
                    break

            # Check stagnation
            should_stop, reason = tracker.should_stop()
            if should_stop:
                log.info("Navigator stopping: %s (step %d)", reason, step_num)
                return NavigatorResult(
                    status="stagnant", data=f"Stopped: {reason}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=fantoma._engine.get_page().url,
                )

            # Check domain drift
            current_url = fantoma._engine.get_page().url
            if self._is_domain_drift(current_url, start_domain):
                log.info("Domain drift detected: %s", current_url)
                return NavigatorResult(
                    status="failed", data=f"Domain drift to {current_url}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=current_url,
                )

        return NavigatorResult(
            status="max_steps", data="Step budget exhausted",
            steps_taken=max_steps, steps_detail=steps_detail,
            final_url=fantoma._engine.get_page().url,
        )

    def _extract_answer(self, subtask: Subtask, fantoma, llm) -> str:
        """Extract answer from current page when navigator says DONE."""
        try:
            page = fantoma._engine.get_page()
            content = fantoma._dom.extract_content(page)
            messages = [
                {"role": "system", "content": EXTRACT_ON_DONE},
                {"role": "user", "content": f"Task: {subtask.instruction}\n\nPage content:\n{content}"},
            ]
            return llm.chat(messages, max_tokens=1000) or ""
        except Exception as e:
            log.warning("Extract answer failed: %s", e)
            return ""

    @staticmethod
    def _is_domain_drift(current_url: str, start_domain: str) -> bool:
        """Check if current URL has drifted from the expected domain."""
        if not start_domain:
            return False
        try:
            current = urlparse(current_url).netloc.lower()
            start = start_domain.lower()
            # Allow subdomain matching: www.amazon.com matches amazon.com
            return not (current == start or current.endswith("." + start) or start.endswith("." + current))
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_navigator.py -v`
Expected: All tests PASS

Also run the planner summarise test that was skipped:
Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_planner.py::TestSummarise -v`
Expected: PASS (NavigatorResult now exists)

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add fantoma/navigator.py tests/test_navigator.py && git commit -m "feat: add Navigator for single-subtask execution with mutation feedback"
```

---

### Task 4: Refactor Agent as Orchestrator

**Files:**
- Modify: `fantoma/agent.py`
- Create: `tests/test_agent_orchestrator.py`

- [ ] **Step 1: Write failing tests for the new orchestrator**

```python
# tests/test_agent_orchestrator.py
"""Test Agent.run() orchestration of planner → navigator → summarise."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from fantoma.agent import Agent, AgentResult
from fantoma.planner import Subtask
from fantoma.navigator import NavigatorResult


class TestAgentRunOrchestration:
    def _mock_agent(self, planner_subtasks, navigator_results, summary="Final answer"):
        """Build an Agent with mocked planner, navigator, and browser."""
        agent = Agent.__new__(Agent)
        agent._max_steps = 25

        # Mock planner
        agent._planner = MagicMock()
        agent._planner.decompose.return_value = planner_subtasks
        agent._planner.summarise.return_value = summary
        agent._planner.replan.return_value = None

        # Mock navigator
        agent._navigator = MagicMock()
        agent._navigator.execute.side_effect = navigator_results

        # Mock fantoma (browser tool)
        agent.fantoma = MagicMock()
        agent.fantoma.start.return_value = {"url": "https://example.com", "aria_tree": ""}
        agent.fantoma._engine = MagicMock()
        page_mock = MagicMock()
        page_mock.url = "https://example.com"
        page_mock.title.return_value = "Example"
        agent.fantoma._engine.get_page.return_value = page_mock
        agent.fantoma._dom = MagicMock()
        agent.fantoma._dom.extract_content.return_value = "Page content here"
        agent.fantoma._dom.extract.return_value = "Page: Example\nURL: https://example.com"

        # Mock LLM
        agent._llm = MagicMock()
        agent._sensitive_data = {}
        agent.escalation = MagicMock()
        agent.escalation.total_escalations = 0

        return agent

    def test_simple_task_two_subtasks(self):
        subtasks = [
            Subtask("Search for AI", "interact", "Results visible"),
            Subtask("Read first result", "read", "Title extracted"),
        ]
        nav_results = [
            NavigatorResult("done", "Search done", 3, [], "https://example.com/search"),
            NavigatorResult("done", "Title is: AI Paper", 2, [], "https://example.com/result"),
        ]
        agent = self._mock_agent(subtasks, nav_results, "AI Paper found")
        result = agent.run("Find AI papers", start_url="https://example.com")

        assert result.success is True
        assert result.data == "AI Paper found"
        assert result.steps_taken == 5

    def test_stagnation_triggers_replan(self):
        subtasks = [Subtask("Click menu", "interact", "Menu opens")]
        nav_results = [
            NavigatorResult("stagnant", "Stopped: action_cycle", 8, [], "https://example.com"),
        ]
        agent = self._mock_agent(subtasks, nav_results)
        agent._planner.replan.return_value = None  # No more replans
        result = agent.run("Find something", start_url="https://example.com")

        agent._planner.replan.assert_called_once()

    def test_login_bypasses_orchestrator(self):
        """Agent.login() should NOT go through planner/navigator."""
        agent = Agent.__new__(Agent)
        agent.fantoma = MagicMock()
        agent.fantoma.start.return_value = {}
        agent.fantoma.login.return_value = {"success": True, "fields_filled": [], "steps": 2}
        agent.fantoma._engine = MagicMock()
        page_mock = MagicMock()
        page_mock.url = "https://example.com/dashboard"
        agent.fantoma._engine.get_page.return_value = page_mock

        from fantoma.browser.form_login import _looks_logged_in
        with patch("fantoma.browser_tool._looks_logged_in", return_value=True):
            result = agent.login("https://example.com/login", email="a@b.com", password="pw")

        assert result.success is True

    def test_extract_bypasses_orchestrator(self):
        """Agent.extract() should NOT go through planner/navigator."""
        agent = Agent.__new__(Agent)
        agent.fantoma = MagicMock()
        agent.fantoma.start.return_value = {}
        agent.fantoma.extract.return_value = "Extracted data"
        agent.fantoma.stop = MagicMock()

        result = agent.extract("https://example.com", "Get the title")
        assert result == "Extracted data"

    def test_step_budget_rolls_over(self):
        subtasks = [
            Subtask("Quick step", "interact", "Done"),
            Subtask("Big step", "find", "Done"),
        ]
        nav_results = [
            NavigatorResult("done", "Quick done", 2, [], "https://a.com"),
            NavigatorResult("done", "Big done", 10, [], "https://a.com/deep"),
        ]
        agent = self._mock_agent(subtasks, nav_results)
        agent._max_steps = 25
        result = agent.run("Do things", start_url="https://a.com")

        # Second navigator.execute should get more steps because first only used 2
        calls = agent._navigator.execute.call_args_list
        first_budget = calls[0].kwargs.get("max_steps") or calls[0][1].get("max_steps", 0)
        second_budget = calls[1].kwargs.get("max_steps") or calls[1][1].get("max_steps", 0)
        assert second_budget >= first_budget  # Rolled-over budget

    def test_browser_start_failure(self):
        agent = Agent.__new__(Agent)
        agent.fantoma = MagicMock()
        agent.fantoma.start.side_effect = Exception("Connection refused")
        agent._sensitive_data = {}

        result = agent.run("Do something", start_url="https://example.com")
        assert result.success is False
        assert "Connection refused" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py -v`
Expected: FAIL because Agent doesn't have `_planner`, `_navigator`, or the new `run()` logic yet.

- [ ] **Step 3: Refactor agent.py**

This is the biggest change. Replace the contents of `fantoma/agent.py` with the orchestrator version. Key changes:
- Remove `REACTIVE_PROMPT`, `EXTRACTION_PROMPT`, `COMPACTION_PROMPT`
- Remove `_parse_actions()`, `_parse_reflection()`, `_format_history()`
- Remove the action loop from `run()`
- Add `Planner` and `Navigator` instantiation in `__init__()`
- New `run()` that orchestrates planner → navigator → summarise
- Add `_get_page_summary()` helper
- Keep `login()`, `extract()`, `session()` exactly as-is
- Keep `AgentResult` dataclass exactly as-is
- Keep `_Session` class exactly as-is

The full replacement `agent.py` is approximately 300 lines. The key `run()` method:

```python
def run(self, task: str, start_url: str = None) -> AgentResult:
    """Run a browser task described in English."""
    log.info("Task: %s", task)
    self.fantoma._task = task

    start_domain = ""
    if start_url:
        try:
            start_domain = urlparse(start_url).netloc
        except Exception:
            pass

    try:
        state = self.fantoma.start(start_url)
    except Exception as e:
        return AgentResult(success=False, error=f"Browser start failed: {e}")

    try:
        summary = self._get_page_summary()
        subtasks = self._planner.decompose(task, summary)
        completed = []      # list of (Subtask, NavigatorResult)
        checkpoints = []    # list of Checkpoint
        all_steps = []
        total_steps = 0
        remaining_budget = self._max_steps

        i = 0
        while i < len(subtasks) and remaining_budget > 0:
            subtask = subtasks[i]
            n_remaining = len(subtasks) - i
            step_budget = max(5, remaining_budget // max(1, n_remaining))
            tracker = StateTracker()

            result = self._navigator.execute(
                subtask=subtask,
                fantoma=self.fantoma,
                llm=self._llm,
                tracker=tracker,
                max_steps=step_budget,
                start_domain=start_domain,
                sensitive_data=self._sensitive_data,
            )

            all_steps.extend(result.steps_detail)
            total_steps += result.steps_taken
            remaining_budget -= result.steps_taken

            if result.status == "done":
                completed.append((subtask, result))
                checkpoints.append(Checkpoint(
                    url=result.final_url,
                    subtask=subtask,
                    result_summary=result.data[:200],
                ))
                i += 1
                continue

            # Stagnation, failure, or budget exhausted — replan
            summary = self._get_page_summary()
            new_subtasks = self._planner.replan(task, completed, subtask, summary)
            if new_subtasks is None:
                break
            # Replace remaining subtasks with new plan
            subtasks = subtasks[:i] + new_subtasks
            # Backtrack if we have a checkpoint
            if checkpoints:
                try:
                    self.fantoma.navigate(checkpoints[-1].url)
                except Exception:
                    pass
            continue  # Retry from same index with new subtask

        answer = self._planner.summarise(task, completed)
        return AgentResult(
            success=bool(completed),
            data=answer,
            steps_taken=total_steps,
            steps_detail=all_steps,
            escalations=self.escalation.total_escalations,
        )
    except Exception as e:
        return AgentResult(success=False, error=str(e),
                           steps_taken=total_steps if 'total_steps' in dir() else 0,
                           steps_detail=all_steps if 'all_steps' in dir() else [])
    finally:
        self.fantoma.stop()
```

- [ ] **Step 4: Run the new orchestrator tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_agent_orchestrator.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_browser_tool.py tests/test_observer.py tests/test_dom_modes.py tests/test_session.py -v`
Expected: All PASS (these don't touch agent.py)

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_reflection.py -v`
Expected: May FAIL — reflection tests depend on `_parse_reflection()` which moved. If they fail, update imports to point to navigator or delete if reflection is removed.

- [ ] **Step 6: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add fantoma/agent.py tests/test_agent_orchestrator.py && git commit -m "refactor: replace single-loop agent with planner/navigator orchestrator"
```

---

### Task 5: Wire Mutation Feedback (already in navigator, verify end-to-end)

**Files:**
- No new files — verify navigator.py integration with observer.py

- [ ] **Step 1: Write integration test for mutation wiring**

```python
# Add to tests/test_navigator.py

class TestMutationFeedback:
    def test_change_line_included_in_prompt(self):
        """Verify the navigator includes Change: line in LLM messages."""
        from fantoma.navigator import Navigator, NAVIGATOR_SYSTEM
        from fantoma.planner import Subtask
        from fantoma.state_tracker import StateTracker

        nav = Navigator()
        subtask = Subtask("Click the button", "interact", "Page changes")

        # Mock fantoma
        fantoma = MagicMock()
        page = MagicMock()
        page.url = "https://example.com"
        page.title.return_value = "Example"
        fantoma._engine.get_page.return_value = page
        fantoma._dom.extract.return_value = "Page: Example\n[0] button 'Submit'"
        fantoma._dom.extract_content.return_value = "Some content"
        fantoma.click.return_value = {"success": True, "state": {"url": "https://example.com"}}

        # Mock LLM: first call returns CLICK, second returns DONE
        llm = MagicMock()
        llm.chat.side_effect = ["CLICK [0]", "DONE"]

        tracker = StateTracker()

        with patch("fantoma.navigator.collect_mutations", return_value={"added": ["div.results"], "removed": [], "changed_attrs": [], "text_changes": ["3 items found"]}):
            with patch("fantoma.navigator.format_mutations", return_value="Added: div.results | New text: 3 items found"):
                result = nav.execute(subtask, fantoma, llm, tracker, max_steps=5)

        # Check second LLM call includes mutation feedback
        second_call = llm.chat.call_args_list[1][0][0]
        user_msg = [m for m in second_call if m["role"] == "user"][0]["content"]
        assert "Added: div.results" in user_msg
```

- [ ] **Step 2: Run the test**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_navigator.py::TestMutationFeedback -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add tests/test_navigator.py && git commit -m "test: verify mutation feedback wiring in navigator"
```

---

### Task 6: Update Existing Tests and Clean Up

**Files:**
- Modify: `tests/test_reflection.py` (if it fails)
- Modify: `tests/test_progress.py` (if it references old agent internals)
- Verify: all other test files still pass

- [ ] **Step 1: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/live_test_runner.py --ignore=tests/live_test_25.py --ignore=tests/live_test_10_new.py --ignore=tests/scenario_test_deepseek.py --ignore=tests/chromium_antibot_test.py --ignore=tests/full_signup_test.py --ignore=tests/real_signup_test.py --ignore=tests/real_site_test.py --ignore=tests/test_container_live.py --ignore=tests/live_reddit_test.py -x`

Ignoring live/integration tests that require a running browser. Focus on unit tests.

- [ ] **Step 2: Fix any failing tests**

For `test_reflection.py`: if it imports `_parse_reflection` from `agent`, update to either remove (reflection is no longer used in navigator) or point to a compat import.

For `test_progress.py`: if it references the old action loop structure, update assertions to match new orchestrator flow.

For any test importing `REACTIVE_PROMPT` or `_parse_actions` from `agent`: update import to `from fantoma.navigator import _parse_actions`.

- [ ] **Step 3: Run full suite again**

Run: same command as Step 1
Expected: All unit tests PASS

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma && git add -u tests/ && git commit -m "fix: update tests for hierarchical agent refactor"
```

---

### Task 7: Deploy to Container and Smoke Test

**Files:**
- No file changes — deployment and verification

- [ ] **Step 1: Deploy to fantoma-browser container**

```bash
cd /home/workspace/workbench/fantoma
docker cp fantoma/state_tracker.py fantoma-browser:/app/fantoma/state_tracker.py
docker cp fantoma/planner.py fantoma-browser:/app/fantoma/planner.py
docker cp fantoma/navigator.py fantoma-browser:/app/fantoma/navigator.py
docker cp fantoma/agent.py fantoma-browser:/app/fantoma/agent.py
docker exec fantoma-browser find /app/fantoma -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
```

- [ ] **Step 2: Verify imports work in container**

```bash
docker exec fantoma-browser python3 -c "
from fantoma.state_tracker import StateTracker
from fantoma.planner import Planner, Subtask, Checkpoint
from fantoma.navigator import Navigator, NavigatorResult
from fantoma.agent import Agent, AgentResult
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 3: Smoke test Agent.run() with a simple task**

```bash
docker exec fantoma-browser python3 -c "
from fantoma.agent import Agent
agent = Agent(llm_url='http://host.docker.internal:8080/v1', max_steps=15)
result = agent.run('What is the title of this page?', start_url='https://example.com')
print(f'success={result.success}, steps={result.steps_taken}')
print(f'data={result.data[:200] if result.data else \"(empty)\"}')
"
```

Expected: `success=True`, some data about "Example Domain"

- [ ] **Step 4: Smoke test Agent.login() still works (regression)**

```bash
docker exec fantoma-browser python3 -c "
from fantoma.browser_tool import Fantoma
f = Fantoma(headless=True)
f.start('https://example.com')
state = f.get_state()
print(f'URL: {state[\"url\"]}')
print(f'Elements: {len(state[\"aria_tree\"])} chars')
f.stop()
print('Fantoma direct use: OK')
"
```

Expected: URL printed, elements shown, `Fantoma direct use: OK`

- [ ] **Step 5: Commit deployment confirmation**

No code changes needed. This step is verification only.

---

### Task 8: Run Benchmark Subset to Validate Improvement

**Files:**
- No code changes — benchmark measurement

- [ ] **Step 1: Run 50-task benchmark subset across 5 sites**

```bash
cd /home/workspace/workbench/fantoma
OPENAI_API_KEY=$(python3 -c "import json; print(json.load(open('benchmark/results/2026-04-06_193412/config.json'))['openai_api_key'])")
python3 -m benchmark \
  --llm "https://api.deepseek.com/v1" \
  --llm-api-key "$(python3 -c "import json; print(json.load(open('benchmark/results/2026-04-06_193412/config.json'))['llm_api_key'])")" \
  --eval-model gpt-4o \
  --workers 1 \
  --max-steps 25 \
  --timeout 150 \
  --limit 10 \
  --site Allrecipes
```

Repeat for: Amazon, Google Search, ArXiv, Booking (10 tasks each = 50 total).

- [ ] **Step 2: Run failure analysis on new results**

```bash
cd /home/workspace/workbench/fantoma
python3 analyze_failures.py benchmark/results/<new_run_id>
```

- [ ] **Step 3: Compare before/after**

Key metrics to compare:
- Overall score (target: >40%, up from 22.1%)
- Loop detection failures (target: <30% of failures, down from 80%)
- Average steps per task
- Per-site improvement on previously zero-score sites

Document results in the benchmark results directory.
