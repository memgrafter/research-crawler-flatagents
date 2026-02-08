#!/usr/bin/env bash
set -euo pipefail

# Show v2 phase/buffer counts from executions DB.
#
# Usage:
#   ./phase_buffer_counts.sh
#   ./phase_buffer_counts.sh /path/to/v2_executions.sqlite

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DB_PATH="${1:-${V2_EXECUTIONS_DB_PATH:-$SCRIPT_DIR/data/v2_executions.sqlite}}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "DB not found: $DB_PATH" >&2
  exit 1
fi

sqlite3 -header -column "$DB_PATH" <<'SQL'
WITH agg AS (
  SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
    SUM(CASE WHEN status = 'prepping' THEN 1 ELSE 0 END) AS prepping,
    SUM(CASE WHEN status = 'prepped' THEN 1 ELSE 0 END) AS prepped,
    SUM(CASE WHEN status = 'analyzing' THEN 1 ELSE 0 END) AS analyzing,
    SUM(CASE WHEN status = 'analyzed' THEN 1 ELSE 0 END) AS analyzed,
    SUM(CASE WHEN status = 'wrapping' THEN 1 ELSE 0 END) AS wrapping,
    SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
  FROM executions
)
SELECT
  total,
  pending,
  prepping,
  prepped,
  analyzing,
  analyzed,
  wrapping,
  done,
  failed,
  -- phase buffers
  prepped AS prep_buffer_for_expensive,
  analyzed AS wrap_buffer_for_wrap,
  -- derived rollups
  (prepping + analyzing + wrapping) AS in_flight,
  (done + failed) AS terminal
FROM agg;
SQL
