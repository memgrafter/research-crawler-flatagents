#!/usr/bin/env bash
set -euo pipefail

# Queue top N papers for each word cloud, then seed into v2.
#
# Usage:
#   ./queue_top_per_word_cloud.sh
#   ./queue_top_per_word_cloud.sh --limit 500 --seed-limit 20000
#   ./queue_top_per_word_cloud.sh --no-seed
#   ./queue_top_per_word_cloud.sh --db /path/to/arxiv.sqlite

LIMIT=500
SEED_LIMIT=20000
DO_SEED=1
DB_PATH=""

print_help() {
  cat <<'HELP'
Usage: queue_top_per_word_cloud.sh [options]

Options:
  --limit, -n N     Top N papers per word cloud (default: 500)
  --seed-limit N    Max rows to seed into v2 in one pass (default: 20000)
  --db PATH         Path to arXiv sqlite DB (default: ../arxiv_crawler/data/arxiv.sqlite)
  --no-seed         Only queue in arXiv DB; skip v2 seed step
  -h, --help        Show this help
HELP
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit|-n)
      LIMIT="${2:-}"
      shift 2
      ;;
    --seed-limit)
      SEED_LIMIT="${2:-}"
      shift 2
      ;;
    --db)
      DB_PATH="${2:-}"
      shift 2
      ;;
    --no-seed)
      DO_SEED=0
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if ! [[ "$LIMIT" =~ ^[0-9]+$ ]] || [[ "$LIMIT" -le 0 ]]; then
  echo "Error: --limit must be a positive integer" >&2
  exit 1
fi

if ! [[ "$SEED_LIMIT" =~ ^[0-9]+$ ]] || [[ "$SEED_LIMIT" -le 0 ]]; then
  echo "Error: --seed-limit must be a positive integer" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V2_DIR="$SCRIPT_DIR"
WORD_CLOUD_DIR="$V2_DIR/queries/word_clouds"

if [[ -z "$DB_PATH" ]]; then
  DB_PATH="$V2_DIR/../arxiv_crawler/data/arxiv.sqlite"
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "Error: arXiv DB not found: $DB_PATH" >&2
  exit 1
fi

if [[ ! -d "$WORD_CLOUD_DIR" ]]; then
  echo "Error: word cloud dir not found: $WORD_CLOUD_DIR" >&2
  exit 1
fi

mapfile -t WORD_CLOUD_FILES < <(find "$WORD_CLOUD_DIR" -maxdepth 1 -type f -name '*.txt' | sort)

if [[ ${#WORD_CLOUD_FILES[@]} -eq 0 ]]; then
  echo "No word cloud files found in $WORD_CLOUD_DIR" >&2
  exit 1
fi

echo "Using arXiv DB: $DB_PATH"
echo "Word clouds: ${#WORD_CLOUD_FILES[@]}"
echo "Top per cloud: $LIMIT"
echo

for wc in "${WORD_CLOUD_FILES[@]}"; do
  echo "==> Queueing: $(basename "$wc")"
  (
    cd "$V2_DIR"
    ./run_search_repl.sh \
      --db "$DB_PATH" \
      --limit "$LIMIT" \
      --query-file "$wc" \
      --order-by impact \
      --llm-relevant-only \
      --auto-summarize
  )
  echo
done

if [[ "$DO_SEED" -eq 1 ]]; then
  echo "==> Seeding into v2 executions (seed-limit=$SEED_LIMIT)"
  (
    cd "$V2_DIR"
    python run.py --seed-only --seed-limit "$SEED_LIMIT"
  )
fi

echo "Done."
