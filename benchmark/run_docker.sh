#!/usr/bin/env bash
# Run the WebVoyager benchmark inside the fantoma-browser Docker container.
# Camoufox requires Xvfb + LD_PRELOAD shim which only exist in the container.
#
# Usage:
#   ./benchmark/run_docker.sh                          # Full run, local LLM
#   ./benchmark/run_docker.sh --task "GitHub--0"       # Single task
#   ./benchmark/run_docker.sh --site GitHub            # Single site
#   ./benchmark/run_docker.sh --eval-only /app/benchmark/results/<run-id>/

set -euo pipefail

CONTAINER="fantoma-browser"
BENCHMARK_SRC="$(cd "$(dirname "$0")" && pwd)"
FANTOMA_SRC="$(cd "$BENCHMARK_SRC/.." && pwd)"

echo "Copying benchmark code to container..."
# Copy CONTENTS of $BENCHMARK_SRC into existing /app/benchmark/ (overwriting matching files).
# Without the trailing /. docker cp would nest the source as /app/benchmark/benchmark/,
# leaving the original /app/benchmark/worker.py loaded — exactly the bug that masked
# the watchdog-leak fix on 2026-04-09.
docker cp "$BENCHMARK_SRC/." "$CONTAINER:/app/benchmark/"
docker exec "$CONTAINER" find /app/benchmark -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

# Ensure httpx is available (needed for evaluator)
docker exec "$CONTAINER" pip3 install -q httpx 2>/dev/null || true

# Pass through API keys and Telegram credentials
OPENAI_KEY="${OPENAI_API_KEY:-}"
CAPSOLVER_KEY_VAL="${CAPSOLVER_KEY:-}"
# Try loading from config file if env var is empty
if [ -z "$CAPSOLVER_KEY_VAL" ] && [ -f "$HOME/.config/capsolver/config.json" ]; then
    CAPSOLVER_KEY_VAL=$(python3 -c "import json; print(json.load(open('$HOME/.config/capsolver/config.json'))['api_key'])" 2>/dev/null || echo "")
fi
TG_TOKEN=$(python3 -c "import json; d=json.load(open('/home/steamvibe/.nanobot/config.json')); print(d['channels']['telegram']['token'])" 2>/dev/null || echo "")
TG_CHAT=$(python3 -c "import json; d=json.load(open('/home/steamvibe/.nanobot/config.json')); print(d['channels']['telegram']['allowFrom'][0])" 2>/dev/null || echo "")

# Default agent LLM to OpenAI GPT-4o when key is available and no explicit override
LLM_URL="${BENCHMARK_LLM_URL:-}"
LLM_MODEL="${BENCHMARK_LLM_MODEL:-}"
if [ -z "$LLM_URL" ] && [ -n "$OPENAI_KEY" ]; then
    LLM_URL="https://api.openai.com/v1"
    LLM_MODEL="${LLM_MODEL:-gpt-4o}"
fi

# Escalation chain pass-through (pipe-separated lists)
ESC_URLS="${BENCHMARK_ESCALATION_URLS:-}"
ESC_KEYS="${BENCHMARK_ESCALATION_KEYS:-}"
ESC_MODELS="${BENCHMARK_ESCALATION_MODELS:-}"

echo "Starting benchmark inside container..."
docker exec \
    -e OPENAI_API_KEY="$OPENAI_KEY" \
    -e BENCHMARK_LLM_URL="$LLM_URL" \
    -e BENCHMARK_LLM_API_KEY="$OPENAI_KEY" \
    -e BENCHMARK_LLM_MODEL="$LLM_MODEL" \
    -e BENCHMARK_ESCALATION_URLS="$ESC_URLS" \
    -e BENCHMARK_ESCALATION_KEYS="$ESC_KEYS" \
    -e BENCHMARK_ESCALATION_MODELS="$ESC_MODELS" \
    -e CAPSOLVER_KEY="$CAPSOLVER_KEY_VAL" \
    -e BENCHMARK_CAPTCHA_API="capsolver" \
    -e TELEGRAM_BOT_TOKEN="$TG_TOKEN" \
    -e TELEGRAM_CHAT_ID="$TG_CHAT" \
    -w /app \
    "$CONTAINER" \
    python3 -m benchmark "$@"

# Copy results back to host
LATEST=$(docker exec "$CONTAINER" ls -t /app/benchmark/results/ 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
    echo "Copying results back to host..."
    mkdir -p "$BENCHMARK_SRC/results"
    docker cp "$CONTAINER:/app/benchmark/results/$LATEST" "$BENCHMARK_SRC/results/$LATEST"
    echo "Results saved to benchmark/results/$LATEST"
fi
