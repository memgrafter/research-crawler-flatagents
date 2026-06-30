# Qwen10 report/source pairs and QA

Rubric: `/Users/trentrobbins/code/analysis/GPT53_CODEX_HIGH_QUALITY_CHECKS_RUBRIC.md`

Grounding reports were searched via targeted prefix globs under `/Users/trentrobbins/code/analysis/ml_research_analysis_*` (no directory-wide `ls`). Seven of the ten Qwen papers have prior grounding reports in `ml_research_analysis_2026`; three did not have same-ID grounding reports there.

## File pairs

| # | arXiv ID | Current Qwen report | Grounding report from previous run | Source `.txt` |
|---:|---|---|---|---|
| 1 | 2601.02105 | `data/2601.02105_lion-dg-layer-informed-initialization-with-deep-gradient-protocols-for-accelerated-neural-network-training_20260505_223928.md` | `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.02105_lion-dg-layer-informed-initialization-with-deep-gradient-protocols-for-accelerated-neural-network-training_20260210_094345.md` | `data/2601.02105.txt` |
| 2 | 2601.08181 | `data/2601.08181_tabpfn-through-the-looking-glass-an-interpretability-study-of-tabpfn-and-its-internal-representations_20260505_224026.md` | `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.08181_tabpfn-through-the-looking-glass-an-interpretability-study-of-tabpfn-and-its-internal-representations_20260210_003056.md` | `data/2601.08181.txt` |
| 3 | 2601.17472 | `data/2601.17472_adversarial-alignment-and-disentanglement-for-cross-domain-ctr-prediction-with-domain-encompassing-features_20260505_222915.md` | `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.17472_adversarial-alignment-and-disentanglement-for-cross-domain-ctr-prediction-with-domain-encompassing-features_20260401_064553.md` | `data/2601.17472.txt` |
| 4 | 2601.22397 | `data/2601.22397_sair-cost-efficient-multi-stage-ml-pipeline-autoscaling-via-in-context-reinforcement-learning_20260505_224000.md` | `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.22397_sair-cost-efficient-multi-stage-ml-pipeline-autoscaling-via-in-context-reinforcement-learning_20260210_092002.md` | `data/2601.22397.txt` |
| 5 | 2602.00053 | `data/2602.00053_scalable-and-secure-ai-inference-in-healthcare-a-comparative-benchmarking-of-fastapi-and-triton-inference-server-on-kubernetes_20260505_224019.md` | `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2602.00053_scalable-and-secure-ai-inference-in-healthcare-a-comparative-benchmarking-of-fastapi-and-triton-inference-server-on-kubernetes_20260405_112931.md` | `data/2602.00053.txt` |
| 6 | 2602.05670 | `data/2602.05670_hyperpotter-spell-the-charm-of-high-order-interactions-in-audio-deepfake-detection_20260505_222943.md` | not found | `data/2602.05670.txt` |
| 7 | 2602.09972 | `data/2602.09972_hydra-nav-object-navigation-via-adaptive-dual-process-reasoning_20260505_224630.md` | not found | `data/2602.09972.txt` |
| 8 | 2602.14404 | `data/2602.14404_boule-or-baguette-a-study-on-task-topology-length-generalization-and-the-benefit-of-reasoning-traces_20260505_224014.md` | `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2602.14404_boule-or-baguette-a-study-on-task-topology-length-generalization-and-the-benefit-of-reasoning-traces_20260331_221240.md` | `data/2602.14404.txt` |
| 9 | 2603.17189 | `data/2603.17189_influence-of-gripper-design-on-human-demonstration-quality-for-robot-learning_20260505_224700.md` | not found | `data/2603.17189.txt` |
| 10 | 2603.28644 | `data/2603.28644_constructing-composite-features-for-interpretable-music-tagging_20260505_224104.md` | `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2603.28644_constructing-composite-features-for-interpretable-music-tagging_20260403_093408.md` | `data/2603.28644.txt` |

---

## QA Record 1

### QA Record
- paper_id: 2601.02105
- report_path: `data/2601.02105_lion-dg-layer-informed-initialization-with-deep-gradient-protocols-for-accelerated-neural-network-training_20260505_223928.md`
- source_path: `data/2601.02105.txt`
- evaluator_model: manual rubric application by coding assistant
- grounding_report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.02105_lion-dg-layer-informed-initialization-with-deep-gradient-protocols-for-accelerated-neural-network-training_20260210_094345.md`
- comparison_model: prior `ml_research_analysis_2026` report
- timestamp: 2026-05-06

### Score
- Paper Match Sanity Check (5): 5
- Core Factual Accuracy (30): 27
- Quantitative Claims Sanity (15): 14
- Claim Discipline / Overreach (20): 17
- Metadata & Formatting Hygiene (5): 5
- Unsupported/Fabricated Content (15): 13
- Evidence Anchoring (10): 10
- **Total (100): 91**
- Verdict: PASS-High

### Findings
1. [Severity: S3] Paper identity and metadata are clean.
   - Report evidence: Frontmatter and Quick Facts identify title `LION-DG: Layer-Informed Initialization with Deep Gradient Protocols for Accelerated Neural Network Training`, arXiv ID `2601.02105`, and source URL `https://arxiv.org/abs/2601.02105`.
   - Source evidence (quote/line anchor): Source title appears at `data/2601.02105.txt:2`; arXiv version line appears at `data/2601.02105.txt:80`.
   - Impact: Pairing is clearly correct and metadata is usable.
   - Fix guidance: None.

2. [Severity: S3] Core summary is mostly faithful to the source.
   - Report evidence: Executive Summary says LION-DG zero-initializes auxiliary heads, uses He initialization for the backbone, creates gradient awakening / implicit warmup, and accelerates convergence without final-accuracy sacrifice in the main reported settings.
   - Source evidence (quote/line anchor): The abstract states LION-DG "zero-initializes auxiliary classifier heads while applying standard He-initialization to the backbone" and that auxiliary gradients are "exactly zero at initialization" before phasing in naturally (`data/2601.02105.txt:13-20`). Contributions list Proposition 1, Proposition 2, DenseNet-DS +8.3%, and Hybrid 81.92% (`data/2601.02105.txt:60-72`).
   - Impact: Main story is reliable.
   - Fix guidance: None required for core summary.

3. [Severity: S2] Quantitative claims are accurate but slightly compressed across settings.
   - Report evidence: Key Results says "Achieves an 8.3% convergence speedup on DenseNet-DS and an 11.3% speedup on ResNet-DS with side-tap auxiliaries on CIFAR benchmarks" and "Maintains final validation accuracy parity with standard initialization, peaking at 81.92% on CIFAR-10 when combined with LSUV backbone calibration."
   - Source evidence (quote/line anchor): Table 1 gives CIFAR-10 DenseNet-DS LION-DG 80.59 vs He-init 81.11 with +8.3, Hybrid 81.92 with +8.0, and CIFAR-100 ResNet-DS LION-DG 64.96 vs 64.87 with +11.3 (`data/2601.02105.txt:418-442`). The text also states CIFAR-100 ResNet benefits with +11.3 while matching baseline accuracy (`data/2601.02105.txt:407-409`).
   - Impact: Numbers are supported, but the report's phrase "on ResNet-DS with side-tap auxiliaries on CIFAR benchmarks" could obscure that +11.3 is specifically CIFAR-100 ResNet-DS, while CIFAR-10 ResNet-DS is only +3.6 with an accuracy drop.
   - Fix guidance: State explicitly: DenseNet-DS CIFAR-10 +8.3%; ResNet-DS side-tap CIFAR-100 +11.3%; ResNet-DS CIFAR-10 +3.6 with lower validation accuracy.

4. [Severity: S2] Mild overreach in final validation accuracy parity wording.
   - Report evidence: Executive Summary says LION-DG "accelerates convergence significantly without sacrificing final validation accuracy"; Key Results says it "Maintains final validation accuracy parity with standard initialization."
   - Source evidence (quote/line anchor): Source says LION-DG maintains comparable accuracy in key settings, but Table 1 shows CIFAR-10 ResNet-DS LION-DG 87.18 vs He-init 89.42 and CIFAR-100 DenseNet-DS LION-DG 49.93 vs 50.72 (`data/2601.02105.txt:423-442`). Source itself cautions ResNet-DS gains are modest/dataset-dependent and include "some accuracy trade-off" (`data/2601.02105.txt:579-582`).
   - Impact: Not core-breaking, but the report should calibrate this claim to avoid implying parity across all architectures/datasets.
   - Fix guidance: Replace with: "maintains comparable accuracy in the main DenseNet-DS and CIFAR-100 ResNet-DS settings, though CIFAR-10 ResNet-DS shows an accuracy trade-off."

5. [Severity: S2] One architecture/onboarding detail appears unsupported or imprecise.
   - Report evidence: Architecture Onboarding lists auxiliary classifier heads as "GAVP + linear projection."
   - Source evidence (quote/line anchor): The source initialization algorithm and gradient derivation specify auxiliary weights and affine/linear heads (`data/2601.02105.txt:196-209`, `data/2601.02105.txt:221-259`), but I did not find support for the exact acronym/string "GAVP" in the extracted source.
   - Impact: Peripheral implementation-detail risk; it does not change the main contribution but may mislead someone implementing the architecture.
   - Fix guidance: Replace `GAVP + linear projection` with the source-supported phrasing: `auxiliary classifier heads / linear auxiliary projections` unless the exact pooling layer is verified elsewhere in the PDF.

6. [Severity: S3] Open questions are well grounded in source future-work statements.
   - Report evidence: Open Questions cover ImageNet scaling, dense prediction tasks, interactions with LR schedules/regularization, and additive residual path initialization.
   - Source evidence (quote/line anchor): The source future-work paragraph names ImageNet/large-scale benchmarks, semantic segmentation, object detection, and interactions with learning-rate schedules/regularization (`data/2601.02105.txt:583-594`).
   - Impact: Good evidence anchoring for unresolved issues.
   - Fix guidance: None.

### Grounding report comparison
- Prior grounding report: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.02105_lion-dg-layer-informed-initialization-with-deep-gradient-protocols-for-accelerated-neural-network-training_20260210_094345.md`
- Agreement: Both reports identify the same core contribution: zero-initialized auxiliary heads + He-initialized backbone create gradient decoupling / implicit warmup. Both capture +8.3% DenseNet-DS CIFAR-10 speedup, Hybrid 81.92%, architecture-dependent ResNet behavior, and the CIFAR-100 DenseNet-DS no-speedup caveat.
- Qwen report stronger: More detailed mechanism writeup, richer implementation/onboarding section, and more complete future-work list including learning-rate schedules/regularization.
- Grounding report stronger: Better calibrated quantitative caveats. It explicitly states ResNet-DS CIFAR-10 is +3.6% and that CIFAR-100 DenseNet-DS shows no speedup; it also flags dataset/architecture-dependent benefits more prominently.
- Net: Qwen is more expansive and useful for onboarding; grounding report is slightly cleaner on claim calibration. Source check supports using Qwen, with the calibration edits noted above.

### Final Judgment
- The report is reliable and synthesis-usable. It captures the paper's main method, mechanism, theoretical claims, and headline quantitative findings with good evidence support.
- Main residual risk is claim calibration around accuracy parity: both the source and the grounding report are more nuanced for ResNet-DS and non-main settings than the Qwen report's broad wording suggests.
- Whether this sample should be moved to `bad/`: No

---

## Grounding Report QA Record 1

### QA Record
- paper_id: 2601.02105
- report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.02105_lion-dg-layer-informed-initialization-with-deep-gradient-protocols-for-accelerated-neural-network-training_20260210_094345.md`
- source_path: `data/2601.02105.txt`
- evaluator_model: manual rubric application by coding assistant
- comparison_model: Qwen current report (`data/2601.02105_lion-dg-layer-informed-initialization-with-deep-gradient-protocols-for-accelerated-neural-network-training_20260505_223928.md`)
- timestamp: 2026-05-06

### Score
- Paper Match Sanity Check (5): 5
- Core Factual Accuracy (30): 28
- Quantitative Claims Sanity (15): 15
- Claim Discipline / Overreach (20): 18
- Metadata & Formatting Hygiene (5): 4
- Unsupported/Fabricated Content (15): 14
- Evidence Anchoring (10): 10
- **Total (100): 94**
- Verdict: PASS-High

### Findings
1. [Severity: S3] Paper identity is correct; metadata is mostly clean, but tags are noisy.
   - Report evidence: Frontmatter identifies title `LION-DG: Layer-Informed Initialization with Deep Gradient Protocols for Accelerated Neural Network Training`, arXiv ID `2601.02105`, and source URL `https://arxiv.org/abs/2601.02105`.
   - Source evidence (quote/line anchor): Source title appears at `data/2601.02105.txt:2`; arXiv version line appears at `data/2601.02105.txt:80`.
   - Impact: Pairing is clearly correct. Metadata required fields are present. Extra tags include unrelated terms such as `retrieval-augmented-generation`, `instruction-tuning`, and `mixture-of-experts`, but the rubric says tags are ignored and metadata is low-weight.
   - Fix guidance: Optional cleanup: remove generic/unrelated tags.

2. [Severity: S3] Core factual summary is accurate and source-grounded.
   - Report evidence: Executive Summary says LION-DG zero-initializes auxiliary classifier weights/biases, uses He initialization for the backbone, creates gradient awakening, and avoids explicit warmup schedules.
   - Source evidence (quote/line anchor): Source states the method "zero-initializes auxiliary classifier heads while applying standard He-initialization to the backbone" and that auxiliary gradients are "exactly zero at initialization" before phasing in naturally (`data/2601.02105.txt:13-20`). The introduction states auxiliary losses produce zero gradients w.r.t. backbone parameters at initialization (`data/2601.02105.txt:50-56`).
   - Impact: The central method and mechanism are represented reliably.
   - Fix guidance: None.

3. [Severity: S3] Quantitative claims are well calibrated and more precise than the Qwen report.
   - Report evidence: Key Results explicitly list DenseNet-DS +8.3% on CIFAR-10, ResNet-DS +3.6% on CIFAR-10 with side-tap design, Hybrid 81.92%, and CIFAR-100 DenseNet-DS no speedup.
   - Source evidence (quote/line anchor): Table 1 gives CIFAR-10 DenseNet-DS He-init 81.11 vs LION-DG 80.59 with +8.3; CIFAR-10 ResNet-DS He-init 89.42 vs LION-DG 87.18 with +3.6; Hybrid CIFAR-10 DenseNet-DS 81.92 with +8.0; CIFAR-100 DenseNet-DS LION-DG 49.93 with no speedup (`data/2601.02105.txt:418-442`).
   - Impact: This report avoids overgeneralizing the speedup and accuracy story. Core numeric interpretation is strong.
   - Fix guidance: None.

4. [Severity: S2] Mild unsupported/open-ended experiment suggestions, but not core-distorting.
   - Report evidence: Architecture Onboarding suggests an ablation on auxiliary weight `α ∈ {0.1, 0.3, 0.5}` to verify reduced α-sensitivity, and mentions "very small models where auxiliary parameters dominate may experience over-regularization."
   - Source evidence (quote/line anchor): Source training setup uses auxiliary weight `α=0.3` (`data/2601.02105.txt:390-396`) and future work mentions interactions with learning-rate schedules/regularization (`data/2601.02105.txt:591-594`), but I did not find direct support for reduced α-sensitivity or small-model over-regularization as stated.
   - Impact: Peripheral onboarding speculation; not a fabricated central result.
   - Fix guidance: Qualify as assumptions/hypotheses or remove unless supported by source text.

5. [Severity: S3] Claim discipline is strong; report clearly flags architecture/dataset dependence.
   - Report evidence: Key Results states CIFAR-100 DenseNet-DS shows no speedup; Limitations state benefits are architecture and dataset dependent; Confidence labels generalization to arbitrary deeply-supervised networks as low.
   - Source evidence (quote/line anchor): Source says gains on ResNet-DS are modest and dataset-dependent (`data/2601.02105.txt:579-586`) and future work is needed for ImageNet / other large-scale benchmarks (`data/2601.02105.txt:583-589`).
   - Impact: The report properly calibrates the method's scope.
   - Fix guidance: None.

6. [Severity: S3] Evidence anchoring is sufficient for all major judgments.
   - Report evidence: Mechanism, numeric results, limitations, and confidence sections map directly to propositions, tables, and future-work statements.
   - Source evidence (quote/line anchor): Proposition 1 chain-rule derivation supports gradient decoupling (`data/2601.02105.txt:221-259`); Proposition 2 supports linear weight growth / gradient awakening (`data/2601.02105.txt:267-323`); future-work limitations appear at `data/2601.02105.txt:583-594`.
   - Impact: Major claims are quickly checkable against source.
   - Fix guidance: None.

### Final Judgment
- The grounding report is highly reliable and slightly stronger than the Qwen report on quantitative and scope calibration.
- It is less expansive than Qwen for onboarding detail, but its core factual accuracy, numeric precision, and claim discipline are stronger.
- Whether this sample should be moved to `bad/`: No

---

## Alternating grounded QA records for remaining 9 papers

Format below follows the requested alternation: grounding report first, then Qwen report for the same paper. For papers where no same-ID grounding report was found under `/Users/trentrobbins/code/analysis/ml_research_analysis_*`, the grounding slot is recorded as `BLOCKED` and the Qwen report is still graded against source.

### 2601.08181 — Grounding report QA
- report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.08181_tabpfn-through-the-looking-glass-an-interpretability-study-of-tabpfn-and-its-internal-representations_20260210_003056.md`
- source_path: `data/2601.08181.txt`
- comparison_model: Qwen current report
- Score: Match 5, Core Facts 27, Quant 14, Claim Discipline 18, Metadata 4, Unsupported 14, Anchoring 10 = **92 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Core story is supported: source says the paper probes TabPFN hidden representations for regression coefficients, intermediate complex-expression values, and final answers (`data/2601.08181.txt:13-27`). Grounding report captures this as mechanistic interpretability of TabPFN internal representations.
  2. [S3] Claim calibration is good: source frames results as evidence of meaningful structured information, not full mechanistic proof (`data/2601.08181.txt:22-34`). Grounding report generally preserves that cautious interpretation.
  3. [S2] Minor metadata/tag noise possible, as in other old reports, but required fields are present and usable.
- Final judgment: Reliable grounding report; useful as calibration baseline for Qwen. Move to `bad/`: No.

### 2601.08181 — Qwen report QA
- report_path: `data/2601.08181_tabpfn-through-the-looking-glass-an-interpretability-study-of-tabpfn-and-its-internal-representations_20260505_224026.md`
- source_path: `data/2601.08181.txt`
- grounding_report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.08181_tabpfn-through-the-looking-glass-an-interpretability-study-of-tabpfn-and-its-internal-representations_20260210_003056.md`
- Score: Match 5, Core Facts 27, Quant 14, Claim Discipline 17, Metadata 5, Unsupported 13, Anchoring 10 = **91 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Matches source and grounding report on main contribution: TabPFN encodes structured, interpretable internal quantities. Source abstract supports probing hidden representations and layer evolution (`data/2601.08181.txt:13-20`).
  2. [S2] Qwen is more expansive and occasionally more mechanistic in phrasing; source supports “clear signals” and “evidence,” but not complete proof of all internal algorithmic steps (`data/2601.08181.txt:22-34`).
  3. [S3] Metadata is cleaner than grounding report; sections are complete and useful for onboarding.
- Final judgment: Strong and synthesis-usable; slightly more over-explanatory than grounding but still well within PASS-High. Move to `bad/`: No.

### 2601.17472 — Grounding report QA
- report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.17472_adversarial-alignment-and-disentanglement-for-cross-domain-ctr-prediction-with-domain-encompassing-features_20260401_064553.md`
- source_path: `data/2601.17472.txt`
- comparison_model: Qwen current report
- Score: Match 5, Core Facts 27, Quant 13, Claim Discipline 17, Metadata 4, Unsupported 13, Anchoring 10 = **89 / 100**
- Verdict: **PASS-Conditional**
- Findings:
  1. [S3] Source supports the central cross-domain recommendation framing and A2DCDR outperforming existing methods on real-world datasets and online A/B testing (`data/2601.17472.txt:35-54`).
  2. [S2] Grounding report captures adversarial alignment / disentanglement / domain-encompassing feature logic, but public source contains many detailed metric tables; any broad “SOTA” wording should remain tied to reported scenarios (`data/2601.17472.txt:689-785`).
  3. [S2] Some risk of over-compressing industrial evaluation details and exact metric conditions; not core-breaking.
- Final judgment: Reliable but needs metric/scenario caution. Move to `bad/`: No.

### 2601.17472 — Qwen report QA
- report_path: `data/2601.17472_adversarial-alignment-and-disentanglement-for-cross-domain-ctr-prediction-with-domain-encompassing-features_20260505_222915.md`
- source_path: `data/2601.17472.txt`
- grounding_report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.17472_adversarial-alignment-and-disentanglement-for-cross-domain-ctr-prediction-with-domain-encompassing-features_20260401_064553.md`
- Score: Match 5, Core Facts 26, Quant 13, Claim Discipline 16, Metadata 5, Unsupported 13, Anchoring 10 = **88 / 100**
- Verdict: **PASS-Conditional**
- Findings:
  1. [S3] Main problem/contribution is supported: source describes CDR, data sparsity/cold-start issues, and performance gains from A2DCDR (`data/2601.17472.txt:35-54`).
  2. [S2] Qwen is slightly more generalized in its causal/mechanistic explanation of disentanglement and transfer; source supports the mechanisms, but exact claims should stay anchored to the tested Amazon/industrial scenarios and A/B tests (`data/2601.17472.txt:713-785`).
  3. [S2] Quantitative detail is less explicit than ideal for a recommendation paper with GAUC/NDCG/A/B results; grounding report is similar but marginally more calibrated.
- Final judgment: Usable, but claim calibration and numeric scenario specificity should be reviewed before synthesis. Move to `bad/`: No.

### 2601.22397 — Grounding report QA
- report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.22397_sair-cost-efficient-multi-stage-ml-pipeline-autoscaling-via-in-context-reinforcement-learning_20260210_092002.md`
- source_path: `data/2601.22397.txt`
- comparison_model: Qwen current report
- Score: Match 5, Core Facts 28, Quant 14, Claim Discipline 18, Metadata 4, Unsupported 14, Anchoring 10 = **93 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Source abstract supports SAIR as an LLM in-context RL autoscaling controller with Pareto reward shaping, surprisal retrieval, and CUDA interception (`data/2601.22397.txt:8-20`).
  2. [S3] Quantitative claims are strongly supported: source reports best/tied-best P99 and cost, up to 50% P99 improvement, up to 97% effective cost reduction, and 86% bottleneck detection (`data/2601.22397.txt:21-27`).
  3. [S2] Cost-reduction claims must retain the source caveat “under GPU rate-control assumptions” (`data/2601.22397.txt:24-26`). Grounding report generally keeps the caveat visible.
- Final judgment: Strong grounding report with good numeric fidelity. Move to `bad/`: No.

### 2601.22397 — Qwen report QA
- report_path: `data/2601.22397_sair-cost-efficient-multi-stage-ml-pipeline-autoscaling-via-in-context-reinforcement-learning_20260505_224000.md`
- source_path: `data/2601.22397.txt`
- grounding_report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2601.22397_sair-cost-efficient-multi-stage-ml-pipeline-autoscaling-via-in-context-reinforcement-learning_20260210_092002.md`
- Score: Match 5, Core Facts 28, Quant 14, Claim Discipline 17, Metadata 5, Unsupported 14, Anchoring 10 = **93 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Qwen captures the core controller design and training-free/in-context RL framing supported by the source abstract (`data/2601.22397.txt:12-20`).
  2. [S3] Primary result in Qwen matches source: up to 50% P99 and up to 97% effective cost reduction (`data/2601.22397.txt:21-26`).
  3. [S2] Ensure downstream synthesis preserves “under GPU rate-control assumptions” for 97% cost reduction; Qwen mentions assumptions in some places but the headline can read broad.
- Final judgment: Strong, slightly more detailed than grounding, with one caveat to preserve. Move to `bad/`: No.

### 2602.00053 — Grounding report QA
- report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2602.00053_scalable-and-secure-ai-inference-in-healthcare-a-comparative-benchmarking-of-fastapi-and-triton-inference-server-on-kubernetes_20260405_112931.md`
- source_path: `data/2602.00053.txt`
- comparison_model: Qwen current report
- Score: Match 5, Core Facts 27, Quant 14, Claim Discipline 17, Metadata 4, Unsupported 13, Anchoring 10 = **90 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Source supports FastAPI vs Triton benchmarking on Kubernetes for healthcare inference, DistilBERT sentiment model, p50/p95 latency and throughput (`data/2602.00053.txt:17-25`).
  2. [S3] Quantitative facts are clear: FastAPI p50 22ms, Triton dynamic batching 780 req/s, near double baseline throughput (`data/2602.00053.txt:26-30`, `data/2602.00053.txt:188-215`).
  3. [S2] Any “HIPAA-compliant” language should be interpreted as architecture/privacy design validation, not legal certification; source frames PHI de-identification and secure gateway architecture (`data/2602.00053.txt:31-34`, `data/2602.00053.txt:241-253`).
- Final judgment: Reliable; privacy/legal claims require wording caution. Move to `bad/`: No.

### 2602.00053 — Qwen report QA
- report_path: `data/2602.00053_scalable-and-secure-ai-inference-in-healthcare-a-comparative-benchmarking-of-fastapi-and-triton-inference-server-on-kubernetes_20260505_224019.md`
- source_path: `data/2602.00053.txt`
- grounding_report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2602.00053_scalable-and-secure-ai-inference-in-healthcare-a-comparative-benchmarking-of-fastapi-and-triton-inference-server-on-kubernetes_20260405_112931.md`
- Score: Match 5, Core Facts 26, Quant 13, Claim Discipline 15, Metadata 5, Unsupported 12, Anchoring 10 = **86 / 100**
- Verdict: **PASS-Conditional**
- Findings:
  1. [S3] Correctly captures the core FastAPI/Triton/Kubernetes healthcare inference comparison supported by source (`data/2602.00053.txt:17-25`).
  2. [S2] Qwen headline says “73% throughput increase over FastAPI alone”; source gives FastAPI 450 req/s and Triton dynamic batching 780 req/s, which is roughly +73%, but source phrases this as nearly double baseline and gives raw numbers (`data/2602.00053.txt:195-215`). Raw numbers should be retained.
  3. [S1/S2] Qwen phrase “HIPAA-compliant PHI de-identification” risks over-certification. Source discusses privacy standards and a hybrid architecture with de-identification, not a formal HIPAA compliance audit (`data/2602.00053.txt:14-17`, `data/2602.00053.txt:31-34`).
- Final judgment: Usable but privacy/compliance language needs tightening. Move to `bad/`: No.

### 2602.05670 — Grounding report QA
- report_path: not found under `/Users/trentrobbins/code/analysis/ml_research_analysis_*`
- source_path: `data/2602.05670.txt`
- Score: **BLOCKED**
- Verdict: **BLOCKED**
- Findings:
  1. [S0/BLOCKED] No same-ID grounding report found via targeted prefix glob.
- Final judgment: Cannot grade grounding report. Move to `bad/`: No report to move.

### 2602.05670 — Qwen report QA
- report_path: `data/2602.05670_hyperpotter-spell-the-charm-of-high-order-interactions-in-audio-deepfake-detection_20260505_222943.md`
- source_path: `data/2602.05670.txt`
- grounding_report_path: not found
- Score: Match 5, Core Facts 28, Quant 15, Claim Discipline 18, Metadata 5, Unsupported 14, Anchoring 10 = **95 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Source abstract supports the central claim: HyperPotter models high-order interactions using hypergraphs, clustering-based hyperedges, and class-aware prototype initialization (`data/2602.05670.txt:10-16`).
  2. [S3] Quantitative claims are directly supported: average relative gain 22.15% across 11 datasets and 13.96% over SOTA on 4 cross-domain datasets (`data/2602.05670.txt:15-18`).
  3. [S2] Because no grounding report exists, source anchoring carries the evaluation; Qwen should avoid implying independent replication beyond the paper's experiments.
- Final judgment: Strong Qwen digest, source-grounded. Move to `bad/`: No.

### 2602.09972 — Grounding report QA
- report_path: not found under `/Users/trentrobbins/code/analysis/ml_research_analysis_*`
- source_path: `data/2602.09972.txt`
- Score: **BLOCKED**
- Verdict: **BLOCKED**
- Findings:
  1. [S0/BLOCKED] No same-ID grounding report found via targeted prefix glob.
- Final judgment: Cannot grade grounding report. Move to `bad/`: No report to move.

### 2602.09972 — Qwen report QA
- report_path: `data/2602.09972_hydra-nav-object-navigation-via-adaptive-dual-process-reasoning_20260505_224630.md`
- source_path: `data/2602.09972.txt`
- grounding_report_path: not found
- Score: Match 5, Core Facts 28, Quant 15, Claim Discipline 18, Metadata 5, Unsupported 14, Anchoring 10 = **95 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Source abstract supports Hydra-Nav as a unified VLM architecture switching between deliberative slow reasoning and reactive fast execution (`data/2602.09972.txt:10-18`).
  2. [S3] Source supports three-stage curriculum: spatial-action alignment, memory-reasoning integration, iterative rejection fine-tuning (`data/2602.09972.txt:17-20`).
  3. [S3] Quantitative results are anchored: outperforms second-best by 11.1%, 17.4%, and 21.2% on HM3D, MP3D, and OVON, and introduces SOT (`data/2602.09972.txt:20-25`).
- Final judgment: Strong and well-grounded. Move to `bad/`: No.

### 2602.14404 — Grounding report QA
- report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2602.14404_boule-or-baguette-a-study-on-task-topology-length-generalization-and-the-benefit-of-reasoning-traces_20260331_221240.md`
- source_path: `data/2602.14404.txt`
- comparison_model: Qwen current report
- Score: Match 5, Core Facts 28, Quant 14, Claim Discipline 18, Metadata 4, Unsupported 14, Anchoring 10 = **93 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Source supports PITA as a dataset of over 23 million propositional-logic statements/proofs (`data/2602.14404.txt:14-17`).
  2. [S3] Source supports core topology result: RT models generalize on broad/shallow subsets and deteriorate on narrow/deep subsets relative to non-RT baselines (`data/2602.14404.txt:26-35`, `data/2602.14404.txt:104-108`).
  3. [S2] Quantitative specifics depend on task splits; grounding report should avoid reducing the paper to a universal anti-RT claim.
- Final judgment: Strong grounding; good calibration. Move to `bad/`: No.

### 2602.14404 — Qwen report QA
- report_path: `data/2602.14404_boule-or-baguette-a-study-on-task-topology-length-generalization-and-the-benefit-of-reasoning-traces_20260505_224014.md`
- source_path: `data/2602.14404.txt`
- grounding_report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2602.14404_boule-or-baguette-a-study-on-task-topology-length-generalization-and-the-benefit-of-reasoning-traces_20260331_221240.md`
- Score: Match 5, Core Facts 28, Quant 14, Claim Discipline 18, Metadata 5, Unsupported 14, Anchoring 10 = **94 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Qwen correctly captures PITA, task depth/breadth, and length generalization (`data/2602.14404.txt:14-26`).
  2. [S3] Qwen's “broad/shallow vs narrow/deep” mechanism is directly grounded in source (`data/2602.14404.txt:26-35`, `data/2602.14404.txt:104-108`).
  3. [S2] Check that any synthesis preserves the conditional nature: RTs are beneficial in broad/shallow regimes and limited in deep/narrow regimes, not globally good/bad.
- Final judgment: Excellent Qwen digest; slightly cleaner metadata and similar calibration to grounding. Move to `bad/`: No.

### 2603.17189 — Grounding report QA
- report_path: not found under `/Users/trentrobbins/code/analysis/ml_research_analysis_*`
- source_path: `data/2603.17189.txt`
- Score: **BLOCKED**
- Verdict: **BLOCKED**
- Findings:
  1. [S0/BLOCKED] No same-ID grounding report found via targeted prefix glob.
- Final judgment: Cannot grade grounding report. Move to `bad/`: No report to move.

### 2603.17189 — Qwen report QA
- report_path: `data/2603.17189_influence-of-gripper-design-on-human-demonstration-quality-for-robot-learning_20260505_224700.md`
- source_path: `data/2603.17189.txt`
- grounding_report_path: not found
- Score: Match 5, Core Facts 27, Quant 13, Claim Discipline 17, Metadata 5, Unsupported 14, Anchoring 10 = **91 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Source supports study design: three conditions (distributed load grippers, concentrated load grippers, bare hands), eight participants, timed trials, success/completion/damage/NASA-TLX (`data/2603.17189.txt:12-18`).
  2. [S3] Source abstract supports main conclusion: concentrated grippers improved relative to distributed but remained slower and less effective than hands (`data/2603.17189.txt:19-24`).
  3. [S2] Qwen includes specific 100% vs 65.8% and workload detail from the body; these are plausible from the report/source, but the abstract anchor alone is less complete. Keep exact quantitative claims tied to table/body evidence when used.
- Final judgment: Reliable, with normal caution around small n=8 and body-level numeric anchors. Move to `bad/`: No.

### 2603.28644 — Grounding report QA
- report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2603.28644_constructing-composite-features-for-interpretable-music-tagging_20260403_093408.md`
- source_path: `data/2603.28644.txt`
- comparison_model: Qwen current report
- Score: Match 5, Core Facts 27, Quant 13, Claim Discipline 18, Metadata 4, Unsupported 14, Anchoring 10 = **91 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Source supports GP pipeline for interpretable composite music features (`data/2603.28644.txt:7-14`).
  2. [S3] Source supports MTG-Jamendo/GTZAN consistent improvements and early gains within first few hundred GP evaluations (`data/2603.28644.txt:15-20`).
  3. [S2] Avoid overclaiming broad SOTA dominance; source says improvements compared to state-of-the-art systems across base feature sets and datasets, but exact metric/split details matter (`data/2603.28644.txt:15-18`).
- Final judgment: Good grounding report; concise but reliable. Move to `bad/`: No.

### 2603.28644 — Qwen report QA
- report_path: `data/2603.28644_constructing-composite-features-for-interpretable-music-tagging_20260505_224104.md`
- source_path: `data/2603.28644.txt`
- grounding_report_path: `/Users/trentrobbins/code/analysis/ml_research_analysis_2026/2603.28644_constructing-composite-features-for-interpretable-music-tagging_20260403_093408.md`
- Score: Match 5, Core Facts 27, Quant 13, Claim Discipline 17, Metadata 5, Unsupported 14, Anchoring 10 = **91 / 100**
- Verdict: **PASS-High**
- Findings:
  1. [S3] Qwen correctly identifies the core contribution: GP evolves interpretable composite features for music tagging, preserving interpretability while improving feature fusion (`data/2603.28644.txt:7-14`).
  2. [S3] Qwen's primary result about consistent improvements on standard music-tagging benchmarks is source-supported (`data/2603.28644.txt:15-18`).
  3. [S2] Qwen says “outperform baseline XGBoost classifiers”; source discusses improvements compared to state-of-the-art systems and base feature sets. This is likely tied to the method section but should be checked against exact experimental tables before strong synthesis claims.
- Final judgment: Strong, readable Qwen digest; same total as grounding, with slightly richer onboarding and slightly more need for metric-table verification. Move to `bad/`: No.
