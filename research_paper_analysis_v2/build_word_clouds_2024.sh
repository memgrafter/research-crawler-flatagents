#!/usr/bin/env bash
set -euo pipefail

# Build 2024 word clouds in two steps:
# 1) Generate shotgun candidate terms from 2024 title+abstract (heuristic-only)
# 2) Split into per-cloud files + one general high-recall cloud
#
# Usage:
#   ./build_word_clouds_2024.sh
#   ./build_word_clouds_2024.sh --db ../arxiv_crawler/data/arxiv.sqlite --min-score 0.30 --top-papers 20000

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DB_PATH="$SCRIPT_DIR/../arxiv_crawler/data/arxiv.sqlite"
YEAR_PREFIX="24"
SCORE_COLUMN="fmr_2024"
FALLBACK_SCORE_COLUMN="fmr_score"
MIN_SCORE="0.30"
TOP_PAPERS="20000"
MAX_TERMS="1400"
MIN_DF_PHRASE="5"
MIN_DF_UNIGRAM="10"
MIN_DF_ACRONYM="3"
MAX_DOC_RATIO="0.40"
OUTPUT_DIR="queries/word_clouds_2024"
OUTPUT_FILE="shotgun_ml_llm_2024.txt"
CSV_OUTPUT="data/word_list_2024_candidates.csv"
THEME_CAP="260"
GENERAL_CAP="2200"

print_help() {
  cat <<'HELP'
Usage: build_word_clouds_2024.sh [options]

Options:
  --db PATH                 Path to arXiv sqlite DB
  --year-prefix YY          arXiv year prefix (default: 24)
  --score-column NAME       Preferred score column (default: fmr_2024)
  --fallback-score-column N Fallback score column (default: fmr_score)
  --min-score FLOAT         Candidate paper score floor (default: 0.30)
  --top-papers N            Max papers scanned (default: 20000)
  --max-terms N             Max terms emitted by generator (default: 1400)
  --min-df-phrase N         Min phrase doc freq (default: 5)
  --min-df-unigram N        Min unigram doc freq (default: 10)
  --min-df-acronym N        Min acronym doc freq (default: 3)
  --max-doc-ratio FLOAT     Drop terms too frequent across docs (default: 0.40)
  --theme-cap N             Max terms per themed cloud (default: 260)
  --general-cap N           Max terms in general high-recall cloud (default: 2200)
  --output-dir PATH         Output dir (default: queries/word_clouds_2024)
  --output-file FILE        Main generated list filename (default: shotgun_ml_llm_2024.txt)
  --csv-output PATH         Candidate CSV path (default: data/word_list_2024_candidates.csv)
  -h, --help                Show this help
HELP
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)
      DB_PATH="$2"
      shift 2
      ;;
    --year-prefix)
      YEAR_PREFIX="$2"
      shift 2
      ;;
    --score-column)
      SCORE_COLUMN="$2"
      shift 2
      ;;
    --fallback-score-column)
      FALLBACK_SCORE_COLUMN="$2"
      shift 2
      ;;
    --min-score)
      MIN_SCORE="$2"
      shift 2
      ;;
    --top-papers)
      TOP_PAPERS="$2"
      shift 2
      ;;
    --max-terms)
      MAX_TERMS="$2"
      shift 2
      ;;
    --min-df-phrase)
      MIN_DF_PHRASE="$2"
      shift 2
      ;;
    --min-df-unigram)
      MIN_DF_UNIGRAM="$2"
      shift 2
      ;;
    --min-df-acronym)
      MIN_DF_ACRONYM="$2"
      shift 2
      ;;
    --max-doc-ratio)
      MAX_DOC_RATIO="$2"
      shift 2
      ;;
    --theme-cap)
      THEME_CAP="$2"
      shift 2
      ;;
    --general-cap)
      GENERAL_CAP="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --output-file)
      OUTPUT_FILE="$2"
      shift 2
      ;;
    --csv-output)
      CSV_OUTPUT="$2"
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

if [[ ! -f "$DB_PATH" ]]; then
  echo "Error: DB not found: $DB_PATH" >&2
  exit 1
fi

echo "Building 2024 word clouds"
echo "  DB:                 $DB_PATH"
echo "  year prefix:        $YEAR_PREFIX"
echo "  score column:       $SCORE_COLUMN (fallback: $FALLBACK_SCORE_COLUMN)"
echo "  min score:          $MIN_SCORE"
echo "  top papers:         $TOP_PAPERS"
echo "  output dir:         $OUTPUT_DIR"
echo "  general cloud cap:  $GENERAL_CAP"
echo "  themed cloud cap:   $THEME_CAP"
echo

python "$SCRIPT_DIR/generate_word_list_2024.py" \
  --db-path "$DB_PATH" \
  --year-prefix "$YEAR_PREFIX" \
  --score-column "$SCORE_COLUMN" \
  --fallback-score-column "$FALLBACK_SCORE_COLUMN" \
  --min-score "$MIN_SCORE" \
  --top-papers "$TOP_PAPERS" \
  --max-terms "$MAX_TERMS" \
  --min-df-phrase "$MIN_DF_PHRASE" \
  --min-df-unigram "$MIN_DF_UNIGRAM" \
  --min-df-acronym "$MIN_DF_ACRONYM" \
  --max-doc-ratio "$MAX_DOC_RATIO" \
  --output-dir "$OUTPUT_DIR" \
  --output-file "$OUTPUT_FILE" \
  --csv-output "$CSV_OUTPUT"

echo
python "$SCRIPT_DIR/split_word_clouds_2024.py" \
  --input-csv "$CSV_OUTPUT" \
  --output-dir "$OUTPUT_DIR" \
  --theme-cap "$THEME_CAP" \
  --general-cap "$GENERAL_CAP"

echo
echo "Done."
echo "General high-recall cloud: $OUTPUT_DIR/general_ml_llm_high_recall_95.txt"
echo "Themed clouds dir:         $OUTPUT_DIR"
