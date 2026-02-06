#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

./run.sh -- python -m arxiv_crawler.backfill \
  --db-path data/arxiv.sqlite \
  --start-date 2025-01-01 \
  --end-date 2026-02-04 \
  --window-days 1 \
  --max-results 1000 \
  --progress-every 500 \
  --throttle-seconds 3 \
  --use-submitted-date \
  --slice-retries 5 \
  --slice-retry-sleep 60
