#!/usr/bin/env bash
# Serve jina-embeddings-v5-text-nano via llama.cpp on localhost (3090Ti)
# Usage: ./scripts/serve-embeddings.sh [--port PORT] [--model MODEL]
#
# Default: port 8082, model jina-v5-nano-q8_0.gguf

set -euo pipefail

PORT="${EMBEDDING_PORT:-8082}"
MODEL_PATH="${EMBEDDING_MODEL:-/opt/models/jina-v5-nano-q8_0.gguf}"
CONTEXT_SIZE="${EMBEDDING_CTX:-8192}"

# Download model if not present
if [ ! -f "$MODEL_PATH" ]; then
  echo "Model not found at $MODEL_PATH, downloading..."
  mkdir -p "$(dirname "$MODEL_PATH")"
  curl -L "https://huggingface.co/cstr/jina-v5-nano-GGUF/resolve/main/jina-v5-nano-q8_0.gguf" \
    -o "$MODEL_PATH"
fi

echo "Serving embeddings on port $PORT with model: $MODEL_PATH"
echo "Endpoint: http://localhost:${PORT}/v1/embeddings"

exec llama-server \
  --model "$MODEL_PATH" \
  --port "$PORT" \
  --ctx-size "$CONTEXT_SIZE" \
  --embedding \
  --log-disable

# Test endpoint:
# curl http://localhost:8082/v1/embeddings \
#   -H "Content-Type: application/json" \
#   -d '{"model": "jina-v5-nano", "input": "hello world"}'
