# WebVoyager Benchmark for Fantoma

**Date:** 2026-04-05
**Status:** Design approved
**Goal:** Run the WebVoyager benchmark (643 tasks, 15 websites) against Fantoma and publish results on the GitHub README alongside competitor scores.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| LLM for agent | Both Hercules (local) and Claude Sonnet (cloud) | Local score proves vendor independence, Sonnet score competes on the leaderboard |
| README presentation | Comparison table + full breakdown page | Table sells it, detailed page adds credibility |
| Evaluator | GPT-4V (standard) | Scores are directly comparable to every other agent on the Steel.dev leaderboard |
| Task scope | All 643, skip known-broken, report on viable subset | Same approach as browser-use (586 of 643). Patch stale dates, document skips. |
| Approach | Native Python harness in the Fantoma repo | Same language, direct API integration, extensible to other benchmarks later |
| Parallelism | 4 workers (4 browser processes) | Fantoma is mostly code-driven (LLM not bottleneck), cuts runtime from 10+ hours to ~2.5 hours |

## Architecture

```
benchmark/
├── __init__.py
├── __main__.py        # CLI entry point
├── runner.py          # Orchestrator: loads tasks, distributes to workers, collects results
├── worker.py          # Single-task executor: Agent.run() + screenshot capture
├── evaluator.py       # GPT-4V judge: standard WebVoyager auto-eval prompt
├── tasks.py           # Task loader: reads JSONL, filters skipped, supports CLI filters
├── results.py         # Aggregation: JSON summary, Markdown tables, README updater
├── config.py          # BenchmarkConfig dataclass
├── data/
│   ├── webvoyager.jsonl       # Patched WebVoyager tasks (updated dates)
│   └── skipped.json           # Task IDs excluded with reasons
├── results/                   # Git-ignored, generated per run
│   └── <run-id>/
│       ├── summary.json
│       ├── summary.md
│       └── tasks/
│           └── <task-id>/
│               ├── result.json
│               ├── result.eval.json
│               ├── screenshot_final.png
│               └── screenshots/     # Per-step screenshots (optional)
└── README.md          # How to run the benchmark
```

## Components

### Runner (`runner.py`)

The orchestrator. Entry point for the benchmark.

1. Loads benchmark config from CLI args / env vars / `benchmark.yaml`.
2. Loads tasks via `tasks.py`, applying filters (`--site`, `--task`).
3. Splits tasks into 4 chunks (one per worker).
4. Spawns 4 worker processes via `concurrent.futures.ProcessPoolExecutor`.
5. Each worker process runs tasks sequentially (one browser session per task).
6. Collects all results into `results/<run-id>/`.
7. Calls evaluator on all completed tasks.
8. Calls results aggregator to produce `summary.json` and `summary.md`.

Run ID format: `YYYY-MM-DD_HHMMSS` (e.g. `2026-04-05_143000`).

CLI:

```bash
# Full run, 4 workers, local LLM
python -m benchmark --llm http://localhost:8080/v1 --workers 4

# Full run, Claude Sonnet
python -m benchmark --llm https://api.anthropic.com/v1 --llm-api-key $ANTHROPIC_API_KEY

# Single task for debugging
python -m benchmark --task "Allrecipes--0" --llm http://localhost:8080/v1

# Single website
python -m benchmark --site GitHub --llm http://localhost:8080/v1

# Re-evaluate existing results without re-running agent
python -m benchmark --eval-only results/2026-04-05_143000/

# Update README with latest results
python -m benchmark --update-readme results/2026-04-05_143000/
```

### Worker (`worker.py`)

Runs a single benchmark task in its own process.

1. Creates a `Fantoma` instance (own browser, own asyncio loop per process).
2. Creates an `Agent` wrapping it, configured from `BenchmarkConfig`.
3. Calls `agent.run(task["ques"], start_url=task["web"])`.
4. After the agent finishes (DONE or timeout), captures a final screenshot via `fantoma.screenshot()`.
5. Calls `fantoma.stop()` to clean up the browser session.
6. Returns a `TaskResult` dict:
   ```python
   {
       "task_id": "Allrecipes--0",
       "web_name": "Allrecipes",
       "instruction": "Provide a recipe for...",
       "start_url": "https://www.allrecipes.com/",
       "status": "completed",           # completed | timeout | error
       "answer": "The top recipe is...", # Agent's DONE text
       "final_url": "https://...",
       "steps_taken": 12,
       "steps_detail": [...],           # Action-by-action log
       "duration_s": 94.3,
       "tokens_used": 2340,
       "error": null
   }
   ```
7. Saves `result.json` and `screenshot_final.png` to the task's results directory.

Per-step screenshots are optional (`--step-screenshots` flag). When enabled, each action in the agent loop triggers a `fantoma.screenshot()` saved as `screenshots/step_001.png`, `step_002.png`, etc. Off by default (slower, more disk).

### Evaluator (`evaluator.py`)

GPT-4V judge using the standard WebVoyager auto-eval prompt.

For each completed task:
1. Loads the task instruction, agent's answer text, and final screenshot.
2. Sends to GPT-4V (`gpt-4o` model) with the standard evaluation prompt from `MinorJerry/WebVoyager/auto_eval.py`. The prompt instructs the judge to:
   - Compare the task instruction against the screenshot and answer text.
   - Trust the screenshot over the agent's text when they conflict.
   - For multi-part tasks, all parts must succeed.
   - Return `SUCCESS` or `NOT SUCCESS`.
3. Parses the verdict.
4. Saves to `result.eval.json`:
   ```json
   {
       "task_id": "Allrecipes--0",
       "verdict": "SUCCESS",
       "eval_model": "gpt-4o",
       "eval_response": "The screenshot shows a vegetarian lasagna recipe with..."
   }
   ```

Tasks that timed out or errored are auto-scored `NOT SUCCESS` (no GPT-4V call needed).

Evaluation calls run in parallel (async HTTP, no browser needed). All 588+ evaluations complete in a few minutes.

Config: reads `OPENAI_API_KEY` from environment.

### Task Loader (`tasks.py`)

1. Reads `data/webvoyager.jsonl`. Each line:
   ```json
   {"web_name": "Allrecipes", "id": "Allrecipes--0", "ques": "...", "web": "https://..."}
   ```
2. Reads `data/skipped.json`:
   ```json
   {
       "Booking--12": "stale date, no valid future replacement",
       "Google_Flights--8": "requires specific past date range"
   }
   ```
3. Filters out skipped tasks.
4. Applies CLI filters (`--site`, `--task`) if provided.
5. Returns list of task dicts.

Source for `webvoyager.jsonl`: start from Magnitude's `patchedTasks.jsonl` (already has updated dates), review and extend with our own skip list during initial test runs.

### Results (`results.py`)

Produces two outputs from the evaluation results:

**`summary.json`:**
```json
{
    "run_id": "2026-04-05_143000",
    "agent": "fantoma-0.7.0",
    "llm": "hercules-qwen3-coder-next",
    "eval_model": "gpt-4o",
    "total_tasks": 643,
    "skipped": 55,
    "evaluated": 588,
    "success": 470,
    "score_pct": 79.9,
    "avg_steps": 8.3,
    "avg_duration_s": 94,
    "per_site": {
        "Allrecipes": {"total": 42, "evaluated": 40, "success": 34, "score_pct": 85.0, "avg_steps": 7.1, "avg_duration_s": 82},
        "GitHub": {"total": 44, "evaluated": 44, "success": 40, "score_pct": 90.9, "avg_steps": 6.2, "avg_duration_s": 55}
    }
}
```

**`summary.md`:** Markdown with the comparison table and per-site breakdown. Used to update:
- `README.md` benchmark section (comparison table only, via `--update-readme`)
- `docs/benchmark.md` (full breakdown, methodology, skip list, reproduction instructions)

### Config (`config.py`)

```python
@dataclass
class BenchmarkConfig:
    llm_url: str                            # Agent LLM endpoint
    llm_api_key: str | None = None          # For cloud LLMs (Sonnet, etc.)
    eval_model: str = "gpt-4o"              # GPT-4V evaluator model
    openai_api_key: str | None = None       # From env OPENAI_API_KEY
    workers: int = 4                        # Parallel browser processes
    max_steps: int = 30                     # Max agent steps per task
    timeout: int = 180                      # Seconds per task
    browser: str = "camoufox"               # Browser engine
    headless: bool = True                   # Headless mode
    capture_step_screenshots: bool = False  # Per-step screenshots
    results_dir: str = "benchmark/results"  # Output directory
```

Loaded from (in priority order): CLI args > env vars > `benchmark.yaml` > defaults.

## README Output

After a run, the README benchmark section looks like:

```markdown
## Benchmark -- WebVoyager

Tested on [WebVoyager](https://github.com/MinorJerry/WebVoyager)
(643 tasks, 15 live websites). Evaluated by GPT-4V using the standard auto-eval prompt.
588 tasks evaluated (55 skipped for stale dates or impossible requirements).

| Agent | LLM | Score | Avg Steps | Avg Time |
|-------|-----|-------|-----------|----------|
| **Fantoma 0.7.0** | **Hercules (local 32B)** | **XX.X%** | **X.X** | **XXs** |
| **Fantoma 0.7.0** | **Claude Sonnet** | **XX.X%** | **X.X** | **XXs** |
| Surfer 2 | -- | 97.1% | -- | -- |
| Magnitude | Claude Sonnet | 93.9% | -- | -- |
| browser-use | GPT-4o | 89.1% | -- | -- |
| Skyvern 2.0 | -- | 85.9% | -- | -- |

Per-site breakdown and methodology: [docs/benchmark.md](docs/benchmark.md)
```

## Detailed Results Page (`docs/benchmark.md`)

Contains:
- Per-site score table (15 rows, with score/steps/timing per site)
- Comparison with competitors per site (where data is available)
- Skipped tasks list with reasons
- Methodology section (how the benchmark was run, config used, evaluation prompt)
- Reproduction instructions (`pip install fantoma && python -m benchmark ...`)
- Links to raw result JSON for transparency

## Dependencies

- `openai` Python package (for GPT-4V evaluation calls). Already an optional dependency.
- No new system dependencies. Uses existing Fantoma + Camoufox/Chromium.

## What Needs Changing in Fantoma Core

Nothing. The benchmark harness uses `Agent.run()` and `Fantoma.screenshot()` as-is. No modifications to the agent loop, DOM extraction, or browser engine needed.

If we find during testing that the agent needs improvements (better prompts, more steps, etc.), those are separate changes to the agent, not the benchmark harness.
