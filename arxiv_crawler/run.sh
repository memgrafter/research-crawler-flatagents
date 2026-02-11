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

# arXiv API defaults (override via env if needed)
export ARXIV_EXPORT_HOST="${ARXIV_EXPORT_HOST:-export.arxiv.org}"
export ARXIV_API_URL="${ARXIV_API_URL:-https://${ARXIV_EXPORT_HOST}/api/query}"
if [ -z "$ARXIV_CONTACT_EMAIL" ] && [ -n "$OPENALEX_MAILTO" ]; then
    export ARXIV_CONTACT_EMAIL="$OPENALEX_MAILTO"
fi
if [ -z "$ARXIV_USER_AGENT" ]; then
    if [ -n "$ARXIV_CONTACT_EMAIL" ]; then
        export ARXIV_USER_AGENT="research-crawler/0.1 (mailto:${ARXIV_CONTACT_EMAIL})"
    else
        export ARXIV_USER_AGENT="research-crawler/0.1"
    fi
fi

ARGS_STR="${PASSTHROUGH_ARGS[*]}"
if [[ "$ARGS_STR" == *"fetch_citations"* ]] && [ -z "$OPENALEX_MAILTO" ]; then
    echo "Error: OPENALEX_MAILTO environment variable not set"
    echo "Export it before running: export OPENALEX_MAILTO='you@example.com'"
    exit 1
fi

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

echo "Installing dependencies..."
if [ "$UPGRADE" = true ] || needs_install flatmachines arxiv_crawler; then
    if [ "$LOCAL_INSTALL" = true ]; then
        echo "  - Installing flatmachines from local source..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR/../../flatagents/sdk/python/flatmachines"
        else
            uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../../flatagents/sdk/python/flatmachines"
        fi
    else
        echo "  - Installing flatmachines from PyPI..."
        if [ "$UPGRADE" = true ]; then
            uv pip install --python "$VENV_PATH/bin/python" -U "flatmachines>=1.1.1"
        else
            uv pip install --python "$VENV_PATH/bin/python" "flatmachines>=1.1.1"
        fi
    fi

    echo "  - Installing arxiv_crawler package..."
    if [ "$UPGRADE" = true ]; then
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR"
    else
        uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR"
    fi
else
    echo "  - All dependencies already installed; skipping."
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
