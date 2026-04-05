# WebVoyager Benchmark

Run the [WebVoyager](https://github.com/MinorJerry/WebVoyager) benchmark (643 tasks, 15 websites) against Fantoma.

## Quick Start

```bash
# Local LLM (Hercules)
python -m benchmark --llm http://localhost:8080/v1

# Claude Sonnet
python -m benchmark --llm https://api.anthropic.com/v1 --llm-api-key $ANTHROPIC_API_KEY

# Single task (for debugging)
python -m benchmark --task "GitHub--0" --llm http://localhost:8080/v1

# Single website
python -m benchmark --site GitHub --llm http://localhost:8080/v1
```

## Requirements

- `OPENAI_API_KEY` env var (for GPT-4V evaluation)
- Fantoma installed (`pip install fantoma` or running from source)
- An LLM endpoint (local or cloud)

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--llm` | localhost:8080 | LLM endpoint URL |
| `--llm-api-key` | none | LLM API key (for cloud) |
| `--workers` | 4 | Parallel browser processes |
| `--max-steps` | 30 | Max agent steps per task |
| `--timeout` | 180 | Seconds per task |
| `--task` | none | Run single task by ID |
| `--site` | none | Run one website only |
| `--eval-only DIR` | none | Re-evaluate existing results |
| `--update-readme DIR` | none | Update README from results |
| `--step-screenshots` | off | Capture per-step screenshots |

## Re-evaluate or Update README

```bash
# Re-run GPT-4V evaluation on existing results
python -m benchmark --eval-only benchmark/results/2026-04-05_143000/

# Update README.md with latest scores
python -m benchmark --update-readme benchmark/results/2026-04-05_143000/
```

## Task Data

Tasks are in `data/webvoyager.jsonl` (Magnitude's patched version with updated dates). Skipped tasks are listed in `data/skipped.json`.
