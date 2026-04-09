# WebVoyager Benchmark

Fantoma is evaluated against the [WebVoyager](https://arxiv.org/abs/2401.13919) task suite. WebVoyager is the de-facto open benchmark for browser agents: real sites, real tasks, GPT-4V judge.

The full suite is 643 tasks across 15 sites. We currently run a 5-task pilot subset (one task per site, hardest-first), iterate on agent logic, and only commit to the full suite once the pilot is stable.

## How to Run

```bash
# Inside the fantoma container, full pilot
docker exec fantoma-browser python -m benchmark \
  --llm-url https://api.openai.com/v1 \
  --llm-key "$OPENAI_API_KEY" \
  --llm-model gpt-4o \
  --eval-key "$OPENAI_API_KEY" \
  --eval-model gpt-4o \
  --tasks "Apple--0,Booking--0,Coursera--0,ESPN--0,Google Flights--1" \
  --workers 1 --max-steps 30 --timeout 300 \
  --browser camoufox

# Single site for quick iteration
docker exec fantoma-browser python -m benchmark \
  --llm-url https://api.openai.com/v1 \
  --llm-key "$OPENAI_API_KEY" \
  --llm-model gpt-4o \
  --site Apple --limit 1
```

Results land in `benchmark/results/<timestamp>/` with `summary.md`, `summary.json`, and per-task traces.

## Current Score (v0.8)

5-task pilot, GPT-4o agent + GPT-4o judge, run dir `benchmark/results/2026-04-09_191104/`:

| Site | Result | Steps | Duration |
|------|--------|-------|----------|
| Apple — buy a MacBook Air with specific options | PASS | 15 | 106s |
| Coursera — find a beginner 3D-printing course | PASS | 15 | 100s |
| ESPN — current NBA standings | PASS | 15 | 108s |
| Booking — Mexico hotel for given dates | FAIL | 13 | 131s |
| Google Flights — round trip with flexible dates | FAIL | 15 | 93s |

**Score: 3/5 (60%)**. Average 14.6 steps, 107.5s per task.

## Iteration Log

The pilot was used to drive five rounds of agent-logic changes between v0.7 and v0.8. Browser primitives were untouched in every run.

| Run | Score | Change | Verdict |
|-----|-------|--------|---------|
| 8 (v0.7 baseline) | 1/5 | Pre-v0.8 hierarchical agent | ESPN only |
| 9 | 1/5 | Strict pre-DONE rules + search-first planner policy | Strict pre-DONE rule made the navigator click the same Google Flights submit element 12 times. Soften. |
| **10 (v0.8 baseline)** | **3/5** | **Soft submit rule + post-Google CLICK rule + subtask-cycle similarity trigger** | **Apple, Coursera, ESPN all pass. Shipped.** |
| 11 | 0/5 | Counter trigger fired on every navigator non-done return | ESPN had extracted NBA standings, counter incremented anyway, Google fallback fired late, agent ended on google.com mid-task. Reverted. |
| 12 | 2/5 | Counter gated on `has_real_data` (only count pure dead-ends) | ESPN regressed again (cause not yet pinned). Reverted to Run 10. |

The v0.8 release is Run 10. Run 12's no-progress counter is preserved in `.session_snapshots/agent_run12_candidate.py` for future investigation, but the shipping code uses the simpler similarity-only trigger.

## Open Failures

**Booking — Mexico hotel.** Booking's search server returns `errorc_searchstring_not_found` for the literal string "Mexico" because the destination autocomplete only accepts cities. The agent retries the same string under different action shapes (scroll, click, type, navigate), so the similarity trigger never fires. Fix idea: a destination-translation step in the planner that turns countries into representative cities (Mexico → Cancun) before they hit the search box.

**Google Flights — flexible round trip.** Navigator clicks one element repeatedly without submitting the form. The current similarity check is at the planner level and misses navigator-level loops within a single subtask. Fix idea: tighten the existing `StateTracker` action-cycle detector so a single element clicked >3 times in a row trips it, or move the similarity check down into the navigator.

## How This Compares

| Agent | LLM | Score | Notes |
|-------|-----|-------|-------|
| **Fantoma v0.8** | **GPT-4o** | **60.0%** (5-task pilot) | This repo |
| Surfer 2 | — | 97.1% (full 643) | Closed source, leaderboard |
| Magnitude | Claude Sonnet | 93.9% (full 643) | Closed source, leaderboard |
| browser-use | GPT-4o | 89.1% (full 643) | Open source |
| Skyvern 2.0 | — | 85.9% (full 643) | Open source |

The 60% pilot score is not directly comparable to the full-643 leaderboard scores. Once Booking and Google Flights are solved, the next milestone is a 25-task pilot, then the full suite.

## Why a Pilot Subset

The full 643-task run takes a few hours and costs real money on GPT-4o. Running it after every code change would be wasteful when most failures happen on the same handful of broken approaches. The pilot picks one task per site with the highest baseline failure rate, so any agent-logic change shows up in the pilot before it shows up in the full run.
