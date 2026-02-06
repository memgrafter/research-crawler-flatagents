#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

print_help() {
  cat <<'HELP'
Usage: run_auto_search.sh --word-cloud PATH [options]

Defaults:
  --db            ../../arxiv_crawler/data/arxiv.sqlite
  --limit         150
  --order-by      impact
  --llm-only      true
  --auto-summarize enabled

Required:
  --word-cloud PATH     Newline-delimited term list (OR joined)

Options:
  --db PATH             Override database path
  --limit N             Override match limit
  --order-by ORDER      bm25 | impact | hybrid
  --llm-only            Restrict to llm_relevant = 1 (default)
  --all                 Include non-llm_relevant papers
  --auto-summarize      Queue all matches without prompts (default)
  --no-auto-summarize   Prompt for actions instead of auto-queue
  --show-count          Print total matches (slow on large DBs)
  --rebuild-fts         Force FTS rebuild before searching

Notes:
  - This script delegates to run_search_repl.sh (DB-only queue update).
  - Use --show-count to print total matches (slow on large DBs).
HELP
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  print_help
  exit 0
fi

DB_PATH="$SCRIPT_DIR/../../arxiv_crawler/data/arxiv.sqlite"
WORD_CLOUD=""
LIMIT="150"
ORDER_BY="impact"
LLM_ONLY="true"
AUTO_SUMMARIZE="true"

PASSTHROUGH_ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --word-cloud)
      WORD_CLOUD="${2:-}"
      shift 2
      ;;
    --db|--db-path)
      DB_PATH="${2:-}"
      shift 2
      ;;
    --limit)
      LIMIT="${2:-}"
      shift 2
      ;;
    --order-by)
      ORDER_BY="${2:-}"
      shift 2
      ;;
    --llm-only|--llm-relevant-only)
      LLM_ONLY="true"
      shift
      ;;
    --all)
      LLM_ONLY="false"
      shift
      ;;
    --auto-summarize)
      AUTO_SUMMARIZE="true"
      shift
      ;;
    --no-auto-summarize)
      AUTO_SUMMARIZE="false"
      shift
      ;;
    --)
      shift
      PASSTHROUGH_ARGS+=("$@")
      break
      ;;
    *)
      PASSTHROUGH_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -z "$WORD_CLOUD" ]]; then
  echo "Error: --word-cloud PATH is required" >&2
  echo "" >&2
  print_help >&2
  exit 1
fi

ARGS=(
  --db "$DB_PATH"
  --limit "$LIMIT"
  --query-file "$WORD_CLOUD"
  --order-by "$ORDER_BY"
)

if [ "$LLM_ONLY" = "true" ]; then
  ARGS+=(--llm-relevant-only)
fi

if [ "$AUTO_SUMMARIZE" = "true" ]; then
  ARGS+=(--auto-summarize)
fi

ARGS+=("${PASSTHROUGH_ARGS[@]}")

"$SCRIPT_DIR/run_search_repl.sh" "${ARGS[@]}"
