#!/bin/bash
set -e

VENV_PATH=".venv"

echo "--- Paper Discovery Pipeline ---"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check for required env vars
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

echo "Installing dependencies..."
echo "  - Installing flatagents from local repo..."
uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../../flatagents/sdk/python[litellm]"

echo "  - Installing discovery_pipeline package..."
uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR"

# Install child machine packages
echo "  - Installing child machine packages..."
uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../arxiv_crawler"
uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../relevance_scoring"
uv pip install --python "$VENV_PATH/bin/python" -e "$SCRIPT_DIR/../reverse_citation_enrichment"

echo "Running discovery pipeline..."
echo "---"
"$VENV_PATH/bin/python" -m discovery_pipeline.main "$@"
echo "---"
echo "Pipeline complete!"
