#!/bin/bash
set -e

VENV_PATH=".venv"

LOCAL_INSTALL=false
UPGRADE=false
SKIP_INSTALL=false
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
        --skip-install|--no-install)
            SKIP_INSTALL=true
            shift
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

echo "--- Relevance Scoring ---"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Ensuring virtual environment..."
if [ ! -d "$VENV_PATH" ]; then
    uv venv "$VENV_PATH"
else
    echo "Virtual environment already exists."
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

if [ "$SKIP_INSTALL" = true ]; then
    echo "Skipping dependency installs."
else
    echo "Installing dependencies..."
    if [ "$UPGRADE" = true ] || needs_install flatagents relevance_scoring; then
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

        echo "  - Installing relevance_scoring package..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR"
        else
            uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR"
        fi
    else
        echo "  - All dependencies already installed; skipping."
    fi
fi

echo "Running scoring..."
echo "---"
if [ "${PASSTHROUGH_ARGS[0]}" = "python" ] && [ "${PASSTHROUGH_ARGS[1]}" = "-m" ]; then
    "$VENV_PATH/bin/python" "${PASSTHROUGH_ARGS[@]:1}"
else
    "$VENV_PATH/bin/python" -m relevance_scoring.main "${PASSTHROUGH_ARGS[@]}"
fi
echo "---"
echo "Scoring complete!"
