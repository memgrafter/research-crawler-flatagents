#!/usr/bin/env bash
# Research Paper Analysis V2 — three-phase pipeline runner.
#
# Usage:
#   ./run.sh --workers 5 --daemon       # Full pipeline: prep + expensive + wrap
#   ./run.sh --workers 10 --prep-only   # Fill prep buffer only (cheap)
#   ./run.sh --seed-only                # Seed from arxiv DB only
#   ./run.sh -h                         # Show all options
#
# Automatically rebuilds .venv via `uv sync` on every run.
# Logs to logs/run_YYYYMMDD_HHMMSS.log.

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# --- Logging ---------------------------------------------------------------
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
LOG_FILE="${LOG_FILE:-$LOG_DIR/run_${TIMESTAMP}.log}"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "Logging to $LOG_FILE"

# --- File descriptor limit (no sudo needed) --------------------------------
MIN_FDS=10000
CURRENT_FDS="$(ulimit -n)"
if [ "$CURRENT_FDS" -lt "$MIN_FDS" ]; then
  ulimit -n "$MIN_FDS"
  echo "Raised ulimit -n from $CURRENT_FDS to $(ulimit -n)"
fi

# --- Phase concurrency caps ------------------------------------------------
export RPA_V2_MAX_PREP=120
export RPA_V2_MAX_WRAP=180
export RPA_V2_PREP_DOWNLOAD_CONCURRENCY=10
export RPA_V2_DOWNLOAD_USER_AGENT="research_paper_analysis (mailto:memgrafter@gmail.com)"

# --- aiohttp connection pool (litellm uses aiohttp for async HTTP) ---------
export AIOHTTP_CONNECTOR_LIMIT=2000
export AIOHTTP_CONNECTOR_LIMIT_PER_HOST=2000

# --- Venv + deps -----------------------------------------------------------
uv sync

V2_SRC="$SCRIPT_DIR/src"
export PYTHONPATH="$V2_SRC:${PYTHONPATH:-}"

# --- Run -------------------------------------------------------------------
uv run python run.py "$@"
