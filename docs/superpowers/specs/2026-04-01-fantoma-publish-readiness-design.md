# Fantoma v0.7.0 Publish Readiness

**Goal:** Get Fantoma from working-on-a-branch to published on PyPI with `pip install fantoma` working end-to-end.

**Current state:** v0.7.0 code complete on `feat/v0.7-tool-separation` worktree. 355 tests passing. 14/15 real sites passing. Docker container running. CLI and examples have broken imports from the refactor.

**Scope:** Branch merge, fix broken imports, version bump, clean install test, PyPI publish, GitHub Action for auto-publish.

---

## 1. Branch Merge

Merge `feat/v0.6-navigation-intelligence` into `main`, then merge `feat/v0.7-tool-separation` on top. v0.7 deleted most v0.6 additions (executor.py, action_parser.py, etc.) so conflicts resolve by accepting v0.7's deletions. Tag `v0.7.0` on main after all fixes are done.

## 2. pyproject.toml

- Version: `0.7.0`
- Description: `"Accessibility-first browser automation. Zero mouse telemetry. Works with any LLM."`
- Add `flask>=3.0` to optional deps under `[project.optional-dependencies] server` (only needed for Docker/server use)
- Keep existing optional deps: sessions, captcha, chromium, vision, dev

## 3. CLI Fixes

File: `fantoma/cli.py` (1,083 lines, 8 commands)

Broken imports/calls to fix:
- `Agent(verbose=True)` — remove `verbose` param (doesn't exist in v0.7)
- `Agent(llm_url=..., headless=..., proxy=...)` — headless/proxy now go through `**kwargs` to Fantoma
- `cmd_test()` creates Agent for basic browser test — should use Fantoma (no LLM needed for test)
- `cmd_test_full()` same issue
- `cmd_test_fingerprint()` uses `BrowserEngine` directly — should use Fantoma
- `cmd_interactive()` uses Agent — keep as-is but fix constructor params
- `cmd_run()` uses Agent — keep as-is but fix constructor params

Approach: minimal fixes. Update constructor calls, remove dead params. Don't rewrite.

## 4. Example Scripts

7 files in `examples/`. Fixes:
- Remove `verbose=True` from all scripts (param doesn't exist)
- `multi_tab.py`: verify `fantoma.browser.verification` import still works
- Add `tool_api.py`: new example showing the Fantoma class step-by-step (the v0.7 selling point)

## 5. Version Bump

- `pyproject.toml`: `version = "0.7.0"`
- `fantoma/__init__.py`: already says `__version__ = "0.7.0"` (done)

## 6. Clean Install Test

- Build wheel: `python -m build`
- Create fresh venv, install wheel, run:
  - `python -c "from fantoma import Fantoma, Agent; print('OK')"`
  - `fantoma --help` (CLI entry point works)
- Fix any missing dependencies found

## 7. PyPI Publishing

- Manual first publish with `twine upload` to claim the name
- GitHub Action `.github/workflows/publish.yml`:
  - Trigger: push tag `v*`
  - Steps: checkout, build wheel, publish to PyPI
  - Needs `PYPI_API_TOKEN` secret in GitHub repo settings
- Petru creates PyPI account and API token, adds to GitHub secrets

## 8. Git Housekeeping

- Tag `v0.7.0` on main after all fixes
- Delete merged feature branches (local + remote)
- Keep worktree until confirmed working

## Out of Scope

- New features
- Refactoring beyond what's broken
- Docker changes (container already working)
- Live site test suite automation
