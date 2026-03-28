# Fantoma Full Code Audit — Design Spec

**Date**: 2026-03-28
**Scope**: Every .py file in the repository (core library, tests, tools, examples, CLI)
**Method**: Top-down, data-flow order. Fix as we go. Run tests after each phase.

## Problem

Fantoma has had 5 development sessions of incremental patches. Features were added, bugs fixed, and code paths modified without a full review pass. Specific concerns:

- Config objects passed inconsistently (CaptchaConfig vs FantomaConfig, fixed once already)
- Return types changed but callers not updated (extraction returning list vs result object)
- CAPTCHA pipeline had silent failures (misdetection, no sitekey wait — both fixed today)
- Dead code from v0.1/v0.2 patterns may still exist
- 770-line form_login.py and 619-line agent.py may have grown beyond single-responsibility

## Per-File Checklist

For every file, check:

1. **Correctness**: types match caller/callee, config passed right, return values consumed properly, error handling doesn't swallow real failures
2. **Dead code**: unused imports, unreachable branches, leftover debug/test code
3. **Boundaries**: file does one job, nothing should be extracted or merged
4. **Duplication**: same logic repeated across files
5. **Naming**: misleading names, inconsistent conventions, comments describing old behaviour

## Fix Policy

- **Fix on the spot**: bugs, type mismatches, broken wiring, dead code, misleading names/comments
- **Flag but don't fix**: large refactors (file splits), feature gaps, performance optimisation
- Flagged items go into a "Follow-up" section at the bottom of PROGRESS.md

## Audit Order (13 Phases)

### Phase 1: Config
- `fantoma/config.py`

### Phase 2: Core orchestration
- `fantoma/agent.py`
- `fantoma/executor.py`
- `fantoma/planner.py`

### Phase 3: Browser core
- `fantoma/browser/engine.py`
- `fantoma/browser/actions.py`
- `fantoma/browser/humanize.py`

### Phase 4: Form handling
- `fantoma/browser/form_login.py`
- `fantoma/browser/form_assist.py`
- `fantoma/browser/form_memory.py`

### Phase 5: Post-login flows
- `fantoma/browser/consent.py`
- `fantoma/browser/email_verify.py`
- `fantoma/browser/verification.py`

### Phase 6: CAPTCHA
- `fantoma/captcha/detector.py`
- `fantoma/captcha/sitekey.py`
- `fantoma/captcha/orchestrator.py`
- `fantoma/captcha/api_solver.py`
- `fantoma/captcha/pow_solver.py`
- `fantoma/captcha/human_solver.py`
- `fantoma/captcha/telegram_solver.py`

### Phase 7: DOM
- `fantoma/dom/extractor.py`
- `fantoma/dom/accessibility.py`
- `fantoma/dom/diff.py`
- `fantoma/dom/selectors.py`

### Phase 8: LLM
- `fantoma/llm/client.py`
- `fantoma/llm/prompts.py`
- `fantoma/llm/vision.py`

### Phase 9: Resilience
- `fantoma/resilience/escalation.py`
- `fantoma/resilience/checkpoint.py`
- `fantoma/resilience/memory.py`

### Phase 10: Browser stealth
- `fantoma/browser/stealth.py`
- `fantoma/browser/fingerprint.py`
- `fantoma/browser/proxy.py`

### Phase 11: CLI
- `fantoma/cli.py`

### Phase 12: Tests
- All files in `tests/`

### Phase 13: Tools and scripts
- All files in `tools/`
- Root scripts: `hn_post.py`, `stress_test.py`, `stress_audit.py`, `weekly_monitor.py`
- All files in `examples/`

## Checkpoints

After each phase:
1. Run `python3 -m pytest tests/ -q --tb=short`
2. Confirm no regressions
3. If tests break, fix before moving to next phase

## Output

- Clean, committed code after all phases
- Summary of all changes in PROGRESS.md
- Follow-up items (large refactors, feature gaps) listed separately
