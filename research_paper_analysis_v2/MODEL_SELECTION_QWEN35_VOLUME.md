# Model Selection for 130k-Paper Reprocessing

## Decision

Use **Qwen 3.6 35B MoE AWQ** as the primary production model for reprocessing the full research-paper corpus, with **thinking enabled** and a large token budget.

This is a **volume production** decision, not a claim that Qwen35 MoE is the highest-quality model on every paper. The strongest observed quality baseline remains Qwen 3.6 27B dense AWQ. Qwen35 MoE is the better operational choice for 130k papers because it combines good Qwen-family extraction behavior with endpoint control, scale testing, and acceptable quality at volume.

Recommended profile shape:

```yaml
reasoning:
  provider: litellm
  name: qwen3.6-35b-a3b-awq-coding-budget-160k
  base_url: http://194.26.196.169:15377/v1
  timeout: 300
  max_tokens: 30000
  extra_body:
    enable_thinking: true
```

Important runtime note from this session: the custom Qwen endpoint required LiteLLM async to use the httpx path rather than aiohttp:

```python
litellm.disable_aiohttp_transport = True
litellm.use_aiohttp_transport = False
```

## High-level ranking from this evaluation work

For this paper-digest workload:

```text
Quality champion:        Qwen 3.6 27B dense AWQ
Best volume candidate:   Qwen 3.6 35B MoE AWQ, thinking enabled, 30k max_tokens
Best mechanism prose:    DeepSeek v4 Flash
Clean but shallow:       GPT-5.4-mini
Not production-suitable: Gemma 26B / Gemma 31B free variants
```

A compact relative ordering from the 10-paper comparison set:

```text
Qwen27 dense baseline > Qwen35 MoE thinking > Qwen35 MoE non-thinking > GPT-5.4-mini > Gemma variants
```

DeepSeek v4 Flash does not fit cleanly into that linear ranking. It sometimes beats Qwen27 on mechanism-heavy papers, but it is less consistently fact-calibrated on metric/deployment-heavy papers. Treat it as a strong auditor/alternate synthesis model, not the safest primary production model.

## Evidence reviewed from session history

The session history under `~/.pi/agent/sessions/--Users-trentrobbins-code-research_crawler-research_paper_analysis_v2--/2026-05-06T04-59-06-564Z_019dfba7-6bc4-7046-a7c8-f9bd380cb491.jsonl` records the evaluation path:

1. Built a 10-paper Qwen27/VastAI baseline and copied outputs to `qwen3.6-27b-awq-vastai/`.
2. Upgraded the project to strict FlatMachines/FlatAgents v4.
3. Re-ran the same 10 papers across GPT-5.4-mini, Gemma 26B, Gemma 31B, Qwen35 MoE non-thinking, Qwen35 MoE thinking, and DeepSeek v4 Flash.
4. Compared outputs against source text and prior digests for grounding, metric capture, open questions, hallucination type, and research usefulness.
5. Saturation-tested Qwen35 MoE at larger scale and fixed PDF ingest bottlenecks that initially caused arXiv 429 prep failures.

Observed model run outcomes:

| Model / profile | Completion result | Output folder / DB evidence | Main readout |
|---|---:|---|---|
| Qwen 3.6 27B dense AWQ / VastAI | 10/10 baseline | `qwen3.6-27b-awq-vastai/` | Best overall factual/technical baseline. |
| GPT-5.4-mini / Codex OAuth | 10/10 | `gpt-5.4-mini-codex-oauth/`, `data/v2_executions_gpt54mini_first_smoke.sqlite` | Clean prose, under-extracts technical details and open questions. |
| Gemma 4 26B A4B IT free | 10/10 | `gemma-4-26b-a4b-it-free/` | Systematic open-question failure; not primary-quality. |
| Gemma 4 31B IT free | 7/10 | `gemma-4-31b-it-free/` | Less reliable; 3 quality-gate failures. |
| Qwen 3.6 35B MoE AWQ non-thinking | 10/10 | `qwen3.6-35b-a3b-awq-coding-budget-160k/`, `data/v2_executions_qwen35b_single_smoke.sqlite` | Good, but below Qwen27 and below thinking mode. |
| Qwen 3.6 35B MoE AWQ thinking-30k | 10/10 | `qwen3.6-35b-a3b-awq-coding-budget-160k-thinking-30k/`, `data/v2_executions_qwen35b_reasoning_10w.sqlite` | Best Qwen35 configuration; production candidate. |
| Qwen35 thinking saturation run | 200/200 done after ingest fix | `data/v2_executions_qwen35b_reasoning_50w10.sqlite` | Endpoint scaled; failures were PDF-prep/arXiv 429, not model failures. |
| DeepSeek v4 Flash / OpenRouter | 10/10 | `deepseek-v4-flash-openrouter/`, `data/v2_executions_deepseek_v4_flash_first.sqlite` | Very strong mechanism prose; sometimes best, but more factual drift risk. |

## Why Qwen35 MoE is the right primary volume model

The 130k-paper job needs a model that is:

- long-context capable,
- stable under concurrent workers,
- technically competent on dense research papers,
- controllable through a dedicated endpoint,
- cheaper/faster/more scalable than using the highest-quality dense baseline everywhere,
- less shallow than GPT/Gemma-style alternatives.

Qwen35 MoE fits that profile. In the comparison set it retained much of the Qwen-family extraction discipline and beat GPT/Gemma on technical usefulness. The thinking-enabled configuration was better than non-thinking, especially for mechanism extraction, though it did **not** automatically produce longer reports; the report assembler and prompts still control output length.

Recommended production stance:

```text
Use Qwen35 MoE thinking-30k for bulk.
Use sentinels and sampled audits to catch known failure modes.
Escalate flagged/high-value papers to Qwen27 dense and/or DeepSeek.
```

## Why Qwen27 dense is still the quality baseline

Qwen27 dense AWQ “crushed” this benchmark because the task is almost tailor-made for it:

- faithful long-context extraction,
- exact metric retention,
- structured technical synthesis,
- conservative grounding,
- good future-work/open-question extraction,
- strong fit to the digest schema.

It was not necessarily more creative or more generally intelligent than every other model. It was better calibrated for this exact workload: **technical long-context extraction + disciplined report assembly + metric preservation**.

Use Qwen27 dense as:

- the gold-standard auditor,
- a regression target for prompt/model changes,
- the reprocessor for the highest-value or sentinel-flagged papers,
- the model to sample against when measuring Qwen35 production drift.

Do not discard Qwen27 results: they are the best current reference standard.

## Tradeoffs against each reviewed model

### Qwen 3.6 27B dense AWQ baseline

**Best for:** final quality, factual discipline, metric retention, faithful open questions.

Advantages over Qwen35:

- better technical completeness,
- fewer generic/false limitations,
- stronger exact metric preservation,
- better source-grounded open questions,
- best overall trust profile in the 10-paper set.

Disadvantages for 130k volume:

- likely less favorable throughput/cost/scaling profile,
- not the endpoint currently being saturation-tested for bulk,
- using it everywhere may waste the strongest model on low-value papers.

Recommended role:

```text
Qwen27 = quality oracle / audit model / high-value reprocessor
Qwen35 = production-volume model
```

### Qwen 3.6 35B MoE AWQ, non-thinking

**Best for:** cheaper/faster Qwen-family baseline when reasoning budget is constrained.

Observed strengths:

- completed 10/10,
- stronger than GPT/Gemma on most technical extraction,
- good operational behavior once LiteLLM transport was fixed.

Observed weaknesses:

- below thinking mode on technical extraction,
- more generic limitations,
- weak open-question behavior relative to Qwen27,
- one notable miss: false claim that code was absent for a paper with a GitHub URL.

Recommended role:

```text
Use only if thinking mode throughput/cost is unacceptable.
Otherwise prefer thinking-30k.
```

### Qwen 3.6 35B MoE AWQ, thinking enabled, 30k max_tokens

**Best for:** production-volume digest generation.

Observed strengths:

- completed the 10-paper comparison set,
- better than non-thinking on roughly half the comparison papers,
- strongest Qwen35 configuration observed,
- successfully ran a 200-paper saturation workload after ingest fixes,
- retains enough Qwen-family extraction discipline to be viable for 130k papers.

Observed weaknesses:

- still below Qwen27 dense,
- open questions remain weaker than ideal,
- can generate plausible but generic limitations,
- thinking does not guarantee longer/more complete final reports because assembler/prompt structure caps style and length.

Recommended role:

```text
Primary production model for 130k-paper reprocessing.
```

### DeepSeek v4 Flash via OpenRouter

**Best for:** mechanism-rich synthesis, causal explanations, alternate evaluation, “world memory”-like state reconstruction.

DeepSeek was excellent on several papers. It was the best report for `2601.02105` and very strong on SAIR and HyperPotter.

Observed DeepSeek scores relative to the best previous report for each paper:

| Paper | DeepSeek score | Verdict |
|---|---:|---|
| `2601.02105` LION-DG | 5 | Best; beat Qwen27 on this paper. |
| `2601.08181` TabPFN interpretability | 3 | Good, but worse than Qwen27 on layer-specific details. |
| `2601.17472` A²DCDR | 3 | Deep mechanism summary but factual miss on user count. |
| `2601.22397` SAIR | 5 | Excellent; tied/near-tied with Qwen27. |
| `2602.00053` FastAPI/Triton | 4 | Strong limitations/open questions; slight metric phrasing issue. |
| `2602.05670` HyperPotter | 5 | Best mechanism/usefulness report. |
| `2602.09972` Hydra-Nav | 3 | Good, but missed Unitree Go2 / exact deployment details. |
| `2602.14404` Boule/Baguette | 4 | Near Qwen27; strong theory capture. |
| `2603.17189` gripper design | 4 | Near Qwen27; slightly weaker speed-detail phrasing. |
| `2603.28644` music GP features | 3 | Decent but missed several secondary metric distinctions. |

Average DeepSeek relative score: approximately **3.9/5**.

DeepSeek strengths versus Qwen35:

- richer explanations,
- stronger mechanism prose,
- strong open-question generation,
- useful for causal/world-state style reconstruction.

DeepSeek weaknesses versus Qwen/Qwen27:

- more likely to over-specify reproduction details,
- occasional factual drift in metrics or deployment facts,
- may write a very convincing mechanism while missing concrete table/deployment facts,
- OpenRouter dependency adds rate/auth/provider variability.

Recommended role:

```text
DeepSeek = mechanism-rich auditor / alternate synthesis / spot-check model
Not primary 130k production model unless factual sentinels become very strong.
```

### GPT-5.4-mini via Codex OAuth

**Best for:** clean readable summaries and conservative prose.

Observed strengths:

- 10/10 completion,
- clean and concise reports,
- good surface readability,
- less prone to messy verbosity.

Observed weaknesses:

- under-extracted technical mechanisms,
- repeatedly weak `Open Questions`, including empty/`None`-style failures when source contained future work,
- not trusted as the primary technical digest model after comparison.

Recommended role:

```text
GPT = readability comparator / conservative baseline
Not primary archival digest model.
```

### Gemma 4 26B A4B IT free

**Best for:** cheap smoke testing only.

Observed strengths:

- 10/10 completion,
- concise/readable outputs,
- useful as a low-cost route test.

Observed weaknesses:

- systematic open-question failure: `Open Questions: None` or equivalent on 10/10,
- insufficient technical depth,
- weaker research usefulness than Qwen/GPT/DeepSeek.

Recommended role:

```text
Gemma26 = smoke/cost baseline only
Not production-suitable for research digests.
```

### Gemma 4 31B IT free

**Best for:** no production role in this pipeline.

Observed result:

```text
done   | 7
failed | 3
```

Failure pattern:

```text
first judge:  REPAIR
after repair: REPAIR
repair_attempted = true -> wrap_failed
```

Observed weaknesses:

- unreliable completion,
- shallow/generic limitations,
- worse operational profile than Gemma26 despite sometimes better passed reports.

Recommended role:

```text
Gemma31 = discard as production candidate.
```

### OpenRouter free / exploratory models

OpenRouter models were useful for exploration but are not the production path for 130k papers.

Observed issues:

- environment/auth fragility if `OPENROUTER_API_KEY` is not explicitly loaded into `uv run`,
- provider/rate-limit variability,
- less predictable throughput,
- quality not consistently above Qwen-family endpoints.

Working invocation pattern when needed:

```bash
set -a; source ~/.envrc; set +a; V2_EXECUTIONS_DB_PATH=<db> ./run.sh --workers <n> --daemon
```

Recommended role:

```text
OpenRouter = exploratory comparisons and audits, not primary production bulk.
```

## Known Qwen35 failure modes to guard against

1. **Generic or false limitations**
   - Example class: vague claims like “limited dataset diversity” without direct source support.

2. **Source-code availability errors**
   - One Qwen35 report falsely implied code was absent even though source text contained a GitHub URL.

3. **Weak open questions**
   - Qwen35 thinking improved technical extraction but still did not consistently match Qwen27 on future-work/open-question extraction.

4. **Metric under-specification**
   - Usually better than GPT/Gemma, but can omit secondary metrics or concrete deployment details.

5. **Report-length false confidence**
   - Thinking mode can improve reasoning without increasing final report length. Do not use character count alone as a quality proxy.

## Production safeguards for Qwen35 MoE

Use Qwen35 MoE with automated sentinels and sampled audit, not blind acceptance.

Recommended checks:

1. **Open Questions sentinel**
   - Flag empty, `None`, single-line, or generic future-work sections.

2. **Metric extraction sentinel**
   - Extract percentages, latencies, AUC/accuracy/EER, dataset counts, user counts, parameter counts, and benchmark names from source/report.
   - Flag missing headline metrics or suspicious mismatches.

3. **Code URL sentinel**
   - Detect GitHub/GitLab/Code links in source text.
   - Flag reports claiming code is unavailable when source contains a repository URL.

4. **Generic limitation sentinel**
   - Flag limitations that are not anchored to source evidence.

5. **Deployment/system-detail sentinel**
   - For systems papers, specifically check P99/p95 latency, throughput, hardware, user counts, duration, memory overhead, and production constraints.

6. **Section completeness and length checks**
   - Ensure expected digest sections are present.
   - Flag unusually short reports for long/source-rich papers, but do not treat length as sufficient evidence of quality.

7. **Random sampled audit**
   - Audit a fixed percentage with Qwen27 and/or DeepSeek.
   - Oversample papers with many tables, metrics, code claims, or deployment claims.

8. **High-value escalation**
   - Reprocess papers with code availability, benchmark leaderboard claims, high novelty, or downstream importance using Qwen27 or DeepSeek.

## Ingest / scale requirements discovered during saturation testing

The 200-paper Qwen35 saturation run initially had prep/download failures, not model failures. Root cause was the GCS/Kaggle path being silently bypassed because the hardcoded `gsutil` path was missing, causing fallback to `export.arxiv.org` and 429s.

Production requirements:

- GCS/Kaggle PDF fetch must be attempted before arXiv export.
- Missing `gsutil`/GCP command must fail loudly, not silently fall back.
- Default should discover `gsutil` from `PATH`.
- `run.sh` may include a commented explicit override:

```bash
# GCS/Kaggle PDF fetch uses `gsutil` discovered from PATH by default.
# Uncomment to pin an explicit command path if PATH discovery breaks:
# export RPA_V2_GSUTIL_BIN="/Users/trentrobbins/Downloads/google-cloud-sdk/bin/gsutil"
```

Modern GCS PDF prefixes used:

```text
gs://arxiv-dataset/arxiv/pdf
gs://arxiv-dataset/arxiv/arxiv/pdf
```

For 130k papers, the model decision is only safe if ingestion stays GCS-first and arXiv export is not hammered.

## Suggested production policy

Primary path:

```text
GCS/Kaggle-first ingest -> Qwen35 MoE thinking-30k -> QA sentinels -> accepted digest
```

Escalation path:

```text
flagged digest -> Qwen27 dense and/or DeepSeek v4 Flash re-eval -> human/sample review
```

Model roles:

```text
Qwen35 MoE thinking-30k: primary production model
Qwen27 dense AWQ: gold-standard auditor / high-value reprocessor
DeepSeek v4 Flash: mechanism-rich auditor / alternate synthesis
GPT-5.4-mini: readability/conservatism comparator
Gemma variants: no production role
```

Worker policy:

- Use one worker process when changing worker counts.
- For long saturation runs, run workers in the background so interactive work can continue.
- Scale concurrency after verifying ingest path and endpoint queue behavior.

## Token cost analysis: Qwen35 thinking vs non-thinking

### Source of truth used

The SQLite execution databases do **not** persist provider token usage in ordinary tables. They have `daily_usage`, but that tracks calls, not tokens. The machine checkpoints also do not reliably persist `AgentResult.usage` into context.

However, the run logs contain OpenTelemetry JSON metric exports from FlatAgents. These include cumulative counters:

```text
flatagents.agent.input_tokens
flatagents.agent.output_tokens
```

Important parsing detail: these are cumulative monotonic counters, so the correct aggregation is **max/last per `(metric, model, agent, status)` series**, not summing every export line.

Relevant log files:

| Run | Logs used |
|---|---|
| Qwen35 non-thinking 10-paper run | `logs/run_20260508_151905.log` + `logs/run_20260508_153032.log` |
| Qwen35 thinking-30k 10-paper run | `logs/run_20260508_160605.log` |
| Qwen27 dense baseline | `data/logs/qwen10_run.log` |

### Actual Qwen35 10-paper token metrics from log exports

| Mode | Input tokens | Output tokens | Total tokens | Avg total / paper |
|---|---:|---:|---:|---:|
| Qwen35 non-thinking | 200,998 | 24,242 | 225,240 | 22,524 |
| Qwen35 thinking-30k | 191,690 | 112,947 | 304,637 | 30,464 |
| Delta, thinking - non-thinking | -9,308 | +88,705 | +79,397 | +7,940 |
| Percent delta | -4.6% | +366.0% | +35.3% | +35.3% |

This is the key correction: **thinking mode was about 35% more tokens overall in the actual Qwen35 metric exports**, despite producing slightly shorter final visible markdown reports. The extra cost shows up as output/completion tokens, which likely include hidden or reasoning-token generation.

Projected directly to 130,000 papers at the observed historical 10-paper rate:

| Mode | Projected input tokens | Projected output tokens | Projected total tokens |
|---|---:|---:|---:|
| Qwen35 non-thinking | 2.61B | 315M | 2.93B |
| Qwen35 thinking-30k | 2.49B | 1.47B | 3.96B |
| Thinking premium | -121M | +1.15B | +1.03B |

So, if the historical call shape were representative, thinking mode costs roughly:

```text
+1.03B tokens over 130k papers
+35% total token volume
~4.7x output/completion token volume
```

### Agent-level Qwen35 breakdown

Qwen35 non-thinking, 10-paper run:

| Agent | Input | Output | Total |
|---|---:|---:|---:|
| key-outcome-writer | 155,563 | 1,893 | 157,456 |
| completeness-judge | 23,548 | 29 | 23,577 |
| report-assembler | 9,987 | 14,668 | 24,655 |
| limits-confidence-writer | 6,659 | 2,724 | 9,383 |
| targeted-repair | 5,241 | 4,928 | 10,169 |
| **Total** | **200,998** | **24,242** | **225,240** |

Qwen35 thinking-30k, 10-paper run:

| Agent | Input | Output | Total |
|---|---:|---:|---:|
| key-outcome-writer | 155,543 | 26,336 | 181,879 |
| completeness-judge | 18,233 | 9,138 | 27,371 |
| report-assembler | 9,661 | 48,565 | 58,226 |
| limits-confidence-writer | 6,806 | 26,055 | 32,861 |
| targeted-repair | 1,447 | 2,853 | 4,300 |
| **Total** | **191,690** | **112,947** | **304,637** |

The main cost shift is not input. The main cost shift is output/completion:

```text
key-outcome output:       1,893 -> 26,336
limits-confidence output: 2,724 -> 26,055
report-assembler output: 14,668 -> 48,565
judge output:                29 -> 9,138
```

That is consistent with hidden reasoning being counted as completion/output tokens by the endpoint or metrics layer.

### Important caveat: the Qwen35 historical runs had a prompt-path failure

The Qwen35 non-thinking and thinking DBs both show `expensive-pipeline.parallel_raw` errors for the why/reproduction/open-question submachines:

```text
Referenced config file not found:
config/agents/./prompts/why_hypothesis_writer.prompt.yml
```

That means these token counts are **not full intended production-pipeline costs**. They are still a valid comparison of thinking vs non-thinking under the same flawed call shape, but they exclude the expensive writer calls that should exist in the fixed pipeline:

- `why_hypothesis_writer`
- `reproduction_writer`
- `open_questions_writer`

Because those missing calls are content-generating full-paper calls, a fixed production run will cost more than both historical Qwen35 figures. Thinking mode may also add hidden/output tokens to those missing writer calls.

### Qwen27 metric export for context

The Qwen27 baseline log metric export shows:

| Model | Input tokens | Output tokens | Total tokens | Avg total / paper |
|---|---:|---:|---:|---:|
| Qwen27 dense baseline | 105,176 | 31,622 | 136,798 | 13,680 |

Agent-level Qwen27 metrics:

| Agent | Input | Output | Total |
|---|---:|---:|---:|
| key-outcome-writer | 21,222 | 2,192 | 23,414 |
| why-hypothesis-writer | 23,218 | 5,062 | 28,280 |
| reproduction-writer | 21,537 | 3,398 | 24,935 |
| open-questions-writer | 21,456 | 2,733 | 24,189 |
| limits-confidence-writer | 5,344 | 5,263 | 10,607 |
| report-assembler | 6,945 | 11,217 | 18,162 |
| completeness-judge | 5,454 | 1,757 | 7,211 |
| **Total** | **105,176** | **31,622** | **136,798** |

Caveat: the Qwen27 run was from the earlier pipeline shape and is not apples-to-apples with the later strict-v4 Qwen35 runs. It is useful as a historical quality/cost reference, but not as the direct production-cost estimator for the current fixed v4 pipeline.

### Visible report tokens vs provider output tokens

Approximate visible final markdown token counts over the same 10-paper folders were:

| Folder / model | Avg visible final report tokens/paper |
|---|---:|
| Qwen27 dense baseline | 2,548 |
| DeepSeek v4 Flash, fixed prompt path | 2,999 |
| Qwen35 non-thinking historical run | 1,682 |
| Qwen35 thinking historical run | 1,514 |

This explains the confusion: the thinking reports were visibly shorter, but the provider metric output tokens were much higher. For cost, use provider/log metrics, not final markdown length.

### Practical conclusion on thinking cost

For the selected Qwen35 model, based on actual log metric exports:

```text
Thinking mode quality:  better than non-thinking
Thinking mode cost:     about +35% total tokens in the historical 10-paper run
Thinking mode output:   about +366% output/completion tokens
```

Recommended production policy:

1. Keep **thinking enabled** for archival-quality bulk if quality matters more than raw throughput.
2. Before 130k production, run a small fixed-prompt-path calibration batch and capture actual token metrics with all writer agents working.
3. If the +35% premium holds and endpoint throughput is acceptable, use thinking for all papers.
4. If throughput or budget is tight, use a tiered policy:
   - non-thinking for low-priority papers,
   - thinking for papers with code, benchmark claims, many metrics, high novelty, sentinel flags, or high downstream value.
5. The biggest structural cost optimization is still reducing repeated full-paper passes, but thinking-vs-non-thinking is now known to be a real completion-token cost lever.

## Final recommendation

Proceed with **Qwen 3.6 35B MoE AWQ, thinking enabled, `max_tokens: 30000`** for the 130k-paper reprocessing job, but add usage instrumentation before the full run.

Keep Qwen27 dense as the quality oracle and DeepSeek v4 Flash as a mechanism-rich auditor. The right architecture is not “pick one model and trust it”; it is:

```text
Qwen35 for volume + sentinels + sampled Qwen27/DeepSeek audits.
```

That gives the best current balance of throughput, endpoint control, technical digest quality, and recoverability from known model failure modes.
