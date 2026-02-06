#!/bin/bash
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

export LITELLM_API_BASE="https://opencode.ai/zen/v1"
export LITELLM_API_KEY="public"
export OPENAI_API_KEY="public"
export OPENAI_API_BASE="https://opencode.ai/zen/v1"
export OPENAI_BASE_URL="https://opencode.ai/zen/v1"
export OPENAI_LIKE_API_BASE="https://opencode.ai/zen/v1"
export OPENAI_LIKE_API_KEY="public"
export FLATAGENTS_LITELLM_STREAM="1"
export FLATAGENTS_LITELLM_STREAM_INCLUDE_USAGE="0"
export LITELLM_HEADERS='{"user-agent":"ai-sdk/openai-compatible/1.0.32 ai-sdk/provider-utils/3.0.20 runtime/bun/1.3.5","x-opencode-client":"cli","x-opencode-project":"global"}'

"$SCRIPT_DIR/run.sh" "$@"
