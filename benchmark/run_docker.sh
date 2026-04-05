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
docker cp "$BENCHMARK_SRC" "$CONTAINER:/app/benchmark"

# Ensure httpx is available (needed for evaluator)
docker exec "$CONTAINER" pip3 install -q httpx 2>/dev/null || true

# Pass through OPENAI_API_KEY for evaluation
OPENAI_KEY="${OPENAI_API_KEY:-}"

echo "Starting benchmark inside container..."
docker exec \
    -e OPENAI_API_KEY="$OPENAI_KEY" \
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
