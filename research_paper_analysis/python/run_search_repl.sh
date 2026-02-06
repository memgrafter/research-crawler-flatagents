#!/bin/bash
set -euo pipefail

# --- Configuration ---
VENV_PATH=".venv"

# --- Parse Arguments ---
LOCAL_INSTALL=false
UPGRADE=false
SHOW_HELP=false
JSON_LOG=false
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
Usage: run_search_repl.sh [wrapper options] [search options]

Wrapper options:
  -l, --local            Use local flatagents source
  -u, --upgrade          Reinstall/upgrade dependencies
  -j, --json-log         JSON log format
  -h, --help             Show this help

Search options (passed to research_paper_analysis.search_repl):
  --db PATH              SQLite DB path
  --limit N              Number of matches to return
  --query TEXT           FTS5 query
  --query-file PATH      Newline-delimited term list (OR joined)
  --order-by ORDER       bm25 | impact | hybrid
  --llm-relevant-only    Restrict to llm_relevant = 1
  --rebuild-fts          Force FTS rebuild
  --auto-summarize       Queue all matches without prompts
  --show-count           Print total match count (slow on large DBs)

Notes:
  - This wrapper only updates the DB queue; it does not spawn workers.
HELP
}

if [ "$SHOW_HELP" = true ]; then
    print_help
    exit 0
fi

# --- Script Logic ---
echo "--- Research Paper Search (DB Queue Update) ---"

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

# Run
echo "🚀 Starting search (DB queue update)..."
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

"$VENV_PATH/bin/python" -m research_paper_analysis.search_repl "${PASSTHROUGH_ARGS[@]}"
echo "---"
echo "✅ Search update complete! (DB queue only)"
