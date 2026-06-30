#!/usr/bin/env bash
# Download the jina-embeddings-v5-text-nano HF model to models/.
#
# To convert to GGUF after downloading, run:
#   ./scripts/download_embedding_model.sh --convert
#
# Usage:
#   ./scripts/download_embedding_model.sh [--convert]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MODEL_DIR="${PROJECT_DIR}/models"

REPO_ID="jinaai/jina-embeddings-v5-text-nano"
MODEL_SUBDIR="jina-embeddings-v5-text-nano"
MODEL_PATH="${MODEL_DIR}/${MODEL_SUBDIR}"
GGUF_FILE="Jina-Embeddings-v5-Text-Nano-212M-Q8_0.gguf"
GGUF_PATH="${MODEL_PATH}/${GGUF_FILE}"

CONVERT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --convert) CONVERT=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "=== Downloading embedding model ==="
echo "Repo: ${REPO_ID}"
echo "Destination: ${MODEL_PATH}"

if [[ -d "${MODEL_PATH}" && -f "${MODEL_PATH}/config.json" ]]; then
    echo "HF model already present at ${MODEL_PATH} — skipping download."
else
    mkdir -p "${MODEL_DIR}"
    echo "Downloading..."
    HF_HUB_DISABLE_XET=1 huggingface-cli download "${REPO_ID}" --local-dir "${MODEL_PATH}"
    echo "Downloaded to ${MODEL_PATH}/"
fi

# Convert if requested
if [[ "${CONVERT}" == true ]]; then
    if [[ -f "${GGUF_PATH}" ]]; then
        SIZE=$(du -h "${GGUF_PATH}" | cut -f1)
        echo "GGUF already exists at ${GGUF_PATH} (${SIZE}) — skipping conversion."
    else
        echo "Converting to GGUF (Q8_0)..."
        .venv/bin/python "/home/robbintt/clones/llama.cpp/convert_hf_to_gguf.py" \
            "${MODEL_PATH}" \
            --outtype q8_0
        SIZE=$(du -h "${GGUF_PATH}" | cut -f1)
        echo "Converted: ${GGUF_PATH} (${SIZE})"
    fi
else
    if [[ -f "${GGUF_PATH}" ]]; then
        SIZE=$(du -h "${GGUF_PATH}" | cut -f1)
        echo "GGUF exists at ${GGUF_PATH} (${SIZE})"
    else
        echo
        echo "No GGUF file found. To convert the HF model to GGUF Q8_0, run:"
        echo "  ./scripts/download_embedding_model.sh --convert"
    fi
fi

echo
echo "=== Model ready ==="
echo "To serve embeddings via llama-server, run:"
echo "  ./scripts/serve_embeddings.sh"
