#!/usr/bin/env bash
# Overnight full benchmark run with local local-coder (port 8081).
# Expected duration: ~15 hours (590 tasks, single worker).
#
# Usage: run via systemd timer or at/cron. Not interactive.

# Log first, before anything can fail
LOG="/home/workspace/workbench/fantoma/benchmark/overnight-run.log"
exec >> "$LOG" 2>&1

set -eo pipefail
# Note: no -u flag. bashrc and subshells use unset vars freely.

echo "=== Overnight benchmark started at $(date) ==="

# Load OPENAI_API_KEY from .bashrc (needed for GPT-4o evaluator)
set +e
source "$HOME/.bashrc" 2>/dev/null
set -e

if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: OPENAI_API_KEY not set. Aborting."
    exit 1
fi
echo "OPENAI_API_KEY: set"

# Wait for Atlas lock (model swap might be in progress)
for i in {1..30}; do
    [ ! -f /tmp/atlas_system_busy.lock ] && break
    echo "Atlas lock present, waiting 10s..."
    sleep 10
done

# Verify local-coder is responding
if ! curl -sf http://localhost:8081/v1/models > /dev/null 2>&1; then
    echo "ERROR: local-coder not responding on port 8081. Aborting."
    exit 1
fi

MODEL=$(curl -s http://localhost:8081/v1/models | python3 -c "import json,sys; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null || echo "unknown")
echo "LLM model: $MODEL"

cd /home/workspace/workbench/fantoma

# Copy latest code to container
docker cp benchmark/. fantoma-browser:/app/benchmark/
docker cp fantoma/. fantoma-browser:/app/fantoma/
docker exec fantoma-browser find /app -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

echo "Code deployed to container. Starting benchmark..."

# Run the full benchmark with local local-coder
# - workers=1 (local-coder handles one request at a time)
# - max_steps=50 (default, plenty for flat-first)
# - flat_budget=20 (Phase 1 gets 20 steps)
BENCHMARK_LLM_URL="http://host.docker.internal:8081/v1" \
BENCHMARK_LLM_API_KEY="" \
BENCHMARK_LLM_MODEL="$MODEL" \
OPENAI_API_KEY="${OPENAI_API_KEY}" \
./benchmark/run_docker.sh --workers 1

echo "=== Overnight benchmark finished at $(date) ==="

# Send Telegram notification
TG_TOKEN=$(python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.nanobot/config.json'))); print(d['channels']['telegram']['token'])" 2>/dev/null || echo "")
TG_CHAT=$(python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.nanobot/config.json'))); print(d['channels']['telegram']['allowFrom'][0])" 2>/dev/null || echo "")

if [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ]; then
    LATEST=$(ls -t benchmark/results/ 2>/dev/null | head -1)
    if [ -n "$LATEST" ] && [ -f "benchmark/results/$LATEST/summary.json" ]; then
        SCORE=$(python3 -c "import json; d=json.load(open('benchmark/results/$LATEST/summary.json')); print(f\"{d.get('score', '?')}/{d.get('total', '?')} ({d.get('percentage', '?')}%)\")" 2>/dev/null || echo "check results")
        MSG="Fantoma v0.9 overnight benchmark complete.%0AModel: $MODEL%0AScore: $SCORE%0AResults: benchmark/results/$LATEST"
        curl -s "https://api.telegram.org/bot${TG_TOKEN}/sendMessage?chat_id=${TG_CHAT}&text=${MSG}" > /dev/null 2>&1
    fi
fi
