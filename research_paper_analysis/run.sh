#!/bin/bash
set -e

# --- Configuration ---
VENV_PATH=".venv"

# --- Parse Arguments ---
LOCAL_INSTALL=false
UPGRADE=false
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
        *)
            PASSTHROUGH_ARGS+=("$1")
            shift
            ;;
    esac
done

# --- Script Logic ---
echo "--- Research Paper Analysis Demo (HSM + Checkpoint) ---"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Create venv
echo "Ensuring virtual environment..."
if [ ! -d "$VENV_PATH" ]; then
    uv venv "$VENV_PATH"
else
    echo "Virtual environment already exists."
fi

# Install dependencies
echo "Installing dependencies..."
if [ "$LOCAL_INSTALL" = true ]; then
    echo "  - Installing flatagents from local source..."
    if [ "$UPGRADE" = true ]; then
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR/../..[litellm]"
    else
        uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../..[litellm]"
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

# Run
echo "Running demo..."
echo "---"
"$VENV_PATH/bin/python" -m research_paper_analysis.main "${PASSTHROUGH_ARGS[@]}"
echo "---"

echo "Demo complete!"
