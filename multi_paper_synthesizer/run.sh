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
for arg in "$@"; do
    case $arg in
        --local)
            USE_LOCAL=true
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

# Install dependencies
echo "Installing dependencies..."
if [ "$USE_LOCAL" = true ]; then
    echo "  - Installing flatagents from local source..."
    uv pip install --python "$VENV_DIR/bin/python" -e "$SDK_DIR" --quiet
else
    echo "  - Installing flatagents from PyPI..."
    uv pip install --python "$VENV_DIR/bin/python" flatagents[litellm] --quiet
fi

echo "  - Installing multi_paper_synthesizer package..."
uv pip install --python "$VENV_DIR/bin/python" -e "$SCRIPT_DIR" --quiet

# Run the demo
echo "Running demo..."
echo "---"
"$VENV_DIR/bin/python" -m multi_paper_synthesizer.main
echo "---"
echo "Demo complete!"
