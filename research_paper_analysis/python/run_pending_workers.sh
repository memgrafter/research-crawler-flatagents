#!/bin/bash
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# --- Parse arguments we handle; pass the rest through ---
MAX_WORKERS=3
DB_PATH_OVERRIDE=""
JSON_LOG=false
PASSTHROUGH_ARGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
        -w|--workers)  MAX_WORKERS="$2"; shift 2 ;;
        --db)          DB_PATH_OVERRIDE="$2"; shift 2 ;;
        -j|--json-log) JSON_LOG=true; shift ;;
        -l|--local|-u|--upgrade) shift ;;  # no-ops, kept for back-compat
        *)             PASSTHROUGH_ARGS+=("$1"); shift ;;
    esac
done

echo "--- Kick Off Pending Workers ---"

# One command: creates venv, installs all deps (respects [tool.uv.sources] for local dev)
uv sync

# Logging
LOG_DIR="${FLATAGENTS_LOG_DIR:-$SCRIPT_DIR/logs}"
mkdir -p "$LOG_DIR"
export FLATAGENTS_LOG_DIR="$LOG_DIR"
export FLATAGENTS_LOG_LEVEL="${FLATAGENTS_LOG_LEVEL:-INFO}"
if [ "$JSON_LOG" = true ]; then
    export FLATAGENTS_LOG_FORMAT="${FLATAGENTS_LOG_FORMAT:-json}"
else
    export FLATAGENTS_LOG_FORMAT="${FLATAGENTS_LOG_FORMAT:-standard}"
fi
echo "📝 Logs: $LOG_DIR (format=$FLATAGENTS_LOG_FORMAT, level=$FLATAGENTS_LOG_LEVEL)"

# DB path
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEFAULT_DB_PATH="$PROJECT_ROOT/arxiv_crawler/data/arxiv.sqlite"
export ARXIV_DB_PATH="${DB_PATH_OVERRIDE:-$DEFAULT_DB_PATH}"

echo "🚀 Running checker (max_workers=$MAX_WORKERS)..."
uv run python run_checker.py --max-workers "$MAX_WORKERS" "${PASSTHROUGH_ARGS[@]}"
