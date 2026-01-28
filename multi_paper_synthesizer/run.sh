#!/bin/bash
# Multi-Paper Research Synthesizer Demo
# Analyzes multiple research papers and synthesizes insights

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
SDK_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "--- Multi-Paper Research Synthesizer ---"

# Check for API key
if [ -z "$CEREBRAS_API_KEY" ]; then
    echo "Error: CEREBRAS_API_KEY environment variable not set"
    echo "Export it before running: export CEREBRAS_API_KEY='your-key'"
    exit 1
fi

# Parse arguments
USE_LOCAL=false
UPGRADE=false
for arg in "$@"; do
    case $arg in
        --local)
            USE_LOCAL=true
            shift
            ;;
        --upgrade|-u)
            UPGRADE=true
            shift
            ;;
    esac
done

# Create venv if needed
echo "Ensuring virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    uv venv "$VENV_DIR"
fi
echo "Virtual environment ready."

is_module_installed() {
    local module_name="$1"
    "$VENV_DIR/bin/python" - <<PY >/dev/null 2>&1
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
echo "Installing dependencies..."
if [ "$UPGRADE" = true ] || needs_install flatagents multi_paper_synthesizer; then
    if [ "$USE_LOCAL" = true ]; then
        echo "  - Installing flatagents from local source..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_DIR/bin/python" -U -e "$SDK_DIR" --quiet
        else
            uv pip install --python "$VENV_DIR/bin/python" -e "$SDK_DIR" --quiet
        fi
    else
        echo "  - Installing flatagents from PyPI..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_DIR/bin/python" -U flatagents[litellm] --quiet
        else
            uv pip install --python "$VENV_DIR/bin/python" flatagents[litellm] --quiet
        fi
    fi

    echo "  - Installing multi_paper_synthesizer package..."
    if [ "$UPGRADE" = true ]; then
        uv pip install --python "$VENV_DIR/bin/python" -U -e "$SCRIPT_DIR" --quiet
    else
        uv pip install --python "$VENV_DIR/bin/python" -e "$SCRIPT_DIR" --quiet
    fi
else
    echo "  - All dependencies already installed; skipping."
fi

# Run the demo
echo "Running demo..."
echo "---"
"$VENV_DIR/bin/python" -m multi_paper_synthesizer.main
echo "---"
echo "Demo complete!"
