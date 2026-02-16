#!/usr/bin/env bash
set -euo pipefail

# Build organic (corpus-first) ML/LLM word clouds with semantic reranking.
#
# This pipeline does NOT require paper_relevance coverage. It mines terms from
# papers.title + papers.abstract for the selected year prefix, then reranks terms
# by similarity to anchor concepts.
#
# Safe-by-default behavior:
# - Refuses to overwrite existing outputs unless --overwrite is passed.
#
# Examples:
#   # 2023 organic + semantic
#   ./build_word_clouds_organic_semantic.sh \
#     --year-prefix 23 \
#     --output-dir queries/word_clouds_2023_organic_semantic \
#     --output-file shotgun_ml_llm_2023_organic_semantic.txt \
#     --csv-output data/word_list_2023_organic_semantic_candidates.csv
#
#   # 2024 organic + semantic
#   ./build_word_clouds_organic_semantic.sh \
#     --year-prefix 24 \
#     --output-dir queries/word_clouds_2024_organic_semantic \
#     --output-file shotgun_ml_llm_2024_organic_semantic.txt \
#     --csv-output data/word_list_2024_organic_semantic_candidates.csv
#
#   # 2025 organic + lexical-only fallback
#   ./build_word_clouds_organic_semantic.sh \
#     --year-prefix 25 --disable-semantic \
#     --output-dir queries/word_clouds_2025_organic \
#     --output-file shotgun_ml_llm_2025_organic.txt \
#     --csv-output data/word_list_2025_organic_candidates.csv

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DB_PATH="$SCRIPT_DIR/../arxiv_crawler/data/arxiv.sqlite"
YEAR_PREFIX="23"
TOP_PAPERS="70000"
MAX_TERMS="2500"
NGRAM_MIN="2"
NGRAM_MAX="4"
INCLUDE_UNIGRAMS="0"
DISABLE_ACRONYMS="0"
DISABLE_SEMANTIC="0"
THEME_CAP="320"
GENERAL_CAP="2600"
SPLIT_MIN_SCORE="0.15"
OVERWRITE="0"
DRY_RUN="0"

LEXICAL_WEIGHT="0.55"
SEMANTIC_WEIGHT="0.45"
SEMANTIC_FLOOR="0.00"
MODEL_PATTERN_BOOST="0.05"
EMBEDDING_CONFIG=""
MODEL_NAME=""
EMBEDDING_BATCH_SIZE=""

OUTPUT_DIR=""
OUTPUT_FILE=""
CSV_OUTPUT=""

ANCHOR_FILES=()
ANCHORS=()
EXCLUDE_FILES=()
EXCLUDE_TERMS=()

print_help() {
  cat <<'HELP'
Usage: build_word_clouds_organic_semantic.sh [options]

Options:
  --db PATH                 Path to arXiv sqlite DB
  --year-prefix YY          arXiv year prefix (default: 23)
  --top-papers N            Max papers scanned from target year (default: 70000)
  --max-terms N             Max terms emitted by generator (default: 2500)
  --ngram-min N             Minimum n-gram size for phrase mining (default: 2)
  --ngram-max N             Maximum n-gram size for phrase mining (default: 4)
  --include-unigrams        Include technical unigrams in addition to multi-word terms
  --disable-acronyms        Disable acronym extraction

  --disable-semantic        Disable semantic reranking (lexical score only)
  --anchor-file PATH        Newline-delimited anchor file (repeatable)
  --anchor TERM             Inline anchor term (repeatable)
  --exclude-file PATH       Newline-delimited exclusion list file (repeatable)
  --exclude-term TERM       Inline exclusion term/token (repeatable)
  --embedding-config PATH   YAML embedding config (e.g. ../relevance_scoring_2024.yml)
  --model-name NAME         SentenceTransformer model override
  --embedding-batch-size N  Embedding batch size override
  --lexical-weight FLOAT    Weight for lexical signal (default: 0.55)
  --semantic-weight FLOAT   Weight for semantic signal (default: 0.45)
  --semantic-floor FLOAT    Similarity floor (default: 0.00)
  --model-pattern-boost F   Boost for '<something> model(s)' terms (default: 0.05)

  --theme-cap N             Max terms per themed cloud (default: 320)
  --general-cap N           Max terms in general cloud (default: 2600)
  --split-min-score FLOAT   Minimum generator score passed to splitter (default: 0.15)

  --output-dir PATH         Output dir (default: queries/word_clouds_<YY>_organic_semantic)
  --output-file FILE        Main generated list filename (default: shotgun_ml_llm_<YY>_organic_semantic.txt)
  --csv-output PATH         Candidate CSV path (default: data/word_list_<YY>_organic_semantic_candidates.csv)

  --overwrite               Allow overwriting existing outputs
  --dry-run                 Run generator in dry-run mode (no files written)
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
    --top-papers)
      TOP_PAPERS="$2"
      shift 2
      ;;
    --max-terms)
      MAX_TERMS="$2"
      shift 2
      ;;
    --ngram-min)
      NGRAM_MIN="$2"
      shift 2
      ;;
    --ngram-max)
      NGRAM_MAX="$2"
      shift 2
      ;;
    --include-unigrams)
      INCLUDE_UNIGRAMS="1"
      shift 1
      ;;
    --disable-acronyms)
      DISABLE_ACRONYMS="1"
      shift 1
      ;;
    --disable-semantic)
      DISABLE_SEMANTIC="1"
      shift 1
      ;;
    --anchor-file)
      ANCHOR_FILES+=("$2")
      shift 2
      ;;
    --anchor)
      ANCHORS+=("$2")
      shift 2
      ;;
    --exclude-file)
      EXCLUDE_FILES+=("$2")
      shift 2
      ;;
    --exclude-term)
      EXCLUDE_TERMS+=("$2")
      shift 2
      ;;
    --embedding-config)
      EMBEDDING_CONFIG="$2"
      shift 2
      ;;
    --model-name)
      MODEL_NAME="$2"
      shift 2
      ;;
    --embedding-batch-size)
      EMBEDDING_BATCH_SIZE="$2"
      shift 2
      ;;
    --lexical-weight)
      LEXICAL_WEIGHT="$2"
      shift 2
      ;;
    --semantic-weight)
      SEMANTIC_WEIGHT="$2"
      shift 2
      ;;
    --semantic-floor)
      SEMANTIC_FLOOR="$2"
      shift 2
      ;;
    --model-pattern-boost)
      MODEL_PATTERN_BOOST="$2"
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
    --split-min-score)
      SPLIT_MIN_SCORE="$2"
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
    --overwrite)
      OVERWRITE="1"
      shift 1
      ;;
    --dry-run)
      DRY_RUN="1"
      shift 1
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

if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="queries/word_clouds_${YEAR_PREFIX}_organic_semantic"
fi
if [[ -z "$OUTPUT_FILE" ]]; then
  OUTPUT_FILE="shotgun_ml_llm_${YEAR_PREFIX}_organic_semantic.txt"
fi
if [[ -z "$CSV_OUTPUT" ]]; then
  CSV_OUTPUT="data/word_list_${YEAR_PREFIX}_organic_semantic_candidates.csv"
fi

if [[ ! -f "$DB_PATH" ]]; then
  echo "Error: DB not found: $DB_PATH" >&2
  exit 1
fi

PRIMARY_OUTPUT="$OUTPUT_DIR/$OUTPUT_FILE"
GENERAL_OUTPUT="$OUTPUT_DIR/general_ml_llm_high_recall_95.txt"

if [[ "$DRY_RUN" != "1" && "$OVERWRITE" != "1" ]]; then
  if [[ -e "$PRIMARY_OUTPUT" || -e "$CSV_OUTPUT" || -e "$GENERAL_OUTPUT" ]]; then
    echo "Refusing to overwrite existing outputs." >&2
    echo "  Found one of: $PRIMARY_OUTPUT, $CSV_OUTPUT, $GENERAL_OUTPUT" >&2
    echo "  Re-run with --overwrite to allow replacement." >&2
    exit 1
  fi
fi

echo "Building organic semantic word clouds"
echo "  DB:                 $DB_PATH"
echo "  year prefix:        $YEAR_PREFIX"
echo "  top papers:         $TOP_PAPERS"
echo "  ngram range:        $NGRAM_MIN-$NGRAM_MAX"
echo "  semantic enabled:   $([[ "$DISABLE_SEMANTIC" == "1" ]] && echo no || echo yes)"
echo "  embedding config:   ${EMBEDDING_CONFIG:-<none>}"
echo "  model override:     ${MODEL_NAME:-<none>}"
echo "  anchor files:       ${#ANCHOR_FILES[@]}"
echo "  exclusion files:    ${#EXCLUDE_FILES[@]}"
echo "  dry run:            $([[ "$DRY_RUN" == "1" ]] && echo yes || echo no)"
echo "  output dir:         $OUTPUT_DIR"
echo "  csv output:         $CSV_OUTPUT"
echo

GEN_CMD=(
  uv run python "$SCRIPT_DIR/generate_word_list_organic_semantic.py"
  --db-path "$DB_PATH"
  --year-prefix "$YEAR_PREFIX"
  --top-papers "$TOP_PAPERS"
  --max-terms "$MAX_TERMS"
  --ngram-min "$NGRAM_MIN"
  --ngram-max "$NGRAM_MAX"
  --lexical-weight "$LEXICAL_WEIGHT"
  --semantic-weight "$SEMANTIC_WEIGHT"
  --semantic-floor "$SEMANTIC_FLOOR"
  --model-pattern-boost "$MODEL_PATTERN_BOOST"
  --output-dir "$OUTPUT_DIR"
  --output-file "$OUTPUT_FILE"
  --csv-output "$CSV_OUTPUT"
)

if [[ "$INCLUDE_UNIGRAMS" == "1" ]]; then
  GEN_CMD+=(--include-unigrams)
fi
if [[ "$DISABLE_ACRONYMS" == "1" ]]; then
  GEN_CMD+=(--disable-acronyms)
fi
if [[ "$DISABLE_SEMANTIC" == "1" ]]; then
  GEN_CMD+=(--disable-semantic)
fi
if [[ -n "$EMBEDDING_CONFIG" ]]; then
  GEN_CMD+=(--embedding-config "$EMBEDDING_CONFIG")
fi
if [[ -n "$MODEL_NAME" ]]; then
  GEN_CMD+=(--model-name "$MODEL_NAME")
fi
if [[ -n "$EMBEDDING_BATCH_SIZE" ]]; then
  GEN_CMD+=(--embedding-batch-size "$EMBEDDING_BATCH_SIZE")
fi
if [[ "$DRY_RUN" == "1" ]]; then
  GEN_CMD+=(--dry-run)
fi

for anchor_file in "${ANCHOR_FILES[@]}"; do
  GEN_CMD+=(--anchor-file "$anchor_file")
done
for anchor in "${ANCHORS[@]}"; do
  GEN_CMD+=(--anchor "$anchor")
done
for exclude_file in "${EXCLUDE_FILES[@]}"; do
  GEN_CMD+=(--exclude-file "$exclude_file")
done
for exclude_term in "${EXCLUDE_TERMS[@]}"; do
  GEN_CMD+=(--exclude-term "$exclude_term")
done

"${GEN_CMD[@]}"

if [[ "$DRY_RUN" == "1" ]]; then
  echo
  echo "Dry run complete (no files written)."
  exit 0
fi

echo
uv run python "$SCRIPT_DIR/split_word_clouds_2024.py" \
  --input-csv "$CSV_OUTPUT" \
  --output-dir "$OUTPUT_DIR" \
  --theme-cap "$THEME_CAP" \
  --general-cap "$GENERAL_CAP" \
  --min-score "$SPLIT_MIN_SCORE"

echo
echo "Done."
echo "General high-recall cloud: $OUTPUT_DIR/general_ml_llm_high_recall_95.txt"
echo "Themed clouds dir:         $OUTPUT_DIR"
