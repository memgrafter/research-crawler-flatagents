#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
LOG_FILE="${LOG_FILE:-$LOG_DIR/single_worker_${TIMESTAMP}.log}"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Logging to $LOG_FILE"

# Ensure v2 + local flatagents/flatmachines deps are synced.
uv sync

# Ensure local v2 package is importable when running directly.
V2_SRC="$SCRIPT_DIR/src"
export PYTHONPATH="$V2_SRC:${PYTHONPATH:-}"

uv run python run_single_worker.py "$@"
