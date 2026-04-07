# Fantoma v0.8 — Hierarchical Agent Architecture

**Date:** 2026-04-07
**Status:** Draft
**Motivation:** WebVoyager benchmark scored 22.1% (130/588). 80% of failures are loop detections. Root cause: single-loop agent overloads the LLM with simultaneous strategy and element selection, has no state tracking, and cannot recover from stagnation.

## Goals

1. Raise WebVoyager score to 45-55% with cheap/local models, 60-70% with Claude Sonnet
2. General-purpose improvements only — no site-specific hacks
3. Preserve external API: `Agent.run()`, `Agent.login()`, `Agent.extract()`, `Fantoma` class
4. Keep the DOM/ARIA-only approach — no screenshots, no vision
5. Fantoma browser tool remains LLM-optional for scripted use

## Non-Goals

- Vision/screenshot support
- Site-specific prompt routing
- Breaking the public API
- Rewriting browser_tool.py or the DOM extraction layer

## Architecture Overview

```
Agent.run(task, start_url)
  |
  v
Planner.decompose(task, page_summary)
  |
  v
[subtask_1, subtask_2, ..., subtask_n]     (2-5 subtasks)
  |
  v
Navigator.execute(subtask, browser, tracker)
  |                         |
  v                         v
  "done" + result     "stagnant" / "failed"
  |                         |
  v                         v
  next subtask         Planner.replan(context)
                            |
                            v
                       [new_subtask_1, ...]  (max 3 replans)
  |
  v
Planner.summarise(task, all_results)
  |
  v
AgentResult (same dataclass as v0.7)
```

## New Files

### 1. `fantoma/planner.py` (~150 lines)

Thin LLM wrapper with three methods. Never sees the ARIA tree or element IDs. Works from page summaries only.

#### Subtask Dataclass

```python
@dataclass
class Subtask:
    instruction: str    # "Search for 'quantum computing' in the search box"
    mode: str           # "interact" | "read" | "find"
    done_when: str      # "Search results are visible on the page"
```

#### Planner.decompose(task, page_summary) -> list[Subtask]

Called once at the start of `Agent.run()`.

Input:
- `task`: the user's natural language task description
- `page_summary`: title, URL, headings, first 500 chars of page content

Output: 2-5 `Subtask` objects.

System prompt (~200 tokens):
```
You break web tasks into 2-5 concrete steps.
For each step, provide:
- instruction: what to do (name specific elements, URLs, search terms)
- mode: "interact" (forms, buttons), "read" (extract info), "find" (locate something on page)
- done_when: how to verify completion

Rules:
- Be specific. "Click the search box and type 'quantum computing'" not "search for it".
- If the task asks to extract information, the last step should be mode "read".
- If you need to search, specify the search term explicitly.
- Return a numbered list, one step per line, in this format:
  1. instruction: ... | mode: ... | done_when: ...

Note: planner mode names are mapped to DOM extraction modes internally:
"find" → "navigate", "interact" → "form", "read" → "content".
```

#### Planner.replan(task, completed_subtasks, failed_subtask, page_summary) -> list[Subtask]

Called when the navigator reports stagnation. Tracks replan count internally (max 3).

System prompt adds:
```
The previous approach failed on this step: {failed_subtask.instruction}
Completed so far: {completed_summary}
Current page: {page_summary}

You MUST try a completely different strategy. Options:
- Navigate directly to a URL instead of clicking through menus
- Use search functionality instead of browsing categories
- Simplify the goal — extract partial information and move on
- Try a different section of the site
```

Each replan gets the previous failed strategy so the planner avoids repeating it.

#### Planner.summarise(task, subtask_results) -> str

Called after all subtasks complete. Combines extracted data from all subtasks into a final answer.

System prompt:
```
You are extracting the answer to a web task from data gathered across multiple pages.
Address every criterion in the task explicitly.
Be specific and complete — vague answers will fail evaluation.
```

Input: task + concatenated results from each navigator subtask.

### 2. `fantoma/navigator.py` (~250 lines)

Executes a single subtask against the browser. Refactored from the current action loop in `agent.py` lines 180-380.

#### Navigator.execute(subtask, fantoma, llm, state_tracker) -> NavigatorResult

```python
@dataclass
class NavigatorResult:
    status: str         # "done" | "stagnant" | "failed" | "max_steps"
    data: str           # extracted content or status description
    steps_taken: int
    steps_detail: list  # same format as current AgentResult.steps_detail
    final_url: str
```

**Step loop (per subtask):**

```
for step in range(step_budget):
    1. Collect mutations from previous action → format as "Change:" line
    2. Get DOM via extract(mode=subtask.mode)
    3. Build prompt: subtask instruction + done_when + Change line + filtered DOM
    4. Call LLM → parse actions
    5. Execute actions via Fantoma methods
    6. Update StateTracker
    7. Check: stagnant? cycling? scroll limit? domain drift? → return early
    8. Check: DONE action? → return "done" with extracted data
```

**Prompt structure (per step):**
```
You control a browser to complete one specific task.

Subtask: {instruction}
Done when: {done_when}

Change: {mutation_feedback}

Page ({url}):
{filtered_DOM}

Pick 1-5 actions (one per line):
CLICK [number]
TYPE [number] "text"
SELECT [number] "option"
SCROLL down|up
NAVIGATE https://...
PRESS Enter
DONE

Rules:
- Match [number] to the element list above.
- Say DONE only when the done_when condition is met.
- DONE ends the sequence.
```

No EVAL/MEMORY/GOAL reflection lines. The navigator executes, it doesn't strategise. This cuts ~30% from the prompt and removes a confusion source for cheap models.

**Step budget:** `max_steps / len(subtasks)`, minimum 5 per subtask. Unused steps from completed subtasks roll over to remaining ones.

**Action parsing:** reuses `_parse_actions()` from current `agent.py` exactly as-is. Moved to a shared `fantoma/actions.py` or kept in navigator.

**Domain drift detection:** after any action that changes the URL, compare current domain to start_url domain. If different and the subtask didn't explicitly mention navigating elsewhere, return `status="failed"` with reason "domain drift".

### 3. `fantoma/state_tracker.py` (~80 lines)

Standalone class, no dependencies beyond `hashlib`, `re`, `collections`.

```python
class StateTracker:
    def __init__(self, window: int = 6):
        self.fingerprints: deque[str]       # MD5 of url+content[:800]
        self.action_norms: deque[str]       # normalised action strings
        self._scroll_count: int = 0
        self._scroll_url: str = ""

    def add(self, url: str, content: str, action_str: str) -> None:
        """Record a step. Call after every action."""
        # Fingerprint
        fp = md5(f"{url}|{content[:800]}".encode()).hexdigest()
        self.fingerprints.append(fp)
        # Normalise action: strip element IDs and outcomes
        norm = re.sub(r"\{'element_id':\s*\d+\}", "{ID}", action_str)
        norm = re.sub(r"\s*->\s*(OK|FAILED|ERROR)", "", norm)
        self.action_norms.append(norm.strip())
        # Scroll tracking
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
        """Convenience: check all conditions, return (should_stop, reason)."""
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

## Changed Files

### 4. `fantoma/agent.py` — Refactored Orchestrator (~300 lines)

The current 522-line file gets significantly simpler. The action loop moves to `navigator.py`. The orchestration logic replaces it.

**Agent.__init__():** adds `self._planner = Planner(llm)` alongside existing fields. All constructor args unchanged.

**Agent.run():**
```python
def run(self, task: str, start_url: str = None) -> AgentResult:
    try:
        state = self.fantoma.start(start_url)
    except Exception as e:
        return AgentResult(success=False, error=f"Browser start failed: {e}")

    try:
        summary = self._get_page_summary()
        subtasks = self._planner.decompose(task, summary)
        completed = []
        all_steps = []
        total_steps = 0
        remaining_budget = self._max_steps

        for subtask in subtasks:
            step_budget = max(5, remaining_budget // max(1, len(subtasks) - len(completed)))
            tracker = StateTracker()

            result = self._navigator.execute(
                subtask=subtask,
                fantoma=self.fantoma,
                llm=self._llm,
                tracker=tracker,
                max_steps=step_budget,
                start_domain=self._start_domain,
            )

            all_steps.extend(result.steps_detail)
            total_steps += result.steps_taken
            remaining_budget -= result.steps_taken

            if result.status == "done":
                completed.append((subtask, result))
                continue

            if result.status in ("stagnant", "failed", "max_steps"):
                summary = self._get_page_summary()
                new_subtasks = self._planner.replan(task, completed, subtask, summary)
                if new_subtasks is None:
                    # Max replans reached — extract what we have
                    break
                subtasks = new_subtasks + subtasks[subtasks.index(subtask)+1:]
                continue

        answer = self._planner.summarise(task, completed)
        return AgentResult(
            success=bool(completed),
            data=answer,
            steps_taken=total_steps,
            steps_detail=all_steps,
        )
    except Exception as e:
        return AgentResult(success=False, error=str(e))
    finally:
        self.fantoma.stop()
```

**Agent.login(), Agent.extract(), Agent.session():** unchanged. They bypass planner/navigator entirely.

**_get_page_summary():** new private method. Returns title + URL + headings + first 500 chars of `extract_content()`. Used by planner, never by navigator.

**Removed from agent.py:** `REACTIVE_PROMPT`, `EXTRACTION_PROMPT`, `COMPACTION_PROMPT`, `_parse_actions()`, `_parse_reflection()`, `_format_history()`, the entire action loop. These move to `navigator.py` (action parsing) or are replaced by planner/navigator prompts.

### 5. `fantoma/dom/accessibility.py` — Mode Parameter

`extract()` method gets an optional `mode` parameter:

```python
def extract(self, page, task: str = "", mode: str = "find") -> str:
```

Modes:
These align with existing mode names in `accessibility.py`:

- **`navigate`** (default, current behaviour): interactive elements + headings + content. Full ARIA tree as today. Maps to planner mode `"find"`.
- **`form`**: interactive elements only (forms, buttons, links). Headings included for context. No page content text. ~40% smaller. Maps to planner mode `"interact"`.
- **`content`**: page content text + headings via `extract_aria_content()`. Only navigation links from interactive elements. ~50% smaller. Maps to planner mode `"read"`.

Implementation: the extractor already separates interactive elements, headings, and content internally. The mode parameter controls which sections appear in the output string. Minimal code change — a few `if mode` guards around the output assembly.

### 6. Mutation Feedback Wiring

In `navigator.py`, after each action execution:

```python
from fantoma.browser.observer import collect_mutations, format_mutations

mutations = collect_mutations(page)
change_line = format_mutations(mutations)
if not change_line:
    change_line = "No changes detected"
```

This `change_line` is prepended to the next step's prompt as `Change: {change_line}`.

`inject_observer()` is already called inside `browser_tool.py` actions (click, type_text, select, fill_by_selector). No changes needed there. `collect_mutations()` is called by the navigator immediately after the action method returns and before the next LLM call. This immediate post-action timing is critical — batching or delaying mutations breaks the action-reaction coherence that helps the LLM understand what its last action did.

## Recovery Strategy

### Checkpointing

The orchestrator saves a checkpoint (URL) after each successfully completed subtask. When the navigator reports stagnation, the planner can include a `backtrack_to` URL in the new subtask list, sending the navigator back to a known-good page state before trying a new approach. This matches the backtracking strategy used by Agent-E and BacktrackAgent (EMNLP 2025), which is more reliable than replan-only recovery.

Checkpoint data stored per completed subtask:
```python
@dataclass
class Checkpoint:
    url: str                # page URL after subtask completed
    subtask: Subtask        # what was accomplished
    result_summary: str     # short description of what was found
```

### Replan Cycle

When the navigator returns stagnation to the planner, the replan cycle works as follows:

**Replan 1:** backtrack to last checkpoint URL, try a different navigation path (e.g. use search instead of menus, or direct URL).
**Replan 2:** backtrack to start_url, simplify the goal (extract partial information from what's visible).
**Replan 3:** accept best available and return (extract from current page, answer with what's known).

Each replan clears the navigator's history and StateTracker, breaking LLM context momentum. The planner sees what was tried before (including URLs) and is prompted to avoid repeating it.

After 3 failed replans, the agent extracts whatever is on the current page and returns `AgentResult(success=True, data=partial_answer)`. This is better than the current behaviour of returning `success=False` with no data.

## Escalation Chain Integration

The existing `EscalationChain` from `resilience/escalation.py` moves from the navigator loop to the planner level. If all 3 replans fail with the current LLM, and escalation is available, the planner escalates to a more capable model and retries with a fresh decomposition. This is more effective than the current approach of swapping the LLM mid-loop.

## Testing Strategy

**Unit tests (no browser, no LLM):**
- `test_state_tracker.py`: fingerprint detection, cycle detection, scroll limit, reset
- `test_planner.py`: parse subtask output, replan count limit, mode validation
- `test_navigator.py`: action parsing (moved from agent), step budget allocation, domain drift detection

**Integration tests (mocked LLM, real browser):**
- Planner decomposition with various task types
- Navigator execution on a local test page
- Full Agent.run() with mocked LLM responses
- Stagnation → replan → recovery flow
- Verify Agent.login() and Agent.extract() still work (regression)

**Benchmark validation:**
- Run 50-task subset across 5 sites before/after
- Compare loop detection rate, average steps, success rate

## Migration

1. New files (`planner.py`, `navigator.py`, `state_tracker.py`) added alongside existing code
2. `agent.py` refactored — old single-loop `run()` replaced with orchestrator
3. `dom/accessibility.py` — mode parameter added with backward-compatible default (`find`)
4. All existing tests must pass (Agent.login, Agent.extract, Fantoma direct use)
5. Benchmark re-run to validate improvement

## Known Limitations (v0.8)

These are not addressed by this design and are deferred to future work:

- **Shadow DOM traversal:** modern SPAs (Booking, Coursera) use Shadow DOM components that standard ARIA extraction misses. Future: add Shadow DOM piercing via `page.evaluate()`.
- **Virtual scroll / lazy-loaded lists:** sites that render only visible rows (Booking search results, ArXiv listings) don't expand their DOM on scroll. Future: detect virtual scroll containers via JS and trigger programmatic scroll-to-load.
- **Canvas/WebGL content:** Google Maps visualisations, ESPN scoreboards render to canvas. ARIA tree is empty. Future: this requires vision or JS hooks, which is out of scope for DOM-only.

## Risks

- **Planner decomposition quality with cheap models:** if the planner produces bad subtasks, the navigator can't recover. Mitigation: planner prompt is very simple and structured; tested with DeepSeek and local Hermes before merging.
- **Step budget fragmentation:** splitting steps across subtasks might starve complex subtasks. Mitigation: unused steps roll over; minimum 5 per subtask.
- **Mutation observer reliability:** some SPAs aggressively rebuild the DOM, generating noise. Mitigation: `format_mutations()` already caps output; "No changes detected" is the fallback.
- **Replan-only recovery may not suffice for all sites:** published research (BacktrackAgent, LATS) favours checkpoint-based backtracking, which we include. If replan quality is poor with cheap models, the backtrack-to-checkpoint fallback provides a safety net.

## Success Criteria

- WebVoyager score > 40% with DeepSeek (up from 22.1%)
- WebVoyager score > 55% with Claude Sonnet
- Loop detection failures < 30% of total failures (down from 80%)
- Agent.login() and Agent.extract() regression: zero breakage
- No site-specific code in any new file
