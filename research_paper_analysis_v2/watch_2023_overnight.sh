#!/usr/bin/env bash
set -euo pipefail

# Ultralight overnight watcher for 2023 scoring + queue top-up.
#
# Usage:
#   ./watch_2023_overnight.sh
#   ./watch_2023_overnight.sh --interval 300 --target-pending 1200 --max-insert 1200
#   ./watch_2023_overnight.sh --once
#
# Env overrides also work:
#   INTERVAL_SEC=300 TARGET_PENDING=1200 MAX_INSERT_PER_CYCLE=1200 ./watch_2023_overnight.sh

INTERVAL_SEC="${INTERVAL_SEC:-300}"
TARGET_PENDING="${TARGET_PENDING:-1200}"
MAX_INSERT_PER_CYCLE="${MAX_INSERT_PER_CYCLE:-1200}"
ONCE=0
FILL=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval)
      INTERVAL_SEC="$2"; shift 2 ;;
    --target-pending)
      TARGET_PENDING="$2"; shift 2 ;;
    --max-insert)
      MAX_INSERT_PER_CYCLE="$2"; shift 2 ;;
    --once)
      ONCE=1; shift ;;
    --monitor-only)
      FILL=0; shift ;;
    -h|--help)
      cat <<'HELP'
Usage: watch_2023_overnight.sh [options]

Options:
  --interval SECONDS       Poll interval (default: 300)
  --target-pending N       Keep at least N 2023 pending rows (default: 1200)
  --max-insert N           Max inserts per top-up cycle (default: 1200)
  --once                   Run one cycle and exit
  --monitor-only           Print status only; do not enqueue
  -h, --help               Show help
HELP
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 not found" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found" >&2
  exit 1
fi

echo "watch_2023_overnight starting"
echo "  interval_sec=$INTERVAL_SEC"
echo "  target_pending=$TARGET_PENDING"
echo "  max_insert_per_cycle=$MAX_INSERT_PER_CYCLE"
echo "  fill_enabled=$FILL"

dump_status() {
  local status_line
  status_line="$(sqlite3 ../arxiv_crawler/data/arxiv.sqlite "SELECT COUNT(*) || '|' || SUM(CASE WHEN pr.fmr_2023 IS NOT NULL THEN 1 ELSE 0 END) || '|' || SUM(CASE WHEN pr.fmr_2023 IS NULL THEN 1 ELSE 0 END) FROM papers p LEFT JOIN paper_relevance pr ON pr.paper_id=p.id WHERE p.arxiv_id LIKE '23%';")"
  local total done remaining
  IFS='|' read -r total done remaining <<< "$status_line"

  local qline
  qline="$(sqlite3 data/v2_executions.sqlite "SELECT SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) || '|' || SUM(CASE WHEN status IN ('prepping','prepped','wrapping','analyzing','analyzed') THEN 1 ELSE 0 END) || '|' || COUNT(*) FROM executions WHERE arxiv_id LIKE '23%';")"
  local pending in_flight exec_total
  IFS='|' read -r pending in_flight exec_total <<< "$qline"

  local scorer_up="no"
  if pgrep -f "relevance_scoring_2024.main --target-score-column fmr_2023" >/dev/null 2>&1; then
    scorer_up="yes"
  fi

  printf "[%s] scored=%s/%s remaining=%s | exec_total=%s pending=%s inflight=%s | scorer=%s\n" \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$done" "$total" "$remaining" "$exec_total" "$pending" "$in_flight" "$scorer_up" >&2

  echo "$pending"
}

fill_once() {
  MAX_INSERT_PER_CYCLE="$MAX_INSERT_PER_CYCLE" uv run python - <<'PY'
import os
import re
import sqlite3
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ARXIV_DB = Path('../arxiv_crawler/data/arxiv.sqlite')
V2_DB = Path('data/v2_executions.sqlite')
WC_DIR = Path('queries/word_clouds_2023_organic_semantic_cleaned')
MAX_INSERT = int(os.environ.get('MAX_INSERT_PER_CYCLE', '1200'))

CORE = {
    'cs.LG','cs.CL','cs.AI','cs.CV','cs.IR','cs.NE',
    'stat.ML','cs.MA','cs.SD','eess.AS','eess.IV'
}

wc_terms = []
for p in sorted(WC_DIR.glob('*.txt')):
    for line in p.read_text(encoding='utf-8').splitlines():
        t = line.strip()
        if not t or t.startswith('#'):
            continue
        wc_terms.append(t.lower())
wc_terms = sorted(set(wc_terms))
wc_patterns = [(t, re.compile(r'\b' + re.escape(t) + r'\b', re.IGNORECASE)) for t in wc_terms]

aconn = sqlite3.connect(ARXIV_DB)
aconn.row_factory = sqlite3.Row
anchor_rows = aconn.execute(
    """
    SELECT DISTINCT LOWER(json_extract(pr.details_json, '$.best_anchor')) AS term
    FROM papers p
    JOIN paper_relevance pr ON pr.paper_id = p.id
    WHERE p.arxiv_id LIKE '23%'
      AND pr.fmr_2023 IS NOT NULL
      AND json_extract(pr.details_json, '$.best_anchor') IS NOT NULL
    """
).fetchall()
anchor_terms = sorted({r['term'] for r in anchor_rows if r['term']})
anchor_patterns = [(t, re.compile(r'\b' + re.escape(t) + r'\b', re.IGNORECASE)) for t in anchor_terms]

rows = aconn.execute(
    """
    SELECT
      p.id AS paper_id,
      p.arxiv_id,
      p.primary_category,
      p.llm_relevant,
      COALESCE(p.title, '') AS title,
      COALESCE(p.abstract, '') AS abstract,
      pr.fmr_2023 AS score
    FROM papers p
    JOIN paper_relevance pr ON pr.paper_id = p.id
    WHERE p.arxiv_id LIKE '23%'
      AND pr.fmr_2023 IS NOT NULL
      AND pr.fmr_2023 >= 0.40
    ORDER BY pr.fmr_2023 DESC, p.arxiv_id ASC
    """
).fetchall()
aconn.close()

vconn = sqlite3.connect(V2_DB)
vconn.row_factory = sqlite3.Row
vconn.execute('PRAGMA busy_timeout = 1000')
existing = {r['arxiv_id'] for r in vconn.execute("SELECT arxiv_id FROM executions WHERE arxiv_id LIKE '23%'")}

eligible = []
for r in rows:
    arxiv_id = r['arxiv_id']
    if arxiv_id in existing:
        continue
    if int(r['llm_relevant'] or 0) != 1:
        continue
    if (r['primary_category'] or '') not in CORE:
        continue

    score = float(r['score'])
    if score >= 0.60:
        band = '>=0.60'
        hits = 0
        pass_rule = True
    elif score >= 0.55:
        band = '0.55-0.60'
        hits = 0
        pass_rule = True
    else:
        band = '0.50-0.55' if score >= 0.50 else '0.40-0.50'
        text = (r['title'] + ' ' + r['abstract']).strip()
        wc_hits = {t for t, p in wc_patterns if p.search(text)}
        anchor_hits = {t for t, p in anchor_patterns if p.search(text)}
        hits = len(wc_hits | anchor_hits)
        pass_rule = (hits >= 1) if score >= 0.50 else (hits >= 2)

    if not pass_rule:
        continue

    priority = score if score >= 0.55 else score + (0.001 * min(hits, 20))
    eligible.append((arxiv_id, int(r['paper_id']), r['title'], r['abstract'], float(priority), band))

if MAX_INSERT > 0:
    eligible = eligible[:MAX_INSERT]

insert_sql = """
INSERT OR IGNORE INTO executions (
  execution_id, arxiv_id, paper_id, title, authors, abstract,
  status, created_at, updated_at, prep_output, result_path, error, priority
)
VALUES (?, ?, ?, ?, '', ?, 'pending', ?, ?, NULL, NULL, NULL, ?)
"""

now = datetime.now(timezone.utc).isoformat()
inserted_by_band = defaultdict(int)
inserted_total = 0

for arxiv_id, paper_id, title, abstract, priority, band in eligible:
    retries = 0
    while True:
        try:
            cur = vconn.execute(insert_sql, (uuid.uuid4().hex, arxiv_id, paper_id, title, abstract, now, now, priority))
            vconn.commit()
            if cur.rowcount and cur.rowcount > 0:
                inserted_by_band[band] += 1
                inserted_total += 1
            break
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower():
                retries += 1
                if retries > 200:
                    break
                time.sleep(0.1)
                continue
            raise

vconn.close()
print(f"topup_inserted_total={inserted_total}")
for b in ['>=0.60','0.55-0.60','0.50-0.55','0.40-0.50']:
    print(f"topup_inserted_{b}={inserted_by_band.get(b,0)}")
PY
}

while true; do
  pending_now="$(dump_status | tail -n 1)"

  if [[ "$FILL" -eq 1 ]]; then
    if [[ "$pending_now" =~ ^[0-9]+$ ]] && (( pending_now < TARGET_PENDING )); then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] pending below target; topping up..."
      if ! fill_once; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] top-up failed (will retry next cycle)" >&2
      fi
    fi
  fi

  if [[ "$ONCE" -eq 1 ]]; then
    break
  fi
  sleep "$INTERVAL_SEC"
done
