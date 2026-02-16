#!/usr/bin/env bash
set -euo pipefail

# Post-cleanup wrapper for 2023 organic semantic outputs.
#
# This produces cleaned copies in separate files/directories so the raw model
# outputs remain untouched.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT_DIR="queries/word_clouds_2023_organic_semantic"
INPUT_FILE="shotgun_ml_llm_2023_organic_semantic.txt"
INPUT_CSV="data/word_list_2023_organic_semantic_candidates.csv"
EXCLUDE_FILE="queries/word_clouds_2023_organic_semantic/llm_pass_exclusions_2023.list"
EXCLUDE_FILE_V2="queries/word_clouds_2023_organic_semantic/llm_pass_exclusions_2023_artifact_noise_v2.list"

OUTPUT_DIR="queries/word_clouds_2023_organic_semantic_cleaned"
OUTPUT_FILE="shotgun_ml_llm_2023_organic_semantic_cleaned.txt"
OUTPUT_CSV="data/word_list_2023_organic_semantic_candidates.cleaned.csv"

THEME_CAP="320"
GENERAL_CAP="2600"
SPLIT_MIN_SCORE="0.15"
OVERWRITE="0"
DRY_RUN="0"

print_help() {
  cat <<'HELP'
Usage: cleanup_word_clouds_2023_organic_semantic.sh [options]

Options:
  --overwrite               Allow overwriting cleaned outputs
  --dry-run                 Preview cleanup counts without writing files
  --theme-cap N             Max terms per themed cloud (default: 320)
  --general-cap N           Max terms in general cloud (default: 2600)
  --split-min-score FLOAT   Minimum score for splitting (default: 0.15)
  -h, --help                Show this help
HELP
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --overwrite)
      OVERWRITE="1"
      shift 1
      ;;
    --dry-run)
      DRY_RUN="1"
      shift 1
      ;;
    --theme-cap)
      THEME_CAP="$2"
      shift 2
      ;;
    --general-cap)
      GENERAL_CAP="$2"
      shift 2
      ;;
    --split-min-score)
      SPLIT_MIN_SCORE="$2"
      shift 2
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

INPUT_WORD_LIST_PATH="$INPUT_DIR/$INPUT_FILE"
OUTPUT_WORD_LIST_PATH="$OUTPUT_DIR/$OUTPUT_FILE"
OUTPUT_GENERAL_PATH="$OUTPUT_DIR/general_ml_llm_high_recall_95.txt"

if [[ ! -f "$INPUT_WORD_LIST_PATH" ]]; then
  echo "Error: Missing input word list: $INPUT_WORD_LIST_PATH" >&2
  exit 1
fi
if [[ ! -f "$INPUT_CSV" ]]; then
  echo "Error: Missing input CSV: $INPUT_CSV" >&2
  exit 1
fi
if [[ ! -f "$EXCLUDE_FILE" ]]; then
  echo "Error: Missing exclusion list: $EXCLUDE_FILE" >&2
  exit 1
fi
if [[ ! -f "$EXCLUDE_FILE_V2" ]]; then
  echo "Error: Missing exclusion list: $EXCLUDE_FILE_V2" >&2
  exit 1
fi

if [[ "$DRY_RUN" != "1" && "$OVERWRITE" != "1" ]]; then
  if [[ -e "$OUTPUT_WORD_LIST_PATH" || -e "$OUTPUT_CSV" || -e "$OUTPUT_GENERAL_PATH" ]]; then
    echo "Refusing to overwrite cleaned outputs." >&2
    echo "  Found one of: $OUTPUT_WORD_LIST_PATH, $OUTPUT_CSV, $OUTPUT_GENERAL_PATH" >&2
    echo "  Re-run with --overwrite to allow replacement." >&2
    exit 1
  fi
fi

echo "Cleaning 2023 word clouds into separate outputs"
echo "  input txt:          $INPUT_WORD_LIST_PATH"
echo "  input csv:          $INPUT_CSV"
echo "  exclusion file 1:   $EXCLUDE_FILE"
echo "  exclusion file 2:   $EXCLUDE_FILE_V2"
echo "  output txt:         $OUTPUT_WORD_LIST_PATH"
echo "  output csv:         $OUTPUT_CSV"
echo "  output dir:         $OUTPUT_DIR"
echo "  dry run:            $([[ "$DRY_RUN" == "1" ]] && echo yes || echo no)"
echo

CMD=(
  uv run python "$SCRIPT_DIR/cleanup_word_list.py"
  --input-word-list "$INPUT_WORD_LIST_PATH"
  --input-csv "$INPUT_CSV"
  --output-word-list "$OUTPUT_WORD_LIST_PATH"
  --output-csv "$OUTPUT_CSV"
  --exclude-file "$EXCLUDE_FILE"
  --exclude-file "$EXCLUDE_FILE_V2"
)
if [[ "$DRY_RUN" == "1" ]]; then
  CMD+=(--dry-run)
fi

"${CMD[@]}"

if [[ "$DRY_RUN" == "1" ]]; then
  echo
  echo "Dry run complete (no files written)."
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
echo "Cleaned general cloud: $OUTPUT_DIR/general_ml_llm_high_recall_95.txt"
echo "Cleaned themed dir:    $OUTPUT_DIR"
