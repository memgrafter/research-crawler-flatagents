#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_PATH="${1:-$SCRIPT_DIR/../../arxiv_crawler/data/arxiv.sqlite}"
DATA_DIR="${2:-$SCRIPT_DIR/../data}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "DB not found: $DB_PATH" >&2
  exit 1
fi

if [[ ! -d "$DATA_DIR" ]]; then
  echo "Data dir not found: $DATA_DIR" >&2
  exit 1
fi

mapfile -t rows < <(
  sqlite3 -separator '|' "$DB_PATH" "
    SELECT q.id, p.arxiv_id
    FROM paper_queue q
    JOIN papers p ON p.id = q.paper_id
    WHERE q.status = 'poisoned'
      AND datetime(COALESCE(q.finished_at, q.started_at, q.claimed_at, q.enqueued_at))
        >= datetime('now','-1 day','start of day')
      AND datetime(COALESCE(q.finished_at, q.started_at, q.claimed_at, q.enqueued_at))
        < datetime('now','+1 day','start of day')
    ORDER BY q.id;
  "
)

ids=()
selected=()

for row in "${rows[@]}"; do
  IFS='|' read -r queue_id arxiv_id <<<"$row"
  if [[ -z "$queue_id" || -z "$arxiv_id" ]]; then
    continue
  fi

  prefix="${arxiv_id//\//_}"
  pdf_path="$DATA_DIR/${prefix}.pdf"
  txt_path="$DATA_DIR/${prefix}.txt"

  if [[ ! -f "$pdf_path" || ! -f "$txt_path" ]]; then
    continue
  fi

  if compgen -G "$DATA_DIR/${prefix}_*.md" >/dev/null; then
    continue
  fi

  ids+=("$queue_id")
  selected+=("$arxiv_id")
 done

if [[ ${#ids[@]} -eq 0 ]]; then
  echo "No poisoned rows from today/yesterday matched the file criteria."
  exit 0
fi

ids_csv=$(IFS=,; echo "${ids[*]}")

sqlite3 "$DB_PATH" <<SQL
UPDATE paper_queue
SET status = 'pending',
    attempts = 0,
    error = NULL,
    worker = NULL,
    started_at = NULL,
    finished_at = NULL,
    claimed_by = NULL,
    claimed_at = NULL
WHERE id IN ($ids_csv);
SQL

printf "Unpoisoned %s rows (today/yesterday, pdf+txt present, no md):\n" "${#ids[@]}"
printf ' - %s\n' "${selected[@]}"
