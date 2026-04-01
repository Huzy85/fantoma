# Fantoma v0.7.0 Publish Readiness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get Fantoma published on PyPI with `pip install fantoma` working end-to-end.

**Architecture:** Fix broken imports from v0.7 refactor, merge branches, publish.

**Tech Stack:** Python, hatchling, twine, GitHub Actions

---

### Task 1: Fix pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update version and description**

```python
# In [project] section:
version = "0.7.0"
description = "Accessibility-first browser automation. Zero mouse telemetry. Works with any LLM."
```

- [ ] **Step 2: Add server optional dependency**

```toml
[project.optional-dependencies]
sessions = ["cryptography>=41.0"]
captcha = ["capsolver"]
chromium = ["patchright>=1.0"]
vision = ["pillow"]
server = ["flask>=3.0"]
dev = ["pytest", "pytest-asyncio"]
all = ["cryptography>=41.0", "capsolver", "pillow", "flask>=3.0"]
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: bump version to 0.7.0, update description and deps"
```

---

### Task 2: Fix CLI imports and constructor calls

**Files:**
- Modify: `fantoma/cli.py`
- Test: run `OPENBLAS_NUM_THREADS=4 python -c "from fantoma.cli import main; print('CLI imports OK')"`

- [ ] **Step 1: Read the full CLI file and identify every broken call**

Read `fantoma/cli.py`. Find every instance of:
- `Agent(` constructor calls — check params match v0.7 signature: `Agent(llm_url, api_key, model, escalation, escalation_keys, max_steps, sensitive_data, **kwargs)`
- `verbose=True` or `verbose=` — remove (doesn't exist)
- `BrowserEngine(` direct usage — replace with `Fantoma(`
- Any import of deleted modules

- [ ] **Step 2: Fix cmd_test() — use Fantoma instead of Agent**

`cmd_test()` does a basic browser test (load a page, check it works). This doesn't need an LLM. Change from Agent to Fantoma:

```python
# Before:
from fantoma import Agent
agent = Agent(llm_url=..., headless=..., verbose=True)

# After:
from fantoma import Fantoma
browser = Fantoma(headless=headless_mode)
state = browser.start("https://example.com")
# check state has aria_tree
browser.stop()
```

- [ ] **Step 3: Fix cmd_test_full() — same pattern**

Uses Agent for multi-site testing. Change browser setup to Fantoma, keep Agent only for the LLM-driven task portion.

- [ ] **Step 4: Fix cmd_test_fingerprint() — use Fantoma**

Currently imports `BrowserEngine` and `FingerprintTest`. `FingerprintTest` still exists but needs a running browser. Create Fantoma, start it, run fingerprint test on the page, stop.

- [ ] **Step 5: Fix cmd_run() — update Agent constructor**

Remove `verbose=True`. Pass `headless`, `proxy`, `browser` as kwargs:

```python
# Before:
agent = Agent(llm_url=url, headless=headless, proxy=proxy, verbose=True)

# After:
agent = Agent(llm_url=url, headless=headless, proxy=proxy)
```

- [ ] **Step 6: Fix cmd_interactive() — update Agent constructor**

Same as cmd_run: remove verbose, pass browser params as kwargs.

- [ ] **Step 7: Verify CLI imports cleanly**

```bash
OPENBLAS_NUM_THREADS=4 python -c "from fantoma.cli import main; print('OK')"
```

- [ ] **Step 8: Commit**

```bash
git add fantoma/cli.py
git commit -m "fix: update CLI for v0.7 API (Fantoma/Agent split)"
```

---

### Task 3: Fix example scripts

**Files:**
- Modify: `examples/simple_search.py`, `examples/data_extraction.py`, `examples/escalation.py`, `examples/form_filling.py`, `examples/local_llm.py`, `examples/multi_tab.py`, `examples/with_proxy.py`
- Create: `examples/tool_api.py`

- [ ] **Step 1: Remove verbose=True from all examples**

Check each file. Remove `verbose=True` from Agent constructor calls.

- [ ] **Step 2: Fix multi_tab.py imports**

Check if `from fantoma.browser.verification import extract_verification_code` still works. If not, update the import path.

- [ ] **Step 3: Update form_filling.py to show Fantoma login**

```python
"""Example: fill out a login form — no LLM needed."""
from fantoma import Fantoma

browser = Fantoma()
browser.start()
result = browser.login(
    "https://the-internet.herokuapp.com/login",
    username="tomsmith",
    password="SuperSecretPassword!",
)
print(f"Success: {result['success']}")
print(f"URL: {result.get('url', 'unknown')}")
browser.stop()
```

- [ ] **Step 4: Create tool_api.py example**

```python
"""Example: drive the browser step by step with the Tool API.

The Tool API gives you full control. Feed the ARIA tree to your own
LLM, parse the response, call browser.click() / browser.type_text().
No built-in LLM needed.
"""
from fantoma import Fantoma

browser = Fantoma()
state = browser.start("https://news.ycombinator.com")

print("Page:", state["title"])
print("ARIA tree (first 500 chars):")
print(state["aria_tree"][:500])
print(f"\nElements on page: feed this to your LLM and ask what to click.")

# Click the first link (element 0)
result = browser.click(0)
print(f"\nClicked: success={result['success']}")
print(f"New page: {result['state']['title']}")

browser.stop()
```

- [ ] **Step 5: Commit**

```bash
git add examples/
git commit -m "docs: update examples for v0.7 API, add tool_api.py"
```

---

### Task 4: Clean install test

**Files:**
- No file changes — verification only

- [ ] **Step 1: Build the wheel**

```bash
cd /home/workspace/workbench/fantoma/.worktrees/tool-separation
pip install build
python -m build
```

- [ ] **Step 2: Install in fresh venv**

```bash
python -m venv /tmp/fantoma-test-venv
source /tmp/fantoma-test-venv/bin/activate
pip install dist/fantoma-0.7.0-py3-none-any.whl
```

- [ ] **Step 3: Test imports**

```bash
python -c "from fantoma import Fantoma, Agent, AgentResult; print('Imports OK')"
python -c "from fantoma import __version__; print(f'Version: {__version__}')"
```

- [ ] **Step 4: Test CLI entry point**

```bash
fantoma --help 2>&1 | head -5
```

- [ ] **Step 5: Fix any missing deps and rebuild if needed**

If imports fail, add missing packages to pyproject.toml dependencies and repeat steps 1-4.

- [ ] **Step 6: Clean up**

```bash
deactivate
rm -rf /tmp/fantoma-test-venv
```

---

### Task 5: Merge branches to main

**Files:**
- No file changes — git operations only

- [ ] **Step 1: Merge v0.6 into main**

```bash
cd /home/workspace/workbench/fantoma
git checkout main
git merge feat/v0.6-navigation-intelligence --no-edit
```

If conflicts: accept v0.6 changes (they'll be overwritten by v0.7 next).

- [ ] **Step 2: Merge v0.7 on top**

```bash
git merge feat/v0.7-tool-separation --no-edit
```

If conflicts: accept v0.7 changes (the final state).

- [ ] **Step 3: Verify tests pass on main**

```bash
OPENBLAS_NUM_THREADS=4 python -m pytest tests/ -x -q --tb=short
```

- [ ] **Step 4: Tag the release**

```bash
git tag -a v0.7.0 -m "v0.7.0 — Tool separation, accessibility-first interaction, PyPI release"
```

Do NOT push yet — Petru will push via Telegram confirmation.

---

### Task 6: GitHub Action for auto-publish

**Files:**
- Create: `.github/workflows/publish.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
name: Publish to PyPI

on:
  push:
    tags:
      - 'v*'

jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install build tools
        run: pip install build

      - name: Build package
        run: python -m build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{ secrets.PYPI_API_TOKEN }}
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/publish.yml
git commit -m "ci: add GitHub Action for PyPI auto-publish on tag"
```

---

### Task 7: First PyPI publish (manual)

This task requires Petru's involvement.

- [ ] **Step 1: Petru creates PyPI account at https://pypi.org/account/register/**
- [ ] **Step 2: Petru creates API token at https://pypi.org/manage/account/token/**
- [ ] **Step 3: Petru adds token as GitHub secret `PYPI_API_TOKEN`**
- [ ] **Step 4: Petru pushes to GitHub (via Telegram confirmation to Nero)**
- [ ] **Step 5: Petru pushes the tag: `git push origin v0.7.0`**
- [ ] **Step 6: Verify `pip install fantoma` works from PyPI**

```bash
pip install fantoma
python -c "from fantoma import Fantoma; print('Published!')"
```
