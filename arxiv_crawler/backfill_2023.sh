#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

DB_PATH="${DB_PATH:-data/arxiv.sqlite}"
START_BOUND="2023-01-01"
END_BOUND="2023-12-31"
START_DATE="$START_BOUND"

# Auto-resume: read the latest successful 2023 submittedDate slice from crawl_runs.
if [ -f "$DB_PATH" ]; then
  LAST_SLICE_DAY=$(sqlite3 "$DB_PATH" "
    SELECT substr(query, instr(query, 'submittedDate:[') + 15, 8)
    FROM crawl_runs
    WHERE status = 'ok'
      AND query LIKE '%submittedDate:[2023%'
    ORDER BY id DESC
    LIMIT 1;
  ")

  if [[ "$LAST_SLICE_DAY" =~ ^[0-9]{8}$ ]]; then
    START_DATE=$(python - <<PY
from datetime import datetime, timedelta
start_bound = datetime.strptime("$START_BOUND", "%Y-%m-%d")
next_day = datetime.strptime("$LAST_SLICE_DAY", "%Y%m%d") + timedelta(days=1)
print(max(start_bound, next_day).strftime("%Y-%m-%d"))
PY
)
  fi
fi

if python - <<PY
from datetime import datetime
start = datetime.strptime("$START_DATE", "%Y-%m-%d")
end = datetime.strptime("$END_BOUND", "%Y-%m-%d")
raise SystemExit(0 if start <= end else 1)
PY
then
  echo "Backfill window: $START_DATE -> $END_BOUND"
else
  echo "2023 backfill already complete (next start $START_DATE is after $END_BOUND)."
  exit 0
fi

ARXIV_CONTACT_EMAIL="${ARXIV_CONTACT_EMAIL:-memgrafter@gmail.com}" ./run.sh -- python -m arxiv_crawler.backfill \
  --db-path "$DB_PATH" \
  --start-date "$START_DATE" \
  --end-date "$END_BOUND" \
  --window-days "${WINDOW_DAYS:-1}" \
  --max-results "${MAX_RESULTS:-1000}" \
  --progress-every "${PROGRESS_EVERY:-500}" \
  --throttle-seconds "${THROTTLE_SECONDS:-4}" \
  --sleep-seconds "${SLEEP_SECONDS:-4}" \
  --use-submitted-date \
  --slice-retries "${SLICE_RETRIES:-20}" \
  --slice-retry-sleep "${SLICE_RETRY_SLEEP:-120}"
