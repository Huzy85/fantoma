# Fantoma Agent Reflection + Scroll Hints

Inspired by Alibaba's page-agent. Three changes to improve agent quality for WebVoyager benchmark and general task completion.

## Problem

Three agent quality issues block benchmark success (0/16 on first WebVoyager run):

1. **Agent says DONE too early.** No self-evaluation. Finds a recipe, doesn't check if it meets the criteria (star rating, prep time, review count).
2. **No answer extraction on DONE.** Returns raw ARIA tree or a recipe name. Doesn't pull the data the task actually asks for.
3. **LLM scrolls blindly.** No information about how much content is above/below the viewport. Agent can't decide whether to scroll.

Loop detection (same-pattern-different-element sequences) is a fourth issue but gets addressed indirectly: reflection forces the LLM to evaluate whether it's making progress, catching loops before the hard safety net.

## Approach: Reflection-Only (no separate planner)

Page-agent (Alibaba) forces three fields before every action: `evaluation_previous_goal`, `memory`, `next_goal`. No separate planner call. The LLM self-plans each step. This is lighter and cheaper than a two-call planner+executor pattern and fits Fantoma's v0.7 architecture (single agent.py loop, no executor.py).

The existing planner spec (2026-03-31) targeted the old executor.py which was deleted in v0.7. That spec is superseded by this one.

## Change 1: Reflection in REACTIVE_PROMPT

### Current prompt (agent.py lines 34-59)

Asks the LLM for bare action lines: `CLICK [0]`, `TYPE [1] "text"`, `DONE`. No evaluation, no memory, no goal.

### New prompt

```
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
- Reply with ONLY reflection lines + action lines, nothing else.
```

### Example LLM response

```
EVAL: Searched for "chicken parmesan" and results loaded. 12 recipes shown.
MEMORY: On allrecipes.com search results. Need recipe with 4+ stars and under 30min prep. Haven't checked any yet.
GOAL: Click first result to check star rating and prep time.

CLICK [3]
```

### Parsing

New `_parse_reflection(raw)` function extracts EVAL/MEMORY/GOAL lines by prefix before passing the remainder to `_parse_actions()`. Returns a dict:

```python
{"eval": "...", "memory": "...", "goal": "..."}
```

If any reflection line is missing, that field is empty string. Actions still parse normally. This means Hermes (9B) can skip reflection entirely and the agent still works, just without the self-evaluation benefit.

### History format

Currently: list of strings like `"Step 3: click({'element_id': 4}) -> OK"`, last 10 appended as a single assistant message.

New: list of dicts:

```python
{"step": 3, "eval": "...", "memory": "...", "goal": "...", "actions": "CLICK [4] -> OK", "url": "..."}
```

Formatted for the LLM as:

```
Step 3 (allrecipes.com/recipe/...): GOAL: Check prep time | CLICK [4] -> OK | EVAL: Found prep time 25min
```

Compact, one line per step. Last 20 steps included (up from 10 raw strings). If step count exceeds 20, oldest steps are dropped. Token budget stays manageable because each step is one line (~30-50 tokens).

## Change 2: Content-Mode Extraction on DONE

### Current behaviour

`_extract_answer()` takes the navigate-mode ARIA tree (full of element indices), truncates to 4000 chars, asks the LLM to extract data. Often returns element numbers mixed with content.

### New behaviour

When agent says DONE:

1. Re-extract the page using `AccessibilityExtractor.extract_content(page)` (content mode: text only, no element numbers, 30-item cap, groups by region).
2. Build extraction prompt with:
   - Original task
   - Last step's MEMORY field (what the agent thinks it found)
   - Content-mode page text
3. One LLM call to produce the answer.

```python
def _extract_answer(self, task: str, state: dict, memory: str = "") -> str:
    try:
        page = self.fantoma._engine.get_page()
        content = self.fantoma._dom.extract_content(page)
        messages = [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": f"Task: {task}\n\nAgent found: {memory}\n\nPage content:\n{content}"},
        ]
        return self._llm.chat(messages, max_tokens=1000) or ""
    except Exception:
        return state.get("aria_tree", "")[:2000]
```

DONE syntax unchanged. Still a bare keyword. The extraction step is automatic.

## Change 3: Viewport Scroll Hints

### New helper in accessibility.py

```python
def get_scroll_info(page) -> dict | None:
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

### Output format

Before (no scroll context):

```
Page: Allrecipes
URL: https://allrecipes.com/search?q=chicken

Elements (15 of 42):
[0] link "Chicken Parmesan"
```

After:

```
Page: Allrecipes
URL: https://allrecipes.com/search?q=chicken

[Top of page]

Elements (15 of 42):
[0] link "Chicken Parmesan"
...

... 2400 pixels below (3.2 pages) - scroll down for more ...
```

Rules:
- `pixels_above <= 4`: show `[Top of page]`
- `pixels_above > 4`: show `... {pixels} pixels above ({pages} pages) - scroll up for more ...`
- `pixels_below <= 4`: show `[End of page]`
- `pixels_below > 4`: show `... {pixels} pixels below ({pages} pages) - scroll down for more ...`

Called from `extract_aria()`. Page object already available. Falls back silently (no hints) on JS error.

## Files Changed

| File | Change |
|------|--------|
| `fantoma/agent.py` | New REACTIVE_PROMPT. New `_parse_reflection()`. Structured history dicts. Compact history formatting. `_extract_answer()` uses content mode + memory. |
| `fantoma/dom/accessibility.py` | New `get_scroll_info(page)`. `extract_aria()` wraps elements with scroll hints. |
| `tests/test_agent.py` | Reflection parsing tests, history formatting tests. |
| `tests/test_accessibility.py` | Scroll hint formatting tests. |

## What does NOT change

- `browser_tool.py` (Fantoma tool class)
- `browser/actions.py` (click, type, scroll execution)
- `server.py` (API endpoints)
- `agent.login()`, `agent.extract()`, `agent.session()` (code paths, not LLM-driven)
- Loop detection (kept as hard safety net)
- Escalation chain
- DOM extraction modes, pruning, dedup, landmarks, occlusion filtering
- Docker container setup

## Success criteria

1. Reflection fields appear in agent output for Hercules and DeepSeek
2. Hermes (9B) either produces reflection or falls back to actions-only without breaking
3. WebVoyager benchmark score improves from 0/16
4. 25-site live test suite doesn't regress
5. Scroll hints visible in ARIA output for scrollable pages
