#!/bin/bash
set -e

VENV_PATH=".venv"

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

echo "--- arXiv Research Crawler ---"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Ensuring virtual environment..."
if [ ! -d "$VENV_PATH" ]; then
    uv venv "$VENV_PATH"
else
    echo "Virtual environment already exists."
fi

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

echo "  - Installing arxiv_crawler package..."
if [ "$UPGRADE" = true ]; then
    uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR"
else
    uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR"
fi

echo "Running crawler..."
echo "---"
if [ "${PASSTHROUGH_ARGS[0]}" = "python" ] && [ "${PASSTHROUGH_ARGS[1]}" = "-m" ]; then
    "$VENV_PATH/bin/python" "${PASSTHROUGH_ARGS[@]:1}"
else
    "$VENV_PATH/bin/python" -m arxiv_crawler.main "${PASSTHROUGH_ARGS[@]}"
fi
echo "---"
echo "Crawler complete!"
