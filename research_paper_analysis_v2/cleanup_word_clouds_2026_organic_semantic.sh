#!/usr/bin/env bash
set -euo pipefail

# Post-cleanup wrapper for 2026 organic semantic outputs.
# Produces cleaned copies in word_clouds_2026 (the canonical dir).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INPUT_DIR="queries/word_clouds_2026_organic_semantic"
INPUT_FILE="shotgun_ml_llm_26_organic_semantic.txt"
INPUT_CSV="data/word_list_26_organic_semantic_candidates.csv"
EXCLUDE_FILE="queries/word_clouds_2026_organic_semantic/llm_pass_exclusions_2026.list"

OUTPUT_DIR="queries/word_clouds_2026"
OUTPUT_FILE="shotgun_ml_llm_26_cleaned.txt"
OUTPUT_CSV="data/word_list_26_organic_semantic_candidates.cleaned.csv"

THEME_CAP="320"
GENERAL_CAP="2600"
SPLIT_MIN_SCORE="0.15"
OVERWRITE="0"
DRY_RUN="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --overwrite) OVERWRITE="1"; shift ;;
    --dry-run)   DRY_RUN="1"; shift ;;
    --theme-cap) THEME_CAP="$2"; shift 2 ;;
    --general-cap) GENERAL_CAP="$2"; shift 2 ;;
    --split-min-score) SPLIT_MIN_SCORE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: cleanup_word_clouds_2026_organic_semantic.sh [--overwrite] [--dry-run]"
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

INPUT_WORD_LIST_PATH="$INPUT_DIR/$INPUT_FILE"
OUTPUT_WORD_LIST_PATH="$OUTPUT_DIR/$OUTPUT_FILE"
OUTPUT_GENERAL_PATH="$OUTPUT_DIR/general_ml_llm_high_recall_95.txt"

for f in "$INPUT_WORD_LIST_PATH" "$INPUT_CSV" "$EXCLUDE_FILE"; do
  if [[ ! -f "$f" ]]; then
    echo "Error: Missing: $f" >&2
    exit 1
  fi
done

if [[ "$DRY_RUN" != "1" && "$OVERWRITE" != "1" ]]; then
  if [[ -e "$OUTPUT_WORD_LIST_PATH" || -e "$OUTPUT_CSV" || -e "$OUTPUT_GENERAL_PATH" ]]; then
    echo "Refusing to overwrite. Re-run with --overwrite." >&2
    exit 1
  fi
fi

echo "Cleaning 2026 word clouds"
echo "  input:      $INPUT_WORD_LIST_PATH"
echo "  exclusions: $EXCLUDE_FILE"
echo "  output dir: $OUTPUT_DIR"
echo

CMD=(
  uv run python "$SCRIPT_DIR/cleanup_word_list.py"
  --input-word-list "$INPUT_WORD_LIST_PATH"
  --input-csv "$INPUT_CSV"
  --output-word-list "$OUTPUT_WORD_LIST_PATH"
  --output-csv "$OUTPUT_CSV"
  --exclude-file "$EXCLUDE_FILE"
)
if [[ "$DRY_RUN" == "1" ]]; then CMD+=(--dry-run); fi

"${CMD[@]}"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "Dry run complete."
  exit 0
fi

echo
uv run python "$SCRIPT_DIR/split_word_clouds_2024.py" \
  --input-csv "$OUTPUT_CSV" \
  --output-dir "$OUTPUT_DIR" \
  --theme-cap "$THEME_CAP" \
  --general-cap "$GENERAL_CAP" \
  --min-score "$SPLIT_MIN_SCORE"

echo
echo "Done."
echo "Cleaned dir: $OUTPUT_DIR"
