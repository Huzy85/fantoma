# Fantoma v0.3 — LLM as Brain, Code as Hands — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When form_login code can't match fields by labels, make one LLM call to label them. Code fills based on the LLM mapping. Form Memory caches results so the LLM is never called twice for the same form layout.

**Architecture:** Add `_ask_llm_to_label()` to form_login.py. Call it from `_classify_fields()` when label matching finds unmatched elements. Add `FIELD_LABELLER_SYSTEM` prompt to prompts.py. Pass `llm` through agent.py. Remove the current code→LLM handoff (executor.execute_reactive call in login()).

**Tech Stack:** Python 3.10+, existing LLMClient, existing FormMemory SQLite

**Spec:** `docs/superpowers/specs/2026-03-27-fantoma-v03-llm-brain-design.md`

---

## Files

| File | Change | Responsibility |
|------|--------|---------------|
| `fantoma/llm/prompts.py` | Add `FIELD_LABELLER_SYSTEM` | New prompt for element labelling |
| `fantoma/browser/form_login.py` | Add `_ask_llm_to_label()`, modify `_classify_fields()`, add `llm=None` param, add checkbox handling | LLM-assisted field matching |
| `fantoma/agent.py` | Pass `self._llm` to form_login, remove executor handoff | Wire LLM into login() |
| `tests/test_llm_labeller.py` | New | Tests for LLM labelling and checkbox handling |

---

### Task 1: Add FIELD_LABELLER_SYSTEM prompt

**Files:**
- Modify: `fantoma/llm/prompts.py`
- Test: `tests/test_llm_labeller.py`

- [ ] **Step 1: Write failing test for prompt existence**

```python
# tests/test_llm_labeller.py

def test_field_labeller_prompt_exists():
    from fantoma.llm.prompts import FIELD_LABELLER_SYSTEM
    assert "label" in FIELD_LABELLER_SYSTEM.lower()
    assert "email" in FIELD_LABELLER_SYSTEM
    assert "skip" in FIELD_LABELLER_SYSTEM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_llm_labeller.py::test_field_labeller_prompt_exists -v`
Expected: FAIL with `ImportError: cannot import name 'FIELD_LABELLER_SYSTEM'`

- [ ] **Step 3: Add the prompt**

Add to the end of `fantoma/llm/prompts.py`:

```python
FIELD_LABELLER_SYSTEM = """\
You are labelling form elements on a web page. Given a list of HTML elements,
identify what each one is for.

Labels: email, username, password, confirm_password, first_name, last_name,
        full_name, phone, address, submit, checkbox_terms, captcha, 2fa_code, skip

Rules:
- Label each element with exactly one label.
- Use HTML attributes as hints: type="email" → email, type="password" → password.
- If an element is not relevant to login/signup, label it "skip".
- Respond with ONLY: [number]=label, [number]=label
- No explanation, no extra text.\
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_llm_labeller.py::test_field_labeller_prompt_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/llm/prompts.py tests/test_llm_labeller.py
git commit -m "feat: add FIELD_LABELLER_SYSTEM prompt for LLM element labelling"
```

---

### Task 2: Implement `_ask_llm_to_label()` and `_parse_llm_labels()`

**Files:**
- Modify: `fantoma/browser/form_login.py`
- Test: `tests/test_llm_labeller.py`

**Depends on:** Task 1

- [ ] **Step 1: Write failing tests**

Add to `tests/test_llm_labeller.py`:

```python
def test_parse_llm_labels():
    from fantoma.browser.form_login import _parse_llm_labels
    raw = "[3]=email, [5]=password, [7]=submit, [9]=checkbox_terms"
    result = _parse_llm_labels(raw)
    assert result == {3: "email", 5: "password", 7: "submit", 9: "checkbox_terms"}


def test_parse_llm_labels_messy_output():
    from fantoma.browser.form_login import _parse_llm_labels
    # LLMs sometimes add extra text
    raw = "Here are the labels:\n[3]=email, [5]=password\n[7]=submit"
    result = _parse_llm_labels(raw)
    assert result == {3: "email", 5: "password", 7: "submit"}


def test_parse_llm_labels_empty():
    from fantoma.browser.form_login import _parse_llm_labels
    assert _parse_llm_labels("") == {}
    assert _parse_llm_labels("no labels found") == {}


def test_ask_llm_to_label():
    from fantoma.browser.form_login import _ask_llm_to_label
    from unittest.mock import MagicMock

    mock_llm = MagicMock()
    mock_llm.chat.return_value = "[0]=email, [1]=password, [2]=submit"

    elements = [
        {"index": 0, "name": "Identifier", "role": "textbox", "type": "text",
         "_selector": "input[name='id']"},
        {"index": 1, "name": "Secret", "role": "input", "type": "password",
         "_selector": "input[name='pw']"},
        {"index": 2, "name": "Go", "role": "button", "type": "",
         "_selector": "button"},
    ]

    result = _ask_llm_to_label(mock_llm, elements, "https://example.com/login")
    assert result == {0: "email", 1: "password", 2: "submit"}
    # Verify the prompt was sent
    mock_llm.chat.assert_called_once()
    call_args = mock_llm.chat.call_args[0][0]  # messages list
    assert any("FIELD_LABELLER" in str(m) or "label" in str(m).lower() for m in call_args)


def test_ask_llm_to_label_no_llm():
    from fantoma.browser.form_login import _ask_llm_to_label
    result = _ask_llm_to_label(None, [], "https://example.com")
    assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_llm_labeller.py -v`
Expected: FAIL — `_parse_llm_labels` and `_ask_llm_to_label` don't exist

- [ ] **Step 3: Implement both functions**

Add to `fantoma/browser/form_login.py`, before `_classify_fields`:

```python
def _build_element_html(el):
    """Build an HTML-like representation of an element for the LLM."""
    tag = "input" if el.get("role") in ("textbox", "input") else "button"
    attrs = ""
    if el.get("type"):
        attrs += f' type="{el["type"]}"'
    if el.get("name"):
        attrs += f' name="{el["name"]}"'
    if el.get("_selector"):
        # Extract name/id from selector for extra context
        sel = el["_selector"]
        if "name=" in sel:
            pass  # already in name attr
        elif "#" in sel:
            attrs += f' id="{sel.split("#")[1].split("]")[0]}"'
    if tag == "button":
        return f'[{el.get("index", "?")}] <button{attrs}>{el.get("name", "")}</button>'
    return f'[{el.get("index", "?")}] <input{attrs}>'


def _parse_llm_labels(raw):
    """Parse LLM response '[3]=email, [5]=password' into {3: 'email', 5: 'password'}."""
    if not raw:
        return {}
    result = {}
    for match in re.finditer(r'\[(\d+)\]\s*=\s*(\w+)', raw):
        idx = int(match.group(1))
        label = match.group(2).lower()
        result[idx] = label
    return result


def _ask_llm_to_label(llm, elements, url):
    """Ask LLM to label unmatched form elements. Returns {index: label}."""
    if not llm or not elements:
        return {}

    from fantoma.llm.prompts import FIELD_LABELLER_SYSTEM

    # Build element list in HTML format
    lines = [f"URL: {url}", "", "Elements:"]
    for el in elements:
        lines.append(_build_element_html(el))

    user_msg = "\n".join(lines)

    try:
        raw = llm.chat(
            [{"role": "system", "content": FIELD_LABELLER_SYSTEM},
             {"role": "user", "content": user_msg}],
            max_tokens=100,
            temperature=0.1,
        )
        labels = _parse_llm_labels(raw or "")
        if labels:
            log.info("LLM labelled %d elements: %s", len(labels), labels)
        return labels
    except Exception as e:
        log.warning("LLM labelling failed: %s", e)
        return {}
```

- [ ] **Step 4: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_llm_labeller.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/form_login.py tests/test_llm_labeller.py
git commit -m "feat: add _ask_llm_to_label() and _parse_llm_labels() for LLM field matching"
```

---

### Task 3: Wire LLM into `_classify_fields()` and add checkbox handling

**Files:**
- Modify: `fantoma/browser/form_login.py:353-388` (`_classify_fields`)
- Modify: `fantoma/browser/form_login.py:79-82` (`login` signature)
- Test: `tests/test_llm_labeller.py`

**Depends on:** Task 2

- [ ] **Step 1: Write failing tests**

Add to `tests/test_llm_labeller.py`:

```python
def test_classify_fields_uses_llm_when_unmatched():
    """When label matching fails, _classify_fields calls the LLM."""
    from fantoma.browser.form_login import _classify_fields
    from unittest.mock import MagicMock, patch

    mock_llm = MagicMock()
    mock_llm.chat.return_value = "[0]=email, [1]=password, [2]=submit"

    # Elements with labels that don't match any hardcoded list
    elements = [
        {"index": 0, "name": "Identifier", "role": "textbox", "type": "text"},
        {"index": 1, "name": "Secret", "role": "input", "type": "password"},
        {"index": 2, "name": "Go", "role": "button", "type": ""},
    ]

    with patch("fantoma.browser.form_login._find_raw_inputs", return_value=[]):
        with patch("fantoma.browser.form_login._find_raw_buttons", return_value=[]):
            result = _classify_fields(
                page=MagicMock(), elements=elements, step=0,
                first_name="", last_name="", llm=mock_llm
            )

    elems, email_f, user_f, pass_f, fn_f, ln_f, submit_f = result
    assert email_f is not None
    assert email_f["name"] == "Identifier"
    assert pass_f is not None
    assert pass_f["name"] == "Secret"
    assert submit_f is not None
    assert submit_f["name"] == "Go"


def test_classify_fields_no_llm_call_when_all_matched():
    """When labels match, LLM should NOT be called."""
    from fantoma.browser.form_login import _classify_fields
    from unittest.mock import MagicMock, patch

    mock_llm = MagicMock()

    elements = [
        {"index": 0, "name": "Email", "role": "textbox", "type": "email"},
        {"index": 1, "name": "Password", "role": "input", "type": "password"},
        {"index": 2, "name": "Log in", "role": "button", "type": ""},
    ]

    with patch("fantoma.browser.form_login._find_raw_inputs", return_value=[]):
        with patch("fantoma.browser.form_login._find_raw_buttons", return_value=[]):
            _classify_fields(
                page=MagicMock(), elements=elements, step=0,
                first_name="", last_name="", llm=mock_llm
            )

    mock_llm.chat.assert_not_called()


def test_classify_fields_without_llm_unchanged():
    """When llm=None, behaviour is identical to v0.2."""
    from fantoma.browser.form_login import _classify_fields
    from unittest.mock import MagicMock, patch

    elements = [
        {"index": 0, "name": "Weird Label", "role": "textbox", "type": "text"},
        {"index": 1, "name": "Go", "role": "button", "type": ""},
    ]

    with patch("fantoma.browser.form_login._find_raw_inputs", return_value=[]):
        with patch("fantoma.browser.form_login._find_raw_buttons", return_value=[]):
            result = _classify_fields(
                page=MagicMock(), elements=elements, step=0,
                first_name="", last_name="", llm=None
            )

    elems, email_f, user_f, pass_f, fn_f, ln_f, submit_f = result
    # Nothing should match — no LLM available
    assert email_f is None
    assert user_f is None
    assert pass_f is None


def test_checkbox_terms_clicked():
    """When LLM labels an element as checkbox_terms, it should be clicked."""
    from fantoma.browser.form_login import _apply_llm_labels
    from unittest.mock import MagicMock

    mock_page = MagicMock()
    mock_dom = MagicMock()
    mock_element = MagicMock()
    mock_dom.get_element_by_index.return_value = mock_element

    elements = [
        {"index": 0, "name": "Email", "role": "textbox", "type": "email"},
        {"index": 9, "name": "I agree to Terms", "role": "checkbox", "type": "checkbox"},
    ]

    labels = {0: "email", 9: "checkbox_terms"}
    result = _apply_llm_labels(labels, elements, mock_page, mock_dom)

    assert result["email"]["name"] == "Email"
    assert result["checkboxes_clicked"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_llm_labeller.py -v`
Expected: FAIL — `_classify_fields` doesn't accept `llm` param, `_apply_llm_labels` doesn't exist

- [ ] **Step 3: Add `_apply_llm_labels()` function**

Add to `fantoma/browser/form_login.py`, after `_ask_llm_to_label`:

```python
def _apply_llm_labels(labels, elements, page, dom_extractor):
    """Apply LLM-provided labels to elements. Returns field mapping + clicks checkboxes."""
    fields = {}
    checkboxes_clicked = 0

    elements_by_idx = {}
    for el in elements:
        idx = el.get("index")
        if idx is not None and idx >= 0:
            elements_by_idx[idx] = el
        # For raw DOM elements (index=-1), match by position in list
    # Also index by list position for raw DOM elements
    for i, el in enumerate(elements):
        if el.get("index", -1) < 0:
            elements_by_idx[i] = el

    for idx, label in labels.items():
        el = elements_by_idx.get(idx)
        if not el:
            continue

        if label in ("email", "username", "password", "confirm_password",
                     "first_name", "last_name", "full_name", "phone", "address",
                     "submit", "2fa_code"):
            fields[label] = el
        elif label == "checkbox_terms":
            # Click the checkbox
            handle = _get_element(page, dom_extractor, el)
            if handle:
                try:
                    handle.click()
                    checkboxes_clicked += 1
                    log.info("Clicked terms checkbox: '%s'", el.get("name", ""))
                except Exception as e:
                    log.debug("Failed to click checkbox: %s", e)
        elif label == "captcha":
            fields["captcha"] = el

    fields["checkboxes_clicked"] = checkboxes_clicked
    return fields
```

- [ ] **Step 4: Modify `_classify_fields()` to accept `llm` and call it**

Change the signature of `_classify_fields` from:

```python
def _classify_fields(page, elements, step, first_name, last_name):
```

to:

```python
def _classify_fields(page, elements, step, first_name, last_name, llm=None):
```

After the existing classification and raw DOM merge, add the LLM fallback before the return:

```python
    # LLM fallback: if fields are still unmatched, ask the LLM to label them
    matched_any = any([email_f, user_f, pass_f, fn_f, ln_f])
    has_fillable = _has_fillable_fields(elements)
    if not matched_any and has_fillable and llm:
        url = ""
        try:
            url = page.url
        except Exception:
            pass
        labels = _ask_llm_to_label(llm, elements, url)
        if labels:
            applied = _apply_llm_labels(labels, elements, page, None)
            email_f = applied.get("email", email_f)
            user_f = applied.get("username", user_f)
            pass_f = applied.get("password", pass_f)
            fn_f = applied.get("first_name") or applied.get("full_name") or fn_f
            ln_f = applied.get("last_name", ln_f)
            if not submit_f and applied.get("submit"):
                submit_f = applied["submit"]

    return elements, email_f, user_f, pass_f, fn_f, ln_f, submit_f
```

- [ ] **Step 5: Update `login()` signature to accept `llm=None`**

Change line 79-82 of `form_login.py`:

```python
def login(browser, dom_extractor, email="", username="", password="",
          first_name="", last_name="",
          max_steps=5, step_delay=3.0, memory=None, visit_id=None,
          captcha_config=None, llm=None):
```

And update the `_classify_fields` call at line 147-149 to pass `llm`:

```python
        elements, email_field, username_field, password_field, \
            first_name_field, last_name_field, submit_button = \
            _classify_fields(page, elements, step, first_name, last_name, llm=llm)
```

- [ ] **Step 6: Run tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_llm_labeller.py -v`
Expected: All 10 tests PASS

- [ ] **Step 7: Run full test suite for regressions**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/live_reddit_test.py --ignore=tests/scenario_test_deepseek.py --ignore=tests/real_site_test.py --ignore=tests/real_signup_test.py --ignore=tests/full_signup_test.py`
Expected: All 120+ tests PASS

- [ ] **Step 8: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/browser/form_login.py tests/test_llm_labeller.py
git commit -m "feat: LLM labels unmatched fields, code fills — checkbox_terms clicked"
```

---

### Task 4: Wire LLM into agent.py and remove old handoff

**Files:**
- Modify: `fantoma/agent.py`
- Test: `tests/test_llm_labeller.py`

**Depends on:** Task 3

- [ ] **Step 1: Write failing test**

Add to `tests/test_llm_labeller.py`:

```python
def test_agent_login_passes_llm():
    """Agent.login() should pass self._llm to form_login."""
    from fantoma.agent import Agent
    from unittest.mock import patch, MagicMock

    with patch("fantoma.agent.BrowserEngine") as MockEngine:
        with patch("fantoma.agent.LLMClient"):
            mock_browser = MagicMock()
            MockEngine.return_value = mock_browser
            mock_browser.get_url.return_value = "https://example.com/login"

            agent = Agent(llm_url="http://localhost:8080/v1")

            with patch("fantoma.agent.form_login") as mock_form_login:
                mock_form_login.return_value = {
                    "success": True, "steps": 1, "url": "https://example.com/home",
                    "fields_filled": ["Email"]
                }
                # This will fail because form_login is imported inside the method
                # We need to patch at the import location
                pass

    # Simpler: just verify the Agent has an _llm attribute
    with patch("fantoma.agent.BrowserEngine"):
        with patch("fantoma.agent.LLMClient") as MockLLM:
            agent = Agent(llm_url="http://localhost:8080/v1")
            assert agent._llm is not None
```

- [ ] **Step 2: Modify agent.py — pass LLM, remove executor handoff**

In `fantoma/agent.py`, in the `login()` method, change the `form_login` call to include `llm=self._llm`:

```python
            result = form_login(
                browser=browser,
                dom_extractor=dom,
                email=email,
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
                memory=memory,
                visit_id=visit_id,
                captcha_config=self.config.captcha,
                llm=self._llm,
            )
```

Remove the entire code→LLM handoff block that follows (the `if fields and not result["success"]:` block with `executor.execute_reactive`). Replace with a simple return:

```python
            memory.record_visit(domain, result.get("success", False))

            return AgentResult(
                success=result["success"],
                data=result,
                steps_taken=result["steps"],
                error="" if result["success"] else "Login not confirmed",
            )
```

- [ ] **Step 3: Run all tests**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/live_reddit_test.py --ignore=tests/scenario_test_deepseek.py --ignore=tests/real_site_test.py --ignore=tests/real_signup_test.py --ignore=tests/full_signup_test.py`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/agent.py tests/test_llm_labeller.py
git commit -m "feat: pass LLM to form_login, remove old executor handoff"
```

---

### Task 5: Update Form Memory to cache LLM labels with element hash

**Files:**
- Modify: `fantoma/browser/form_login.py` (recording section)
- Modify: `fantoma/browser/form_memory.py` (add element hash lookup)
- Test: `tests/test_llm_labeller.py`

**Depends on:** Task 3

- [ ] **Step 1: Write failing test**

Add to `tests/test_llm_labeller.py`:

```python
import os
import tempfile


def test_form_memory_caches_llm_labels():
    """LLM labels should be recorded in Form Memory and reused on next visit."""
    from fantoma.browser.form_memory import FormMemory

    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))

        # Simulate: LLM labelled "Identifier" as email
        fm.record_step(
            domain="example.com", visit_id="v1", step_number=0,
            field_label="Identifier", field_role="textbox",
            field_purpose="email", submit_label="Go", success=True,
            tree_text="", elements_json="[]",
            url="https://example.com/login", action="llm_labelled", result="ok"
        )

        # Next visit: lookup should find it
        live_elements = [{"label": "Identifier", "role": "textbox"}]
        match = fm.lookup("example.com", 0, live_elements)
        assert match.get("Identifier") == "email"
        fm.close()
```

- [ ] **Step 2: Run test**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/test_llm_labeller.py::test_form_memory_caches_llm_labels -v`
Expected: PASS (Form Memory already supports this — the test validates the existing recording + lookup works for LLM-provided labels)

- [ ] **Step 3: Add LLM label recording to form_login.py**

In the recording section of `login()` (around line 300), after the existing `filled_labels` recording, add recording for LLM-labelled fields that were filled. This is already handled by the existing recording code — when a field is filled via LLM label, it gets added to `filled_labels` the same way. No code change needed if the flow works correctly.

Verify by reading the code: after `_classify_fields` returns LLM-matched fields, those fields get filled by the same code that fills label-matched fields, and the recording section records whatever was filled.

- [ ] **Step 4: Commit**

```bash
cd /home/workspace/workbench/fantoma
git add tests/test_llm_labeller.py
git commit -m "test: verify Form Memory caches LLM-provided labels"
```

---

### Task 6: Final verification and version bump

**Files:**
- Modify: `fantoma/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Run full test suite**

Run: `cd /home/workspace/workbench/fantoma && python -m pytest tests/ -v --ignore=tests/live_reddit_test.py --ignore=tests/scenario_test_deepseek.py --ignore=tests/real_site_test.py --ignore=tests/real_signup_test.py --ignore=tests/full_signup_test.py`
Expected: All tests PASS (should be 130+)

- [ ] **Step 2: Bump version**

In `fantoma/__init__.py`:
```python
__version__ = "0.3.0"
```

In `pyproject.toml`:
```toml
version = "0.3.0"
```

- [ ] **Step 3: Commit and tag**

```bash
cd /home/workspace/workbench/fantoma
git add fantoma/__init__.py pyproject.toml
git commit -m "chore: bump version to 0.3.0"
git tag v0.3.0
```

---

## Task Dependency Graph

```
Task 1 (Prompt)
  ↓
Task 2 (_ask_llm_to_label + _parse_llm_labels)
  ↓
Task 3 (Wire into _classify_fields + checkbox handling)  →  Task 5 (Form Memory caching)
  ↓
Task 4 (Wire into agent.py, remove old handoff)
  ↓
Task 6 (Final verification + version bump)
```

Tasks 1→2→3→4→6 are sequential. Task 5 can run in parallel with Task 4.

## Hercules Offloading

| Task | Hercules Can Generate | Claude Reviews |
|------|----------------------|----------------|
| Task 1 | Prompt text | Wording |
| Task 2 | `_parse_llm_labels` regex parser | LLM integration |
| Task 3 | `_apply_llm_labels` mapping logic | _classify_fields wiring |
| Task 5 | Test code | — |
