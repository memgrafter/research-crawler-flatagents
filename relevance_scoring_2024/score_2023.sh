#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Convenience wrapper for 2023 backfill into paper_relevance.fmr_2023.
./run.sh -- \
  --target-score-column fmr_2023 \
  --year-prefix 23 \
  --config-path ../relevance_scoring_2023.yml \
  "$@"
