#!/usr/bin/env bash
set -euo pipefail

# Run quality sentinel inside project virtualenv.
#
# Usage:
#   ./run_quality_sentinel.sh --latest 25
#   ./run_quality_sentinel.sh --daemon -n 30 --latest 25
#   ./run_quality_sentinel.sh --latest 25 --fail-on-warn --json-out logs/quality_sentinel_latest.json

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_PATH="${VENV_PATH:-$SCRIPT_DIR/.venv}"

if [[ ! -x "$VENV_PATH/bin/python" ]]; then
  echo "Virtualenv not found at: $VENV_PATH" >&2
  echo "Create it first (example): uv sync" >&2
  exit 1
fi

DAEMON=false
INTERVAL=30
PASSTHROUGH_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -d|--daemon)
      DAEMON=true
      shift
      ;;
    -n|--interval)
      INTERVAL="${2:-}"
      if [[ -z "$INTERVAL" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      shift 2
      ;;
    --)
      shift
      PASSTHROUGH_ARGS+=("$@")
      break
      ;;
    *)
      PASSTHROUGH_ARGS+=("$1")
      shift
      ;;
  esac
done

CMD=("$VENV_PATH/bin/python" "$SCRIPT_DIR/quality_sentinel.py" "${PASSTHROUGH_ARGS[@]}")

if [[ "$DAEMON" == true ]]; then
  if ! command -v watch >/dev/null 2>&1; then
    echo "watch not found. Install it (e.g., brew install watch) or run without --daemon." >&2
    exit 1
  fi

  printf -v WATCH_CMD '%q ' "${CMD[@]}"
  WATCH_BODY="mkdir -p \"$HOME/code/analysis/v2\"; find \"$SCRIPT_DIR/data\" -maxdepth 1 -type f -name '*.pdf' -delete; if compgen -G \"$SCRIPT_DIR/data/*.md\" > /dev/null; then cp \"$SCRIPT_DIR\"/data/*.md \"$HOME/code/analysis/v2/\"; ${WATCH_CMD}; else echo 'No markdown files found in $SCRIPT_DIR/data'; fi"
  printf -v WATCH_BASH_CMD 'bash -lc %q' "$WATCH_BODY"
  echo "Starting quality sentinel daemon mode via watch (interval=${INTERVAL}s, deleting data/*.pdf and copying data/*.md to ~/code/analysis/v2 each tick)"
  exec watch -n "$INTERVAL" "$WATCH_BASH_CMD"
fi

exec "${CMD[@]}"
