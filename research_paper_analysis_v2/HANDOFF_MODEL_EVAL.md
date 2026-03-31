# Handoff: Model Comparison Evaluation (Trinity vs Step-3.5-Flash)

**Date:** 2026-03-30  
**Status:** 3 trinity runs needed, then evaluate all 10 pairs

---

## Context

We're comparing two free OpenRouter models for the research paper analysis pipeline:
- **Trinity:** `openrouter/arcee-ai/trinity-large-preview:free` (current production model)
- **Step-3.5-Flash:** `openrouter/stepfun/step-3.5-flash:free` (candidate replacement)

10 papers were processed with Step-3.5-Flash. 7 of those also have Trinity outputs. 3 need Trinity runs.

---

## Step 1: Run 3 remaining Trinity papers

Profiles are already set to Trinity. Run the fill DB:

```bash
cd ~/code/research_crawler/research_paper_analysis_v2
V2_EXECUTIONS_DB_PATH=data/v2_executions_trinity_fill.sqlite \
  nohup ./run.sh --workers 3 --daemon > logs/trinity_fill.log 2>&1 &
```

Monitor:
```bash
sqlite3 data/v2_executions_trinity_fill.sqlite "SELECT arxiv_id, status FROM executions;"
```

Wait until all 3 show `done`. Should take ~5 minutes.

Verify they used Trinity (not stepfun):
```bash
grep "trinity\|step-3.5" logs/trinity_fill.log | head -5
```

---

## Step 2: Run evaluation for all 10 papers

The evaluation prompt is at `eval_model_comparison_prompt.md`. Run it paper by paper using:

```bash
claude -p "<prompt with file paths>" --allowedTools "read"
```

### All 10 paper pairs

Files are in `data/`. Trinity digests have earlier timestamps (~2114xx), stepfun have later (~214xxx-2153xx).

| # | arxiv_id | Source txt | Trinity digest | Step-3.5-Flash digest |
|---|----------|-----------|---------------|----------------------|
| 1 | 2601.03034 | `2601.03034.txt` | `2601.03034_norwai-s-large-language-models-technical-report_20260330_211410.md` | `2601.03034_norwai-s-large-language-models-technical-report_20260330_214032.md` |
| 2 | 2603.19223 | `2603.19223.txt` | `2603.19223_f2llm-v2-..._20260330_211405.md` | `2603.19223_f2llm-v2-..._20260330_214223.md` |
| 3 | 2603.04647 | `2603.04647.txt` | `2603.04647_coordinated-semantic-..._20260330_211409.md` | `2603.04647_coordinated-semantic-..._20260330_214609.md` |
| 4 | 2602.23258 | `2602.23258.txt` | `2602.23258_agentdropoutv2-..._20260330_211413.md` | `2602.23258_agentdropoutv2-..._20260330_214637.md` |
| 5 | 2602.18540 | `2602.18540.txt` | `2602.18540_rodent-bench_20260330_211313.md` | `2602.18540_rodent-bench_20260330_214732.md` |
| 6 | 2602.21655 | `2602.21655.txt` | `2602.21655_cccaption-..._20260330_211319.md` | `2602.21655_cccaption-..._20260330_214804.md` |
| 7 | 2603.00517 | `2603.00517.txt` | `2603.00517_fastbus-..._20260330_211315.md` | `2603.00517_fastbus-..._20260330_215324.md` |
| 8 | 2603.25340 | `2603.25340.txt` | *(from trinity_fill run — find by timestamp)* | `2603.25340_large-language-model-as-token-compressor-..._20260330_214004.md` |
| 9 | 2602.12609 | `2602.12609.txt` | *(from trinity_fill run — find by timestamp)* | `2602.12609_quept-quantized-elastic-..._20260330_214400.md` |
| 10 | 2603.16535 | `2603.16535.txt` | *(from trinity_fill run — find by timestamp)* | `2603.16535_sympformer-..._20260330_214149.md` |

For papers 8-10, find the Trinity digest by:
```bash
ls data/2603.25340_*.md data/2602.12609_*.md data/2603.16535_*.md
```
The newer timestamp is Trinity (from the fill run).

### First result (already completed)

Paper 1 (2601.03034) was evaluated: **Trinity 35/60, Step-3.5-Flash 47/60**. Step-3.5-Flash was stronger on accuracy, depth, and actionability across all sections.

---

## Step 3: Compile results

After evaluating all 10, produce:
- Final summary table (all papers, both models, totals out of /420)
- Recommendation for production use
- Write results to `data/model_comparison_results.md`

---

## Important notes

- **Do NOT touch `config/profiles.yml`** while the main daemon might be running
- The stepfun test DB is `data/v2_executions_stepfun_test.sqlite` — do not mix with main `data/v2_executions.sqlite`
- The trinity fill DB is `data/v2_executions_trinity_fill.sqlite`
- Reports write to `data/` with timestamps in filenames — no clobbering risk
- After evaluation is complete, restore daemon: check `config/profiles.yml` has trinity, then start `run.py --workers 16 --daemon` with the main DB
