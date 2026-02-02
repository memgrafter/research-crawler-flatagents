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
DB_PATH_OVERRIDE=""
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
        --db)
            DB_PATH_OVERRIDE="$2"
            shift 2
            ;;
        -w|--workers)
            MAX_WORKERS="$2"
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

# --- Script Logic ---
echo "--- Kick Off Pending Workers ---"

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
# This only works if your flatagents local is adjacent to this project
PYTHON_SDK_PATH="$PROJECT_ROOT/../flatagents/sdk/python"

echo "📁 Project root: $PROJECT_ROOT"
echo "📁 Python SDK: $PYTHON_SDK_PATH"

DEFAULT_DB_PATH="$PROJECT_ROOT/arxiv_crawler/data/arxiv.sqlite"
DB_PATH="$DEFAULT_DB_PATH"
if [ -n "$DB_PATH_OVERRIDE" ]; then
    DB_PATH="$DB_PATH_OVERRIDE"
fi

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
            uv pip install --python "$VENV_PATH/bin/python" -U -e "$PYTHON_SDK_PATH[litellm,metrics]"
        else
            uv pip install --python "$VENV_PATH/bin/python" -e "$PYTHON_SDK_PATH[litellm,metrics]"
        fi
    else
        echo "  - Installing flatagents from PyPI..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_PATH/bin/python" -U "flatagents[litellm,metrics]"
        else
            uv pip install --python "$VENV_PATH/bin/python" "flatagents[litellm,metrics]"
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
    echo "Wrapper options: --local/-l (use local flatagents), --upgrade/-u (reinstall/upgrade deps), --json-log/-j (JSON log format)."
    echo "Worker options: -w/--workers N (max workers), --db PATH (override DB path)."
fi

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

# Ensure workers use the desired DB
export ARXIV_DB_PATH="$DB_PATH"

# Kick off workers based on current pending queue
"$VENV_PATH/bin/python" "$SCRIPT_DIR/run_checker.py" --max-workers "$MAX_WORKERS" "${PASSTHROUGH_ARGS[@]}"
