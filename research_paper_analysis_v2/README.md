# Research Paper Analysis V2

This folder contains the simplified v2 pipeline configuration and hooks.
It is standalone from `research_paper_analysis` (v1) runtime modules.

## What exists now
- `config/` FlatMachine + FlatAgent configs
- `src/research_paper_analysis_v2/hooks.py` custom hook actions used by the machine:
  - `collect_corpus_signals`
  - `derive_terminology_tags`
  - `prepend_frontmatter_v2`

## Install (editable)
```bash
cd /Users/trentrobbins/code/research_crawler/research_paper_analysis_v2
uv sync
```

## Run one worker (prototype)
Launch one fire-and-forget worker against the existing queue:

```bash
cd /Users/trentrobbins/code/research_crawler/research_paper_analysis_v2
./run_single_worker.sh
# optional explicit id
./run_single_worker.sh --worker-id paper-worker-v2-manual
```

This uses:
- `config/single_worker_launcher.yml`
- `config/paper_analysis_worker.yml`
- v2 hook module `research_paper_analysis_v2.distributed_hooks.DistributedPaperAnalysisHooks`

## Notes
- Reuses corpus DB at `../arxiv_crawler/data/arxiv.sqlite` by default.
- Set `ARXIV_DB_PATH` to override database location.
- Models configured in `config/profiles.yml` (LiteLLM ids):
  - `openrouter/openai/gpt-oss-120b:free`
  - `openrouter/openrouter/pony-alpha` (double-prefix required with current LiteLLM/OpenRouter handling)
