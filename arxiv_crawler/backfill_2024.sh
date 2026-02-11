#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

DB_PATH="${DB_PATH:-data/arxiv.sqlite}"
DEFAULT_START_DATE="2024-01-01"
END_DATE="${END_DATE:-2024-12-31}"

# You can force an explicit restart point with:
#   BACKFILL_START_DATE=2024-03-01 ./backfill_2024.sh
START_DATE="${BACKFILL_START_DATE:-$DEFAULT_START_DATE}"

# Auto-resume: read the latest successful 2024 submittedDate slice from crawl_runs.
if [ -z "${BACKFILL_START_DATE:-}" ] && [ -f "$DB_PATH" ]; then
  LAST_SLICE_DAY=$(sqlite3 "$DB_PATH" "
    SELECT substr(query, instr(query, 'submittedDate:[') + 15, 8)
    FROM crawl_runs
    WHERE status = 'ok'
      AND query LIKE '%submittedDate:[2024%'
    ORDER BY id DESC
    LIMIT 1;
  ")

  if [[ "$LAST_SLICE_DAY" =~ ^[0-9]{8}$ ]]; then
    START_DATE=$(python - <<PY
from datetime import datetime, timedelta
print((datetime.strptime("$LAST_SLICE_DAY", "%Y%m%d") + timedelta(days=1)).strftime("%Y-%m-%d"))
PY
)
  fi
fi

if python - <<PY
from datetime import datetime
start = datetime.strptime("$START_DATE", "%Y-%m-%d")
end = datetime.strptime("$END_DATE", "%Y-%m-%d")
raise SystemExit(0 if start <= end else 1)
PY
then
  echo "Backfill window: $START_DATE -> $END_DATE"
else
  echo "2024 backfill already complete (next start $START_DATE is after $END_DATE)."
  exit 0
fi

ARXIV_CONTACT_EMAIL="${ARXIV_CONTACT_EMAIL:-memgrafter@gmail.com}" ./run.sh -- python -m arxiv_crawler.backfill \
  --db-path "$DB_PATH" \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --window-days "${WINDOW_DAYS:-1}" \
  --max-results "${MAX_RESULTS:-1000}" \
  --progress-every "${PROGRESS_EVERY:-500}" \
  --throttle-seconds "${THROTTLE_SECONDS:-4}" \
  --sleep-seconds "${SLEEP_SECONDS:-4}" \
  --use-submitted-date \
  --slice-retries "${SLICE_RETRIES:-20}" \
  --slice-retry-sleep "${SLICE_RETRY_SLEEP:-120}"
