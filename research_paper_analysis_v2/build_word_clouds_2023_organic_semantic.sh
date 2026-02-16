#!/usr/bin/env bash
set -euo pipefail

# Convenience wrapper: 2023 organic+semantic word-cloud build.
#
# Safe by default (no overwrite) unless you pass --overwrite.
# Additional flags are forwarded to build_word_clouds_organic_semantic.sh.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/build_word_clouds_organic_semantic.sh" \
  --year-prefix 23 \
  --output-dir queries/word_clouds_2023_organic_semantic \
  --output-file shotgun_ml_llm_2023_organic_semantic.txt \
  --csv-output data/word_list_2023_organic_semantic_candidates.csv \
  --embedding-config ../relevance_scoring_2024.yml \
  --anchor-file queries/word_clouds_2023_organic_semantic/llm_semantic_anchors_2023.list \
  --semantic-weight 0.60 \
  --lexical-weight 0.40 \
  --model-pattern-boost 0.00 \
  "$@"
