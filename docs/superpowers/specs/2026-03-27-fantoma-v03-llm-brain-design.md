# Fantoma v0.3 — LLM as Brain, Code as Hands

**Date**: 2026-03-27
**Status**: Approved
**Location**: `/home/workspace/workbench/fantoma/`

## Problem

Fantoma v0.2 has two paths that don't cooperate well:
- **Code path**: Fast, fills 80% of forms by label matching. When it can't match, it gives up.
- **LLM path**: Smart, but tries to TYPE and CLICK directly — slow, error-prone, wastes tokens.

Result: 15-site test showed fields filled on 13/15, but only 1 actual signup completed. The code gets close but can't finish, and the LLM doesn't help it finish — it tries to redo the work.

## Solution

One LLM call to label unmatched fields. Code does all the filling.

### Flow

```
1. ARIA + raw DOM scan the page (always both, merged)
2. Code matches fields by label lists (EMAIL_LABELS, PASSWORD_LABELS, etc.)
3. If ALL fields matched → code fills them (zero LLM calls)
4. If SOME fields unmatched → one LLM call: "label these elements"
5. LLM responds: [3]=email, [5]=password, [7]=submit, [9]=skip
6. Code fills based on LLM mapping
7. Form Memory records everything
8. Next visit → step 4 is skipped (database has the answer)
```

### The LLM Call

**When**: Only when code found interactive elements but couldn't determine their purpose.

**Prompt** (new, added to `llm/prompts.py`):

```
You are labelling form elements. Given a list of interactive elements on a page,
label each one with its purpose.

Labels: email, username, password, confirm_password, first_name, last_name,
        phone, submit, checkbox_terms, captcha, skip

Respond with ONLY a comma-separated list: [number]=label, [number]=label
Elements you don't recognise or that aren't relevant: label as "skip".
```

**Input**: The unmatched elements from ARIA/DOM, e.g.:
```
Task: Sign up for an account
URL: https://example.com/register

Unmatched elements:
[3] textbox "Identifier"
[5] textbox "Secret"
[7] button "Continue"
[9] checkbox "I accept the Terms of Service"
```

**Output**: `[3]=email, [5]=password, [7]=submit, [9]=checkbox_terms`

**Tokens**: ~150 input, ~30 output. One call per page. Cheaper than one TYPE action in the current LLM path.

### What Code Does With the Labels

| LLM Label | Code Action |
|-----------|-------------|
| `email` | Fill with email credential |
| `username` | Fill with username credential |
| `password` | Fill with password credential |
| `confirm_password` | Fill with password credential (same value) |
| `first_name` | Fill with first_name credential |
| `last_name` | Fill with last_name credential |
| `phone` | Skip (unless phone provided) |
| `submit` | Click it |
| `checkbox_terms` | Check it (click) |
| `captcha` | Pass to CaptchaOrchestrator |
| `skip` | Ignore |

### Form Memory Integration

After LLM labels elements and code fills them:
- Record the LLM mapping in `form_steps` table (same as current recording)
- Next visit to same domain + step: database provides the mapping
- LLM is never called for a site it's already seen

This means the LLM cost is **per-site, not per-visit**. First visit: one LLM call. Every subsequent visit: zero.

## Scope — What Fantoma Owns

Fantoma is a stealth browser agent. Its job:
1. **Get through the gate** — signup, login, CAPTCHA, email verification
2. **Navigate inside** — click, scroll, find things, read pages
3. **Learn from experience** — Form Memory records fields, gets smarter
4. **Hand back the page** — caller gets the content

Fantoma does NOT decide what data to collect or what to do with it. That's the caller's job (Scout, Nero, cron job, human script). Fantoma is the browser that gets you in and moves you around.

## Changes

### `fantoma/browser/form_login.py`

**Current**: When `_classify_fields` finds unmatched elements → gives up or relies on heuristics.

**New**: When unmatched elements exist AND an LLM client is available:

```python
# After _classify_fields returns with unmatched fields
if not matched_all and llm:
    unmatched = [e for e in elements if e not in matched_set]
    mapping = _ask_llm_to_label(llm, unmatched, page.url, task_hint)
    # Apply mapping
    for idx, label in mapping.items():
        el = elements_by_idx[idx]
        if label == "email":
            email_field = el
        elif label == "username":
            username_field = el
        # ... etc
        elif label == "checkbox_terms":
            _get_element(page, dom_extractor, el).click()
        elif label == "captcha":
            captcha_orchestrator.handle(page, screenshot_fn)
```

New function `_ask_llm_to_label(llm, elements, url, task)` — builds prompt, makes one call, parses response.

**Parameter change**: `login()` gets an optional `llm=None` parameter. When provided, enables LLM-assisted labelling. When `None`, behaviour is identical to v0.2.

### `fantoma/agent.py`

Pass `self._llm` to `form_login()` so it can ask for help:

```python
result = form_login(
    browser=browser, dom_extractor=dom,
    email=email, username=username, password=password,
    first_name=first_name, last_name=last_name,
    memory=memory, visit_id=visit_id,
    captcha_config=self.config.captcha,
    llm=self._llm,  # NEW — enables LLM-assisted labelling
)
```

Remove the current code→LLM handoff in `login()` (the `executor.execute_reactive` call after form_login). The LLM is now integrated INTO form_login, not called after it.

### `fantoma/llm/prompts.py`

Add one new prompt:

```python
FIELD_LABELLER_SYSTEM = """\
You are labelling form elements on a web page. Given a list of interactive elements,
identify what each one is for.

Labels: email, username, password, confirm_password, first_name, last_name,
        phone, submit, checkbox_terms, captcha, skip

Rules:
- Label each element with exactly one label.
- If an element is not relevant to login/signup, label it "skip".
- Respond with ONLY: [number]=label, [number]=label
- No explanation, no extra text.\
"""
```

### `fantoma/executor.py`

In `_task_wants_login` / `_try_code_form_fill`: pass the LLM to `form_login` so the code path has the brain available even when called from `agent.run()`.

Remove the current `_try_code_form_fill` credential parsing (regex extraction from task text). Instead, the caller should pass credentials properly via `agent.login()`. If `agent.run()` encounters a form, it should recognise it and ask the LLM what to do — but the LLM advises, code acts.

## What Does NOT Change

- Public API: `agent.run()`, `agent.login()`, `agent.extract()`, `agent.session()`
- Browser engine, anti-detection, proxy, tabs
- DOM extraction (ARIA + raw DOM merge)
- CAPTCHA orchestrator
- Resilience (escalation, memory, checkpoints)
- Form Memory database schema
- CLI
- Config
- All existing label matching (the fast path)

## Testing

- Unit test: LLM labels elements correctly (mock LLM returns expected mapping)
- Unit test: Code fills fields based on LLM mapping
- Unit test: Form Memory records LLM-provided labels
- Unit test: Second visit uses database, zero LLM calls
- Unit test: `llm=None` behaves identically to v0.2
- Unit test: `checkbox_terms` label triggers click
- Integration: Re-run 15-site test, compare results

## Success Criteria

The 15-site test should show:
- More fields filled (especially on sites where code couldn't match labels)
- Terms checkboxes clicked
- Fewer "no fields filled" results
- Form Memory growing with LLM-provided labels
- Zero regression on sites that already worked
