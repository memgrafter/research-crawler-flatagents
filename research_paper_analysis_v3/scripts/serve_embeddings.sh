#!/usr/bin/env bash
# Launch llama-server with jina-embeddings-v5-text-nano on GPU 1.
#
# Serves OpenAI-compatible /v1/embeddings endpoint at http://host:port/v1/embeddings
#
# Runs on CUDA1 (GPU 1) — GPU 0 is used by the main inference server.
# If the model hasn't been converted to GGUF yet, this script will tell you how.
#
# Usage:
#   ./scripts/serve_embeddings.sh [--host HOST] [--port PORT] [--device DEVICE]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MODEL_DIR="${PROJECT_DIR}/models"

MODEL_SUBDIR="jina-embeddings-v5-text-nano"
GGUF_FILE="Jina-Embeddings-v5-Text-Nano-212M-Q8_0.gguf"
MODEL_PATH="${MODEL_DIR}/${MODEL_SUBDIR}/${GGUF_FILE}"

# Defaults
HOST="0.0.0.0"
PORT=8083
DEVICE="CUDA1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --device) DEVICE="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Check model exists
if [[ ! -f "${MODEL_PATH}" ]]; then
    echo "Error: GGUF model not found at ${MODEL_PATH}"
    echo
    echo "To download and convert the model, run:"
    echo "  ./scripts/download_embedding_model.sh --convert"
    exit 1
fi

echo "=== Starting llama-server ==="
echo "Host:   ${HOST}:${PORT}"
echo "Device: ${DEVICE}"
echo "Model:  ${MODEL_PATH}"
echo "Endpoint: http://${HOST}:${PORT}/v1/embeddings"
echo "Press Ctrl+C to stop."
echo

exec llama-server \
    -m "${MODEL_PATH}" \
    --host "${HOST}" \
    --port "${PORT}" \
    --embeddings \
    --pooling mean \
    -c 8192 \
    --device "${DEVICE}" \
    --threads 4 \
    --log-disable

# --embeddings: restrict to embedding-only mode (dedicated embedding model)
# --pooling mean: use mean pooling (standard for this model)
# -c 8192: context length
# --device CUDA1: run on GPU 1 (GPU 0 used by main inference server)
# --threads 4: CPU threads for pre/post-processing
# --log-disable: reduce noise in server logs
