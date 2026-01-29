#!/bin/bash
set -e

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
            PASSTHROUGH_ARGS+=("$1")
            shift
            ;;
        *)
            PASSTHROUGH_ARGS+=("$1")
            shift
            ;;
    esac
done

# --- Script Logic ---
echo "--- Research Paper Search REPL (Human Loop) ---"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Establish project root by walking up to find .git
# This ensures paths work regardless of where the script is invoked from
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

PYTHON_SDK_PATH="$PROJECT_ROOT/flatagents"

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
            uv pip install --python "$VENV_PATH/bin/python" -U -e "$PYTHON_SDK_PATH[litellm]"
        else
            uv pip install --python "$VENV_PATH/bin/python" -e "$PYTHON_SDK_PATH[litellm]"
        fi
    else
        echo "  - Installing flatagents from PyPI..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_PATH/bin/python" -U "flatagents[litellm]"
        else
            uv pip install --python "$VENV_PATH/bin/python" "flatagents[litellm]"
        fi
    fi

    echo "  - Installing research_paper_analysis package..."
    if [ "$UPGRADE" = true ]; then
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR"
    else
        uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR"
    fi
else
    echo "  - All dependencies already installed; skipping."
fi

if [ "$SHOW_HELP" = true ]; then
    echo "Wrapper options: --local/-l (use local flatagents), --upgrade/-u (reinstall/upgrade deps), --json-log/-j (JSON log format), -w/--workers (max workers), --queue-only/-q (queue without workers)."
fi

# Run
echo "🚀 Starting search REPL..."
echo "---"

# Set up logging directory for flatagents workers
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
export FLATAGENTS_LOG_DIR="$LOG_DIR"
export FLATAGENTS_LOG_LEVEL="DEBUG"
if [ "$JSON_LOG" = true ]; then
    export FLATAGENTS_LOG_FORMAT="json"
    echo "📝 Logs (JSON): $LOG_DIR"
else
    export FLATAGENTS_LOG_FORMAT="standard"
    echo "📝 Logs: $LOG_DIR"
fi

if [ "$QUEUE_ONLY" = true ]; then
    echo "🧾 Queue-only mode: skipping scale daemon."
    DAEMON_PID=""
    DAEMON_STOPPED=true
else
    # Start the scaling daemon in background
    echo "🔄 Starting scale daemon (background, max_workers=$MAX_WORKERS)..."
    "$VENV_PATH/bin/python" "$SCRIPT_DIR/run_checker.py" --daemon -m "$MAX_WORKERS" > "$LOG_DIR/scale_daemon.log" 2>&1 &
    DAEMON_PID=$!
    DAEMON_STOPPED=false
    echo "   Daemon PID: $DAEMON_PID (will continue after REPL exits)"
    echo "   To stop: kill $DAEMON_PID"
fi

"$VENV_PATH/bin/python" -m research_paper_analysis.search_repl "${PASSTHROUGH_ARGS[@]}"
echo "---"

if [ "$QUEUE_ONLY" = true ]; then
    echo "✅ Search REPL complete! (queue-only)"
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

echo "✅ Search REPL complete!"
if [ "$DAEMON_STOPPED" = true ]; then
    echo "   Scale daemon stopped."
else
    echo "   Scale daemon still running (PID: $DAEMON_PID)"
    echo "   To stop: kill $DAEMON_PID"
fi
echo "   Daemon log: $LOG_DIR/scale_daemon.log"
