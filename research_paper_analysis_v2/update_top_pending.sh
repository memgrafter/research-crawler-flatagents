#!/usr/bin/env bash
set -euo pipefail

# Re-queue top-N papers by relevance/impact into paper_queue as pending.
#
# Usage:
#   ./update_top_pending.sh                       # default limit=500
#   ./update_top_pending.sh 1200                 # positional limit
#   ./update_top_pending.sh --limit 2000
#   ./update_top_pending.sh --db /path/arxiv.sqlite --limit 250

LIMIT=500
DB_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --limit|-n)
            LIMIT="${2:-}"
            shift 2
            ;;
        --db)
            DB_PATH="${2:-}"
            shift 2
            ;;
        -h|--help)
            cat <<'HELP'
Usage: update_top_pending.sh [N] [--db PATH] [--limit N]

Options:
  N              Positional limit (same as --limit)
  --db PATH      Path to arxiv sqlite DB (default: <repo>/arxiv_crawler/data/arxiv.sqlite)
  --limit, -n N  Number of top ranked papers to queue (default: 500)
  -h, --help     Show this help
HELP
            exit 0
            ;;
        *)
            if [[ "$1" =~ ^[0-9]+$ ]]; then
                LIMIT="$1"
                shift
            else
                echo "Unknown argument: $1" >&2
                exit 1
            fi
            ;;
    esac
done

if ! [[ "$LIMIT" =~ ^[0-9]+$ ]] || [ "$LIMIT" -le 0 ]; then
    echo "Error: limit must be a positive integer (got: $LIMIT)" >&2
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
find_project_root() {
    local dir="$1"
    while [[ "$dir" != "/" ]]; do
        if [[ -e "$dir/.git" ]]; then
            echo "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    echo "Error: Could not find project root (no .git found)" >&2
    return 1
}
PROJECT_ROOT="$(find_project_root "$SCRIPT_DIR")"

if [ -z "$DB_PATH" ]; then
    DB_PATH="$PROJECT_ROOT/arxiv_crawler/data/arxiv.sqlite"
fi

if [ ! -f "$DB_PATH" ]; then
    echo "Error: DB file not found: $DB_PATH" >&2
    exit 1
fi

sqlite3 "$DB_PATH" <<SQL
WITH latest_versions AS (
    SELECT arxiv_id, MAX(version) AS max_version
    FROM papers
    GROUP BY arxiv_id
),
ranked AS (
    SELECT
        p.id AS paper_id,
        ROW_NUMBER() OVER (
            ORDER BY pr.fmr_score DESC,
                     (MAX(a.h_index) IS NULL) ASC,
                     MAX(a.h_index) DESC,
                     pc.cited_by_count DESC
        ) AS rk
    FROM papers p
    JOIN latest_versions lv
      ON lv.arxiv_id = p.arxiv_id
     AND lv.max_version = p.version
    JOIN paper_relevance pr ON pr.paper_id = p.id
    LEFT JOIN paper_citations pc ON pc.paper_id = p.id
    LEFT JOIN paper_authors pa ON pa.paper_id = p.id
    LEFT JOIN authors a ON a.openalex_id = pa.author_openalex_id
    JOIN paper_queue pq ON pq.paper_id = p.id
    WHERE p.llm_relevant = 1
      AND p.disable_summary = 0
      AND (pq.summary_path IS NULL OR pq.summary_path = '')
    GROUP BY p.id
),
selected AS (
    SELECT paper_id, rk
    FROM ranked
    ORDER BY rk
    LIMIT ${LIMIT}
)
UPDATE paper_queue
SET status = 'pending',
    worker = NULL,
    started_at = NULL,
    claimed_by = NULL,
    claimed_at = NULL,
    priority = (${LIMIT} + 1 - (SELECT rk FROM selected WHERE selected.paper_id = paper_queue.paper_id))
WHERE paper_id IN (SELECT paper_id FROM selected);
SQL

printf "Updated top %s queued papers to pending in %s\n" "$LIMIT" "$DB_PATH"
