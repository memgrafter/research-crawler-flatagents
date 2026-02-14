#!/usr/bin/env bash
set -euo pipefail

# Run quality sentinel inside project virtualenv.
#
# Usage:
#   ./run_quality_sentinel.sh --latest 25
#   ./run_quality_sentinel.sh -d -n 30 --latest 25
#   ./run_quality_sentinel.sh --latest 25 --fail-on-warn --json-out logs/quality_sentinel_latest.json
#
# Daemon PDF cleanup behavior:
#   RPA_V2_SENTINEL_DELETE_PDFS=1      # delete data/*.pdf only when matching .txt exists and is non-empty (default: enabled)
#   RPA_V2_SENTINEL_INITIAL_BACKFILL=0 # on first daemon tick, skip copying historical *.md (default: 0)

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_PATH="${VENV_PATH:-$SCRIPT_DIR/.venv}"
DATA_DIR="$SCRIPT_DIR/data"
ANALYSIS_DIR="$HOME/code/analysis/ml_research_analysis_2024"

if [[ ! -x "$VENV_PATH/bin/python" ]]; then
  echo "Virtualenv not found at: $VENV_PATH" >&2
  echo "Create it first (example): uv sync" >&2
  exit 1
fi

DAEMON=false
INTERVAL=5
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

  read -r -d '' WATCH_BODY <<EOF || true
mkdir -p "$ANALYSIS_DIR"

if [[ "\${RPA_V2_SENTINEL_DELETE_PDFS:-1}" == "1" ]]; then
  while IFS= read -r -d '' pdf; do
    txt="\${pdf%.pdf}.txt"
    if [[ -s "\$txt" ]]; then
      /bin/rm -f "\$pdf"
    fi
  done < <(find "$DATA_DIR" -maxdepth 1 -type f -name '*.pdf' -print0)
fi

copied_any=0
SYNC_STAMP="$ANALYSIS_DIR/.rpa_v2_md_sync.stamp"
INITIAL_BACKFILL="\${RPA_V2_SENTINEL_INITIAL_BACKFILL:-0}"

if [[ ! -f "\$SYNC_STAMP" ]]; then
  if [[ "\$INITIAL_BACKFILL" == "1" ]]; then
    while IFS= read -r -d '' md; do
      cp "\$md" "$ANALYSIS_DIR/"
      copied_any=1
    done < <(find "$DATA_DIR" -maxdepth 1 -type f -name '*.md' -print0)
  fi
  touch "\$SYNC_STAMP"
fi

NEXT_STAMP="\$(mktemp "$ANALYSIS_DIR/.rpa_v2_md_sync.XXXXXX")"
touch "\$NEXT_STAMP"

while IFS= read -r -d '' md; do
  cp "\$md" "$ANALYSIS_DIR/"
  copied_any=1
done < <(find "$DATA_DIR" -maxdepth 1 -type f -name '*.md' -newer "\$SYNC_STAMP" -print0)

mv "\$NEXT_STAMP" "\$SYNC_STAMP"

if [[ "\$copied_any" -eq 1 ]]; then
  ${WATCH_CMD}
else
  echo "No new markdown files to copy from $DATA_DIR"
fi

pid=\$(pgrep -f '.venv/bin/python3 .*run.py' | head -1 || true)
if [[ -z "\$pid" ]]; then
  pid=\$(pgrep -f 'python.*run.py' | head -1 || true)
fi
if [[ -n "\$pid" ]]; then
  echo "fd_pid=\$pid"
  echo -n 'fd_total='
  lsof -p "\$pid" | wc -l
  echo -n 'fd_tcp_est='
  lsof -nP -a -p "\$pid" -iTCP -sTCP:ESTABLISHED | wc -l
  echo -n 'fd_checkpoint_tmp='
  lsof -nP -a -p "\$pid" | awk 'NR>1 && \$9 ~ /data\/checkpoints\/.*\.tmp\$/ {c++} END {print c+0}'
  echo -n 'fd_locks='
  lsof -nP -a -p "\$pid" | awk 'NR>1 && \$9 ~ /\.locks\// {c++} END {print c+0}'
else
  echo 'fd_pid=none'
fi
EOF

  printf -v WATCH_BASH_CMD 'bash -lc %q' "$WATCH_BODY"

  if [[ "${RPA_V2_SENTINEL_DELETE_PDFS:-1}" == "1" ]]; then
    echo "Starting quality sentinel daemon mode via watch (interval=${INTERVAL}s, SAFE PDF cleanup ENABLED, incremental markdown sync to $ANALYSIS_DIR)"
  else
    echo "Starting quality sentinel daemon mode via watch (interval=${INTERVAL}s, PDF cleanup disabled, incremental markdown sync to $ANALYSIS_DIR)"
    echo "Set RPA_V2_SENTINEL_DELETE_PDFS=1 to re-enable PDF cleanup explicitly."
  fi
  echo "Initial markdown backfill is ${RPA_V2_SENTINEL_INITIAL_BACKFILL:-0} (set to 1 to copy all existing markdown on first tick)."

  exec watch -n "$INTERVAL" "$WATCH_BASH_CMD"
fi

exec "${CMD[@]}"
