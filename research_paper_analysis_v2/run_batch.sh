#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
LOG_FILE="${LOG_FILE:-$LOG_DIR/batch_scheduler_${TIMESTAMP}.log}"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "Logging to $LOG_FILE"

uv sync

V2_SRC="$SCRIPT_DIR/src"
export PYTHONPATH="$V2_SRC:${PYTHONPATH:-}"

uv run python run_batch.py "$@"
