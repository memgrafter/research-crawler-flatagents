#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
VENV_PATH=".venv"

# --- Parse Arguments ---
LOCAL_INSTALL=false
UPGRADE=false
SHOW_HELP=false
JSON_LOG=false
MAX_WORKERS=3
LIMIT=""
DB_PATH_OVERRIDE=""
QUEUE_ONLY=false
PASSTHROUGH_ARGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
        --local|-l)
            LOCAL_INSTALL=true
            shift
            ;;
        --upgrade|-u)
            UPGRADE=true
            shift
            ;;
        --json-log|-j)
            JSON_LOG=true
            shift
            ;;
        --limit)
            LIMIT="$2"
            PASSTHROUGH_ARGS+=("$1" "$2")
            shift 2
            ;;
        --db)
            DB_PATH_OVERRIDE="$2"
            PASSTHROUGH_ARGS+=("$1" "$2")
            shift 2
            ;;
        --queue-only|-q)
            QUEUE_ONLY=true
            PASSTHROUGH_ARGS+=("--queue-only")
            shift
            ;;
        -w|--workers)
            MAX_WORKERS="$2"
            PASSTHROUGH_ARGS+=("$1" "$2")
            shift 2
            ;;
        -h|--help)
            SHOW_HELP=true
            shift
            ;;
        *)
            PASSTHROUGH_ARGS+=("$1")
            shift
            ;;
    esac
done

print_help() {
    cat <<'HELP'
Usage: run_summarizer_repl.sh [wrapper options] [summarizer options]

Wrapper options:
  -l, --local            Use local flatagents source
  -u, --upgrade          Reinstall/upgrade dependencies
  -j, --json-log         JSON log format
  -w, --workers N        Max workers for scale daemon (default: 3)
  -q, --queue-only       Queue only; do not start scale daemon
  -h, --help             Show this help

Summarizer options (passed to research_paper_analysis.summarizer_repl):
  --db PATH              SQLite DB path
  --limit N              Number of candidate papers per batch
  --max-workers N        Summarizer module worker cap
  --queue-only           Queue without spawning workers

Notes:
  - This script is hosted in v2 but runs the v1 summarizer REPL module.
HELP
}

if [ "$SHOW_HELP" = true ]; then
    print_help
    exit 0
fi

echo "--- Research Paper Summarizer REPL (Human Loop) ---"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Establish project root by walking up to find .git
find_project_root() {
    local dir="$1"
    while [[ "$dir" != "/" ]]; do
        if [[ -e "$dir/.git" ]]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    echo "Error: Could not find project root (no .git found)" >&2
    return 1
}

PROJECT_ROOT="$(find_project_root "$SCRIPT_DIR")"
V1_PY_DIR="$PROJECT_ROOT/research_paper_analysis/python"
PYTHON_SDK_PATH="$PROJECT_ROOT/../flatagents/sdk/python"
V1_RUN_CHECKER="$V1_PY_DIR/run_checker.py"

if [ ! -d "$V1_PY_DIR" ]; then
    echo "Error: v1 python dir not found: $V1_PY_DIR" >&2
    exit 1
fi

if [ ! -f "$V1_RUN_CHECKER" ]; then
    echo "Error: v1 checker not found: $V1_RUN_CHECKER" >&2
    exit 1
fi

echo "📁 Project root: $PROJECT_ROOT"
echo "📁 V1 REPL source: $V1_PY_DIR"
echo "📁 Python SDK: $PYTHON_SDK_PATH"

DEFAULT_DB_PATH="$PROJECT_ROOT/arxiv_crawler/data/arxiv.sqlite"
DB_PATH="$DEFAULT_DB_PATH"
if [ -n "$DB_PATH_OVERRIDE" ]; then
    DB_PATH="$DB_PATH_OVERRIDE"
fi

count_summarizer_pending() {
    if ! command -v sqlite3 >/dev/null 2>&1; then
        echo ""
        return 0
    fi
    if [ ! -f "$DB_PATH" ]; then
        echo ""
        return 0
    fi
    sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM paper_queue WHERE status IN ('pending','processing') AND priority > 0" 2>/dev/null || true
}

count_active_workers() {
    if ! command -v sqlite3 >/dev/null 2>&1; then
        echo ""
        return 0
    fi
    if [ ! -f "$DB_PATH" ]; then
        echo ""
        return 0
    fi
    sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM worker_registry WHERE status = 'active'" 2>/dev/null || true
}

BASE_PENDING="$(count_summarizer_pending)"

# Create venv if missing
if [ ! -d "$VENV_PATH" ]; then
    uv venv "$VENV_PATH"
else
    echo "✅ Virtual environment already exists."
fi

is_module_installed() {
    local module_name="$1"
    "$VENV_PATH/bin/python" - <<PY >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec("$module_name") else 1)
PY
}

needs_install() {
    local module
    for module in "$@"; do
        if ! is_module_installed "$module"; then
            return 0
        fi
    done
    return 1
}

# Install dependencies
echo "📦 Installing dependencies..."
if [ "$UPGRADE" = true ] || needs_install flatagents research_paper_analysis; then
    if [ "$LOCAL_INSTALL" = true ]; then
        echo "  - Installing flatagents from local source..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_PATH/bin/python" -U -e "$PYTHON_SDK_PATH/flatagents[litellm,metrics]"
        else
            uv pip install --python "$VENV_PATH/bin/python" -e "$PYTHON_SDK_PATH/flatagents[litellm,metrics]"
        fi
    else
        echo "  - Installing flatagents from PyPI..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_PATH/bin/python" -U "flatagents[litellm,metrics]"
        else
            uv pip install --python "$VENV_PATH/bin/python" "flatagents[litellm,metrics]"
        fi
    fi

    echo "  - Installing research_paper_analysis package (v1 REPL source)..."
    if [ "$UPGRADE" = true ]; then
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$V1_PY_DIR"
    else
        uv pip install --python "$VENV_PATH/bin/python" -e "$V1_PY_DIR"
    fi
else
    echo "  - All dependencies already installed; skipping."
fi

# Run
echo "🚀 Starting summarizer REPL..."
echo "---"

# Set up logging defaults for flatagents workers (hooks will honor these)
LOG_DIR="${FLATAGENTS_LOG_DIR:-$SCRIPT_DIR/logs}"
mkdir -p "$LOG_DIR"
export FLATAGENTS_LOG_DIR="$LOG_DIR"
export FLATAGENTS_LOG_LEVEL="${FLATAGENTS_LOG_LEVEL:-INFO}"
if [ "$JSON_LOG" = true ]; then
    export FLATAGENTS_LOG_FORMAT="${FLATAGENTS_LOG_FORMAT:-json}"
else
    export FLATAGENTS_LOG_FORMAT="${FLATAGENTS_LOG_FORMAT:-standard}"
fi
echo "📝 Logs: $FLATAGENTS_LOG_DIR (format=$FLATAGENTS_LOG_FORMAT, level=$FLATAGENTS_LOG_LEVEL)"

if [ "$QUEUE_ONLY" = true ]; then
    echo "🧾 Queue-only mode: skipping scale daemon."
    DAEMON_PID=""
    DAEMON_STOPPED=true
else
    # Start the scaling daemon in background
    echo "🔄 Starting scale daemon (background, max_workers=$MAX_WORKERS)..."
    "$VENV_PATH/bin/python" "$V1_RUN_CHECKER" --daemon -m "$MAX_WORKERS" > "$LOG_DIR/scale_daemon.log" 2>&1 &
    DAEMON_PID=$!
    DAEMON_STOPPED=false
    echo "   Daemon PID: $DAEMON_PID (will continue after REPL exits)"
    echo "   To stop: kill $DAEMON_PID"
fi

"$VENV_PATH/bin/python" -m research_paper_analysis.summarizer_repl "${PASSTHROUGH_ARGS[@]}"
echo "---"

if [ "$QUEUE_ONLY" = true ]; then
    echo "✅ Summarizer REPL complete! (queue-only)"
    echo "   Scale daemon not started."
    exit 0
fi

if [[ "$BASE_PENDING" =~ ^[0-9]+$ ]]; then
    AFTER_PENDING="$(count_summarizer_pending)"
    if [[ "$AFTER_PENDING" =~ ^[0-9]+$ ]]; then
        if (( AFTER_PENDING == 0 )); then
            echo "🛑 No prioritized jobs queued; stopping scale daemon."
            kill "$DAEMON_PID" 2>/dev/null || true
            DAEMON_STOPPED=true
        else
            if [ -n "$LIMIT" ]; then
                echo "⏳ Waiting for $AFTER_PENDING of $LIMIT prioritized job(s) to finish..."
            else
                echo "⏳ Waiting for $AFTER_PENDING prioritized job(s) to finish..."
            fi
            SEEN_ACTIVE=false
            while true; do
                CURRENT="$(count_summarizer_pending)"
                ACTIVE_WORKERS="$(count_active_workers)"
                if [[ "$ACTIVE_WORKERS" =~ ^[0-9]+$ ]] && (( ACTIVE_WORKERS > 0 )); then
                    SEEN_ACTIVE=true
                fi
                if [[ "$CURRENT" =~ ^[0-9]+$ ]] && (( CURRENT == 0 )); then
                    echo "✅ Prioritized jobs finished; stopping scale daemon."
                    kill "$DAEMON_PID" 2>/dev/null || true
                    DAEMON_STOPPED=true
                    break
                fi
                if [ "$SEEN_ACTIVE" = true ] && [[ "$ACTIVE_WORKERS" =~ ^[0-9]+$ ]] && (( ACTIVE_WORKERS == 0 )); then
                    echo "⚠️ No active workers remain; stopping scale daemon."
                    kill "$DAEMON_PID" 2>/dev/null || true
                    DAEMON_STOPPED=true
                    break
                fi
                sleep 2
            done
        fi
    fi
fi

echo "✅ Summarizer REPL complete!"
if [ "$DAEMON_STOPPED" = true ]; then
    echo "   Scale daemon stopped."
else
    echo "   Scale daemon still running (PID: $DAEMON_PID)"
    echo "   To stop: kill $DAEMON_PID"
fi
echo "   Daemon log: $LOG_DIR/scale_daemon.log"
