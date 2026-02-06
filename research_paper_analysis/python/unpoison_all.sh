#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-../../arxiv_crawler/data/arxiv.sqlite}"

sqlite3 "$DB_PATH" <<'SQL'
UPDATE paper_queue
SET status = 'pending',
    attempts = 0,
    error = NULL,
    worker = NULL,
    started_at = NULL,
    finished_at = NULL,
    claimed_by = NULL,
    claimed_at = NULL
WHERE status = 'poisoned';
SQL

printf "Reset poisoned rows to pending in %s\n" "$DB_PATH"
