#!/bin/bash
set -e

VENV_PATH=".venv"

echo "--- Paper Discovery Pipeline ---"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

UPGRADE=false
PASSTHROUGH_ARGS=()
while [[ $# -gt 0 ]]; do
    case $1 in
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

# Check whether enrichment is skipped (no OpenAlex needed)
SKIP_ENRICH=false
for arg in "${PASSTHROUGH_ARGS[@]}"; do
    if [ "$arg" = "--skip-enrich" ]; then
        SKIP_ENRICH=true
        break
    fi
done

# Check for required env vars
if [ "$SKIP_ENRICH" = false ]; then
    if [ -z "$OPENALEX_MAILTO" ]; then
        echo "Error: OPENALEX_MAILTO environment variable not set"
        echo "Export it before running: export OPENALEX_MAILTO='you@example.com'"
        exit 1
    fi
    if [ -z "$OPENALEX_API_KEY" ]; then
        echo "Error: OPENALEX_API_KEY environment variable not set"
        echo "Export it before running: export OPENALEX_API_KEY='your-key'"
        exit 1
    fi
fi

if [ -z "$CEREBRAS_API_KEY" ]; then
    echo "Error: CEREBRAS_API_KEY environment variable not set"
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
if [ "$UPGRADE" = true ] || needs_install flatagents discovery_pipeline arxiv_crawler relevance_scoring reverse_citation_enrichment; then
    echo "  - Installing flatagents from local repo..."
    if [ "$UPGRADE" = true ]; then
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR/../../flatagents/sdk/python[litellm]"
    else
        uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../../flatagents/sdk/python[litellm]"
    fi

    echo "  - Installing discovery_pipeline package..."
    if [ "$UPGRADE" = true ]; then
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR"
    else
        uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR"
    fi

    # Install child machine packages
    echo "  - Installing child machine packages..."
    if [ "$UPGRADE" = true ]; then
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR/../arxiv_crawler"
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR/../relevance_scoring"
        uv pip install --python "$VENV_PATH/bin/python" -U -e "$SCRIPT_DIR/../reverse_citation_enrichment"
    else
        uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../arxiv_crawler"
        uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../relevance_scoring"
        uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../reverse_citation_enrichment"
    fi
else
    echo "  - All dependencies already installed; skipping."
fi

echo "Running discovery pipeline..."
echo "---"
"$VENV_PATH/bin/python" -m discovery_pipeline.main "${PASSTHROUGH_ARGS[@]}"
echo "---"
echo "Pipeline complete!"
