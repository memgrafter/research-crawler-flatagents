---
title: Attention Is All You Need
arxiv_id: '1706.03762'
year: 2017
domain: nlp, machine-translation
task: sequence-transduction (encoder-decoder)
key_metric: BLEU (EN-DE: 28.4, EN-FR: 41.8)
baseline_beaten: ensemble RNN attention models
novelty_level: paradigm-shifting
reproducibility: high
code_available_at_publication: no
---

## What This Paper Did

Prior sequence models used recurrence (RNNs/LSTMs) or convolutions to process tokens one at a time or in local windows. The Transformer eliminates both — replacing them entirely with self-attention, where every token can directly attend to every other token in parallel. The result: state-of-the-art machine translation in 3.5 days of training on 8 GPUs, beating the prior best ensemble model that took weeks. This is the architecture that became the foundation of all subsequent LLMs.

The core idea is simple but radical: instead of passing information through a chain of hidden states, let every position compute a weighted combination of all other positions in one step. The scaling factor `1/√d_k` keeps softmax from saturating. Multiple attention heads let the model attend in different representational subspaces simultaneously. Sinusoidal positional encodings give the model a sense of order without any recurrence.

## Method

**Architecture:** Encoder-decoder with 6 identical layers each. Encoder layers: multi-head self-attention → residual + layer norm → feed-forward → residual + layer norm. Decoder layers add a cross-attention step between them (queries from decoder, keys/values from encoder). All sub-layers use residual connections and layer normalization.

**Attention mechanism:** Scaled dot-product — `softmax(QK^T / √d_k)V`. Multi-head: project Q, K, V into `h=8` lower-dimensional spaces, attend independently, concatenate, then linear transform. The scaling factor prevents gradient vanishing when `d_k` is large.

**Positional encoding:** Sinusoidal functions of varying frequencies — no learned embeddings. This allows the model to potentially reason about relative positions and extrapolate beyond training lengths.

**Training details:** Adam (β₁=0.9, β₂=0.98, ε=1e-9), warmup for 4000 steps then inverse sqrt LR decay, label smoothing 0.1, dropout 0.1. Batch size equivalent to 250K tokens/step. Data: WMT 2014 EN-DE (4.5M pairs) and EN-FR (36M pairs), BPE with ~37K shared vocabulary, sentences ≤50 tokens.

**Model sizes:** Base (`d_model=512`, `h=8`, `d_ff=2048`, 6 layers) and Big (`d_model=1024`, `h=16`, `d_ff=4096`, 6 layers). The base model achieved the headline results.

## Key Results

| Model | EN-DE BLEU | EN-FR BLEU | Training time |
|-------|-----------|-----------|---------------|
| Transformer (base) | 28.4 | 41.8 | 3.5 days, 8 GPUs |
| Transformer (big) | — | 41.0 | 3.5 days, 8 GPUs |
| Prior best (ensemble) | 26.7 | 41.0 | — |

Single model beats prior best ensemble on EN-DE, matches it on EN-FR.

## Why It Works

**O(1) path length.** Self-attention connects every position directly — no chain of intermediate states. RNNs require O(n) steps; ConvNets require O(log_k(n)). Shorter paths mean gradients flow more easily across long distances, making long-range dependencies learnable without vanishing. *Break condition:* if sequences get too long, O(n²) attention becomes infeasible and you need sparse approximations.

**Multi-head diversity.** A single attention head averages across all positions and learns one dominant pattern. Multiple heads let the model attend in different subspaces simultaneously — tracking syntactic dependencies, semantic roles, positional proximity in parallel. *Break condition:* too many heads relative to `d_model` starves each head of dimensions; too few gives insufficient diversity.

**Sinusoidal encodings enable relative reasoning.** The encoding at `pos + offset` is a linear function of the encoding at `pos` for any fixed offset, so the model can learn relative position without retraining — something learned embeddings can't do. *Break condition:* extrapolation to very long unseen sequences may degrade; the authors state this but don't test it.

## What's Specified (for reproduction)

- Full architecture: layer count, dimensions (`d_model`, `d_ff`, `h`), residual connections, layer norm placement
- Optimizer: Adam with β₁=0.9, β₂=0.98, ε=1e-9
- Learning rate schedule: warmup for 4000 steps then inverse sqrt decay (formula given)
- Dropout rates: 0.1 across attention, FFN outputs, embedding sums
- Label smoothing: 0.1
- Batch size: ~250K tokens per step
- Data preprocessing: BPE with 37K shared vocabulary, sentences ≤50 tokens
- Training data sources and sizes (WMT 2014 EN-DE and EN-FR)

## What's Missing (blocking reproduction)

- **Weight initialization** — not specified (Xavier? He? custom?)
- **Gradient clipping** — whether/how gradients were clipped is absent
- **Exact warmup formula** — LR plot shown but precise interpolation over 4000 steps not stated as a formula
- **Validation protocol** — no details on validation set or early stopping criteria
- **Beam search parameters** — beam size and length penalty for inference unspecified
- **Data augmentation** — whether back-translation was used is unclear

## Author Uncertainties & Hedges

- **Sinusoidal extrapolation claim:** The authors say sinusoidal encodings "might" enable generalization beyond training lengths — presented as a hypothesis, no empirical test.
- **Why multi-head works:** Authors note heads "tend to specialize" but provide no ablation on head count. No justification for `h=8` specifically.
- **Attention vs. FFN contribution:** The paper never disentangles how much comes from attention vs. feed-forward networks — a question that would become central in later research.
- **Local attention for long sequences:** Authors acknowledge O(n²) as a barrier and plan to investigate, but offer no preliminary analysis.

## Open Questions

1. **Can sparse/local attention match dense self-attention on translation?** The paper identifies O(n²) complexity as the bottleneck. Resolving: empirical comparison of sparse variants (banded, strided, learned sparsity) on the same tasks.

2. **Can the Transformer generalize to non-text modalities?** Validated only on text. Resolving: apply to vision/audio — later done with ViT but was open at publication.

3. **Can generation be made less sequential?** Auto-regressive decoding is inherently sequential. Resolving: parallel decoding methods (speculative decoding, etc.).

4. **How important is the positional encoding choice?** No ablation against alternatives (learned embeddings, relative encodings). Critical as sequence lengths grow beyond training distribution.

## Failure Modes & Limitations

- **O(n²) memory and compute per layer** — fundamental scalability barrier vs. RNNs (O(n)) and ConvNets (O(nk))
- **No inherent sequence order** — without positional encodings, the model is permutation-invariant; sinusoidal encoding is a workaround, not a principled solution
- **Decoder masking is fragile** — if the causal mask leaks, the model cheats during training and produces garbage at inference (common implementation bug)
- **Scaling factor is essential** — removing `1/√d_k` causes softmax saturation, vanishing gradients, training failure

## Confidence Assessment

**High:** BLEU results — specific metrics, datasets, protocols clearly stated and reproducible. Architecture description detailed enough for direct implementation.

**Medium:** O(1) path length advantage — theoretically sound but not empirically validated with gradient flow measurements comparing architectures on the same task.

**Low:** Sinusoidal encodings enable extrapolation beyond training lengths — hypothesis without empirical test. Later proved partially true, not universal.

## Next Checks

1. **Gradient flow comparison:** Implement gradient norm analysis on a synthetic long-range dependency task comparing Transformer, RNN, and ConvNet at matched capacity. Measure gradient magnitude decay with distance across architectures — directly test the O(1) path length claim.

2. **Scaling factor ablation:** Train base model without `1/√d_k` on small translation dataset. Monitor gradient norms for first 500 steps to observe softmax saturation failure mode.

3. **Positional encoding extrapolation test:** Train with sinusoidal and learned embeddings on sequences up to length 50. Evaluate both at lengths 100 and 200 to test whether sinusoidal encodings enable the extrapolation the authors hypothesize.
