#!/bin/bash
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "--- Research Paper Analysis ---"

# Strip legacy flags that are no longer needed (local/upgrade handled by pyproject.toml)
ARGS=()
for arg in "$@"; do
    case $arg in
        -l|--local|-u|--upgrade) ;;  # no-ops, kept for back-compat
        *) ARGS+=("$arg") ;;
    esac
done

# One command: creates venv, installs all deps (respects [tool.uv.sources] for local dev)
uv sync

echo "🚀 Running..."
uv run python -m research_paper_analysis.main "${ARGS[@]}"
