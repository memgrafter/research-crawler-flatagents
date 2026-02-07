# Research Paper Analysis V2 Config

This folder contains the v2 FlatMachine/FlatAgent config skeleton.

## Notes
- Specs are aligned to the current FlatAgents/FlatMachines references:
  - `spec: flatagent`
  - `spec: flatmachine`
  - `spec: flatprofiles`
  - `spec_version: "1.0.0"`
- Prototype models are both OpenRouter (configured with LiteLLM ids):
  - `openrouter/openai/gpt-oss-120b:free`
  - `openrouter/openrouter/pony-alpha` (double-prefix required with current LiteLLM/OpenRouter handling)
- Temperature is intentionally left at **1.0** for all profiles during testing.
- Agent outputs are plain text (`output.content`) for this prototype; no structured JSON schemas are used.

## Hook actions used by machine.yml
`machine.yml` uses these custom actions from `research_paper_analysis_v2.hooks.V2Hooks`:

1. `collect_corpus_signals`
   - Inputs: `context.arxiv_id`, `context.title`, `context.abstract`
   - Outputs:
     - `corpus_signals`
     - `corpus_neighbors`
     - `neighbors_considered`
     - `neighbors_used`

2. `derive_terminology_tags`
   - Inputs: paper text + corpus neighbors
   - Outputs:
     - `terminology_tags` (list[str])
     - `domain_tags` (list[str])
     - `terminology_tag_meta` (object/map)

3. `prepend_frontmatter_v2`
   - Inputs: report body + metadata + tags
   - Outputs:
     - `frontmatter`
     - `formatted_report`

4. `normalize_judge_decision`
   - Inputs: `context.judge_decision_raw`
   - Outputs:
     - `judge_decision` (`PASS` | `REPAIR` | `FAIL`)

5. `set_repair_attempted`
   - Outputs:
     - `repair_attempted: true`

## Worker/launcher integration (old DB + queue)

For prototyping, v2 includes a simple single-worker path that reuses the existing
`paper_queue` schema from `arxiv_crawler`, but uses **v2-native hooks**.

- `paper_analysis_worker.yml`
  - uses hook module `research_paper_analysis_v2.distributed_hooks.DistributedPaperAnalysisHooks`
  - claims from existing `paper_queue`, prepares paper, runs `./machine.yml`, writes report
  - no dependency on v1 runtime modules
- `single_worker_launcher.yml`
  - launches exactly one `paper_analysis_worker.yml` and exits

This keeps scheduling minimal in-repo: a lean batch scheduler pass (`batch_scheduler.yml`) that only checks pool state and spawns workers.

## Files
- `profiles.yml`: model profiles
- `machine.yml`: v2 orchestration
- `paper_analysis_worker.yml`: one worker against old queue/db
- `single_worker_launcher.yml`: fire-and-forget one worker
- `batch_scheduler.yml`: one scheduler pass (pool check + spawn)
- `key_outcome_writer.yml`: key outcome section
- `why_hypothesis_writer.yml`: why-it-matters hypothesis ledger
- `reproduction_writer.yml`: reproduction notes
- `limits_confidence_writer.yml`: limits/confidence/next checks
- `report_assembler.yml`: markdown body assembly
- `completeness_judge.yml`: shared completeness-only judge
- `targeted_repair.yml`: single repair pass
- `terminology_map.yml`: starter canonicalization map
