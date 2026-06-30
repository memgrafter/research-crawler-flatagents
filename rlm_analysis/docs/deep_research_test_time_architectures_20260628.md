---
arxiv_id: "N/A"
core_contribution: "Comprehensive analysis of test-time deep research architectures using consumer-facing LLMs with strong organizational capabilities, proposing multiple non-fine-tuning approaches including structured reasoning, knowledge graph construction, and hierarchical synthesis. RAG is included in only one of six proposed architectures."
tags: ["deep-research", "test-time-scaling", "structured-reasoning", "knowledge-graphs", "agentic-search", "information-seeking"]
---

# Test-Time Deep Research Architectures for Consumer LLMs

### Problem Space

Deep research — the automated generation of comprehensive, well-organized reports requiring multi-step information gathering and synthesis — has become a dominant application area for LLMs (see [2508.12752](https://arxiv.org/abs/2508.12752), [2603.28361](https://arxiv.org/abs/2603.28361)). The current approach relies heavily on two assumptions: (1) retrieval-augmented generation (RAG) as the primary information source, and (2) fine-tuning or reinforcement learning to optimize search policies.

This analysis proposes **six distinct test-time architectures** that avoid both assumptions. The target model is a single consumer-facing LLM with strong organizational skills (e.g., Claude Sonnet 4, GPT-4o), augmented only by prompt engineering and custom code. These architectures are designed for **inference-time scaling** — using compute at test time rather than training time to achieve deep research quality.

---

## Architecture 1: Structured Reasoning Tree (SRT)

### Overview

The most directly applicable approach for a single high-organization LLM. Instead of flat ReAct loops, the model constructs a **hierarchical reasoning tree** at test time, where each node represents a sub-question or evidence category, and edges represent logical dependencies.

### Mechanism

1. **Query Decomposition**: The model takes the research question and generates a structured decomposition — a tree of sub-questions organized by topic, with explicit dependency annotations (e.g., "understand X before comparing Y").

2. **Parallel Branch Execution**: Independent branches are processed in parallel via separate LLM calls. Each branch produces a structured finding block containing: claim, supporting evidence (from the model's internal knowledge), confidence score, and open gaps.

3. **Gap-Driven Iteration**: The system identifies unresolved gaps from step 2 and generates targeted follow-up queries. This is **not** RAG — the LLM uses its own parameterized knowledge to reason about what it doesn't know, then formulates precise sub-queries for external tools (web search, API calls) only when necessary.

4. **Hierarchical Synthesis**: Results from leaf nodes are merged upward through the tree, with each parent node performing a structured synthesis of its children's findings, explicitly noting contradictions and confidence levels.

### Why It Works

- **Organization-first design**: The tree structure matches how the LLM naturally organizes information. Models like Claude Sonnet 4 excel at hierarchical decomposition (see [2511.18303](https://arxiv.org/abs/2511.18303) for hierarchical deep research agents).
- **Compute scaling**: More compute = deeper tree + more iterations, not just longer chains of thought.
- **Error isolation**: A hallucination in one branch doesn't propagate to unrelated branches.

### Key Design Decisions

- **Structured output format**: Each node must produce a fixed schema (claim, evidence, confidence, gaps). This prevents the "lost-in-the-middle" problem identified in [2602.13830](https://arxiv.org/abs/2602.13830).
- **Explicit contradiction tracking**: The synthesis step must flag contradictions between branches, not just merge findings.

### Limitations

- Requires careful prompt engineering to maintain schema compliance across deep trees.
- The model's internal knowledge may be outdated for rapidly evolving topics (see [2601.04888](https://arxiv.org/abs/2601.04888) on inaccurate intermediate queries).

---

## Architecture 2: Knowledge Graph Constructor (KGC)

### Overview

The LLM constructs a **working knowledge graph** at test time, incrementally enriching it with entities and relationships as it reasons through the research question. The final report is generated from the graph structure, not from a linear chain of thought.

### Mechanism

1. **Entity Extraction**: Given the query, the model identifies key entities (people, concepts, organizations, events) and outputs them in a structured format (e.g., JSON-LD or RDF triples).

2. **Relationship Propagation**: The model reasons about relationships between entities — causal, temporal, hierarchical — and adds edges to the graph. Each edge includes a confidence score and a short justification.

3. **Graph-Augmented Generation**: At each step, the model queries its own graph (via custom code, not RAG) to find relevant subgraphs for the current reasoning step. This is **self-referential** — the model reads from a graph it built itself.

4. **Iterative Enrichment**: The model identifies missing information by analyzing graph sparsity — topics with few connections get priority for further exploration.

5. **Report Generation**: The final report is generated by traversing the graph in a logical order (e.g., topological sort), producing sections that follow the graph's natural structure.

### Why It Works

- **Explicit knowledge representation**: The graph makes the model's internal reasoning traceable and auditable — addressing the auditability bottleneck noted in [2602.13855](https://arxiv.org/abs/2602.13855).
- **Natural organization**: Graphs encode relationships explicitly, which is exactly what deep research requires.
- **Incremental improvement**: More compute = more entities and edges, directly improving coverage.

### Key Design Decisions

- **Schema flexibility**: The graph schema must be flexible enough to represent diverse domains (see [2510.08521](https://arxiv.org/abs/2510.08521) on FlowSearch's dynamic knowledge flow).
- **Confidence-aware edges**: Every relationship must include a confidence score; low-confidence edges are flagged for the user.

### Limitations

- Graph construction can diverge if the model hallucinates entities (see [2603.28376](https://arxiv.org/abs/2603.28376) on error propagation).
- Complex graph queries require custom code, adding engineering overhead.

---

## Architecture 3: Multi-Pass Synthesis (MPS)

### Overview

Instead of gathering information first and synthesizing later, this architecture interleaves **multiple passes** of synthesis and exploration. Each pass produces a draft report, which is then analyzed for gaps, leading to targeted exploration in the next pass.

### Mechanism

1. **Pass 0 — Initial Synthesis**: The model generates a complete first-draft report from its internal knowledge alone. This draft includes explicit uncertainty markers ("I'm uncertain about X", "This area needs verification").

2. **Gap Analysis**: A separate LLM call analyzes the draft, extracting a structured list of gaps: factual claims needing verification, missing perspectives, and logical holes.

3. **Targeted Exploration**: For each gap, the model formulates a precise sub-query and uses external tools (search, APIs) to gather evidence.

4. **Pass N — Revision**: The model revises the report incorporating new evidence, marking previously uncertain claims as verified or corrected.

5. **Convergence Check**: After each pass, a separate LLM call assesses whether the report has converged (no new gaps found). If not, repeat from step 2.

### Why It Works

- **Progressive refinement**: Each pass produces a usable output — the system is graceful under time constraints.
- **Efficient compute use**: The model doesn't waste tokens on blind exploration; it targets known gaps (see [2510.14337](https://arxiv.org/abs/2510.14337) on Stop-RAG's value-based stopping).
- **Self-correction**: Errors in early passes are explicitly corrected, not accumulated (see [2511.18743](https://arxiv.org/abs/2511.18743) on error accumulation in deep research).

### Key Design Decisions

- **Separate gap-analysis call**: Using a distinct LLM call for gap analysis prevents the model from being biased toward its own draft.
- **Convergence threshold**: The system must have a clear stopping criterion (e.g., 2 consecutive passes with <5% new gaps).

### Limitations

- Early drafts may contain hallucinations that misdirect subsequent exploration (see [2601.04888](https://arxiv.org/abs/2601.04888)).
- Requires careful token budgeting — full report regeneration at each pass is expensive.

---

## Architecture 4: Debate-and-Synthesize (DAS)

### Overview

Multiple instances of the same LLM are prompted with different perspectives or roles, generating competing analyses. A synthesizer instance then reconciles the differences into a unified report.

### Mechanism

1. **Perspective Assignment**: The model is prompted with distinct analytical lenses (e.g., "You are a skeptical analyst focusing on limitations", "You are an optimistic analyst focusing on opportunities"). Each perspective generates a structured analysis of the research question.

2. **Cross-Examination**: Pairs of perspectives exchange critiques — each identifies flaws in the other's analysis. This is not adversarial; it's structured as constructive disagreement.

3. **Synthesis**: A third instance (or the same model with a different prompt) reads all perspectives and critiques, producing a unified report that explicitly acknowledges areas of disagreement.

4. **Iterative Refinement**: If the synthesizer identifies unresolved disagreements, it can request additional rounds of debate on specific points.

### Why It Works

- **Reduces single-perspective bias**: The model's default tendency toward agreeable outputs is countered by structured disagreement (see [2510.12979](https://arxiv.org/abs/2510.12979) on high entropy in planning tokens).
- **Natural organization**: The debate structure maps directly to report sections ("Arguments for X", "Counterarguments", "Synthesis").
- **Compute scaling**: More compute = more perspectives and more rounds, improving coverage.

### Key Design Decisions

- **Structured critique format**: Critiques must follow a fixed schema (claim, evidence against, alternative interpretation) to prevent circular arguments.
- **Synthesizer independence**: The synthesizer should not be influenced by which perspective it "agrees with" — its role is reconciliation, not arbitration.

### Limitations

- Debate can diverge into unproductive tangents without careful constraint (see [2602.07549](https://arxiv.org/abs/2602.07549) on illusory completion).
- Requires significant compute for multiple LLM calls per research question.

---

## Architecture 5: Outline-First Generation (OFG)

### Overview

The model generates a **detailed outline** of the final report first, then fills in each section independently. This leverages the model's strong organizational skills at the highest level, before committing to content generation.

### Mechanism

1. **Outline Generation**: The model produces a comprehensive, multi-level outline (3-4 levels deep) for the research report. Each node includes: title, brief description, expected length, and key questions to address.

2. **Section Scoring**: The model scores each section on importance and uncertainty — high-importance, high-uncertainty sections get priority for external verification.

3. **Parallel Section Generation**: Independent sections are generated in parallel LLM calls, each with the full outline as context so the model knows how its section fits into the whole.

4. **Verification Pass**: For high-uncertainty sections, a separate call verifies factual claims against external sources.

5. **Assembly and Polish**: Sections are assembled into the final report with a polishing pass that ensures transitions and consistency.

### Why It Works

- **Leverages organizational strength**: The outline is generated from the model's strongest capability — structuring information (see [2601.12369](https://arxiv.org/abs/2601.12369) on TaxoBench for taxonomy organization in DRAs).
- **Error containment**: A hallucination in one section doesn't affect others.
- **Efficient compute use**: Parallel section generation reduces wall-clock time vs. sequential approaches.

### Key Design Decisions

- **Outline depth matters**: 3-4 levels is the sweet spot — too shallow and sections are too broad, too deep and the model loses coherence (see [2512.13059](https://arxiv.org/abs/2512.13059) on iterative retrieval for long-form QA).
- **Cross-section references**: The outline should include explicit cross-references so the model can maintain consistency.

### Limitations

- Outline quality determines everything — a bad outline leads to a disjointed report.
- The model may struggle with inter-section dependencies (e.g., comparing X in section 3 requires understanding Y from section 2).

---

## Architecture 6: RAG-Augmented Structured Reasoning (RAG-SR)

### Overview

The **only architecture that uses RAG**. This combines retrieval-augmented generation with the structured reasoning approach of Architecture 1, but with a key difference: RAG is used for **verification**, not information gathering. The model reasons from its internal knowledge first, then retrieves evidence to support or challenge its claims.

### Mechanism

1. **Initial Structured Reasoning**: The model generates a structured analysis (as in SRT) from internal knowledge alone, with confidence scores on each claim.

2. **Targeted Retrieval**: For low-confidence claims, the system formulates precise search queries and retrieves evidence via RAG. This is targeted, not broad — each retrieval targets a specific claim.

3. **Evidence Integration**: Retrieved evidence is integrated into the structured analysis, updating confidence scores and correcting any hallucinated claims.

4. **Synthesis**: The final report is generated from the evidence-augmented structured analysis.

### Why It Works

- **Efficient RAG use**: Retrieval is focused on verification, not exploration — fewer queries needed (see [2510.14337](https://arxiv.org/abs/2510.14337) on value-based stopping in retrieval).
- **Reduces hallucination**: Claims are verified against external evidence before reporting.
- **Combines strengths**: Uses the model's organizational skills for structure, and RAG only where it adds clear value (fact-checking).

### Key Design Decisions

- **Confidence threshold**: Claims below a confidence threshold trigger retrieval; above it, the model trusts its own knowledge.
- **Evidence sufficiency check**: The system must determine when retrieved evidence is sufficient to verify a claim — partial evidence may be worse than no evidence (see [2603.05912](https://arxiv.org/abs/2603.05912) on DRR factuality verification).

### Limitations

- Requires a quality retrieval system — poor retrieval undermines the whole approach.
- The confidence threshold is domain-dependent and hard to calibrate.

---

## Architecture Comparison

| Architecture | RAG Required? | Compute Scaling | Organization Quality | Hallucination Risk | Best For |
|---|---|---|---|---|---|---|
| **SRT** | No | Deep tree + iterations | Very High (hierarchical) | Medium (branch isolation helps) | Complex multi-faceted topics |
| **KGC** | No | More entities/edges | Very High (graph-structured) | Medium-High (entity hallucination) | Relationship-heavy analysis |
| **MPS** | No | More passes | High (progressive refinement) | Low (self-correction) | Time-constrained research |
| **DAS** | No | More perspectives/rounds | High (debate-structured) | Low (cross-examination) | Controversial topics |
| **OFG** | No | Parallel sections | Very High (outline-driven) | Medium (section isolation helps) | Well-scoped reports |
| **RAG-SR** | Yes | More verification queries | Very High (evidence-backed) | Low (retrieval verification) | Fact-heavy domains |

---

## Key Patterns Across Architectures

### 1. Structured Output Is Non-Negotiable

Every architecture requires the LLM to produce structured output at intermediate steps — not free-text chain-of-thought. This is because:
- Free-text reasoning degrades with length (lost-in-the-middle, see [2602.13830](https://arxiv.org/abs/2602.13830))
- Structured outputs enable programmatic processing (gap detection, confidence scoring)
- Schema compliance is the primary failure mode in long-horizon agents (see [2511.18743](https://arxiv.org/abs/2511.18743))

**Implementation**: Use JSON schemas or structured prompts with explicit field definitions. Validate output programmatically before passing it to the next step.

### 2. Confidence Scoring Enables Compute Allocation

All architectures benefit from confidence scores on intermediate claims. This enables:
- **Compute allocation**: Low-confidence areas get more attention (more iterations, more retrieval)
- **User transparency**: The final report can flag uncertain areas
- **Stopping criteria**: Convergence is measured by confidence improvement across passes

**Implementation**: A simple 1-5 scale with justification works. Don't over-engineer this — the model's own confidence calibration is imperfect (see [2603.05912](https://arxiv.org/abs/2603.05912)).

### 3. Separation of Concerns Prevents Error Propagation

The most robust architectures separate reasoning, verification, and synthesis into distinct LLM calls:
- A reasoning call generates claims
- A verification call checks them
- A synthesis call produces the final report

This prevents the model from being influenced by its own potential errors (see [2603.28376](https://arxiv.org/abs/2603.28376) on error propagation).

### 4. Test-Time Compute Scaling Is Linear, Not Exponential

All six architectures scale linearly with compute:
- SRT: deeper tree, more iterations
- KGC: more entities and edges
- MPS: more passes
- DAS: more perspectives, more rounds
- OFG: more parallel sections
- RAG-SR: more verification queries

This is in contrast to approaches that require exponential context growth (e.g., accumulating full conversation histories, see [2511.02805](https://arxiv.org/abs/2511.02805) on MemSearcher's memory inefficiency).

---

## Practical Implementation Guide

### Choosing an Architecture

- **General purpose**: Start with **SRT** (Architecture 1) — it's the most general and has the clearest structure.
- **Time-constrained**: Use **MPS** (Architecture 3) — early passes produce usable output.
- **Controversial topics**: Use **DAS** (Architecture 4) — debate reduces bias.
- **Well-scoped reports**: Use **OFG** (Architecture 5) — outline-first is efficient.
- **Fact-heavy domains**: Use **RAG-SR** (Architecture 6) — retrieval verification adds confidence.

### Recommended Stack

1. **Model**: Claude Sonnet 4 or GPT-4o — both have strong organizational capabilities and structured output compliance.
2. **Orchestration**: Custom code managing the LLM calls, not an agent framework (see [2601.17879](https://arxiv.org/abs/2601.17879) on sequential agent loop limitations).
3. **Schema validation**: JSON Schema or Pydantic for structured output validation at each step.
4. **External tools**: Web search API for targeted retrieval (only in RAG-SR), domain-specific APIs as needed.

### Token Budgeting

- Allocate ~20% of budget to structured reasoning steps (decomposition, gap analysis)
- Allocate ~60% to content generation (section writing, synthesis)
- Allocate ~20% to verification and quality checks
- Use a hard token limit per step with graceful degradation (partial output is better than no output)

---

## Open Research Questions

1. **Optimal tree depth for SRT**: How deep should the reasoning tree go before diminishing returns set in? Empirical studies on this are missing (see [2511.18303](https://arxiv.org/abs/2511.18303)).

2. **Graph schema design for KGC**: What graph schema generalizes across domains while remaining expressive enough for deep research? The literature shows domain-specific schemas work best but require engineering (see [2510.08521](https://arxiv.org/abs/2510.08521)).

3. **Convergence criteria for MPS**: How many passes are enough? Current benchmarks measure final output quality, not convergence dynamics (see [2602.21230](https://arxiv.org/abs/2602.21230) on evaluation crisis).

4. **Perspective diversity in DAS**: How many perspectives are needed for comprehensive coverage? Two is clearly insufficient, but five+ may produce diminishing returns.

5. **Confidence calibration**: Can the model's self-reported confidence scores be trusted as compute allocation signals? Current evidence suggests they're poorly calibrated (see [2603.05912](https://arxiv.org/abs/2603.05912)).

6. **Cross-architecture composition**: Can SRT + MPS produce better results than either alone? The literature has not explored composing multiple test-time approaches.

---

## References

Full reference list with ~535 papers across 8 categories is available at [`spot_analyses/deep_research_references.md`](../spot_analyses/deep_research_references.md). Key papers cited above:

- [2508.12752](https://arxiv.org/abs/2508.12752) — Systematic examination of the deep research paradigm
- [2603.28361](https://arxiv.org/abs/2603.28361) — Unified definition and framework for "deep research"
- [2511.18303](https://arxiv.org/abs/2511.18303) — Hierarchical deep research agent
- [2602.13830](https://arxiv.org/abs/2602.13830) — Open-Ended Deep Research (lost-in-the-middle)
- [2511.18743](https://arxiv.org/abs/2511.18743) — RhinoInsight (error accumulation)
- [2601.17879](https://arxiv.org/abs/2601.17879) — Self-Manager (sequential loop limitations)
- [2510.14337](https://arxiv.org/abs/2510.14337) — Stop-RAG (value-based stopping)
- [2603.05912](https://arxiv.org/abs/2603.05912) — DRR factuality verification
- [2603.28376](https://arxiv.org/abs/2603.28376) — Error propagation in data synthesis
- [2601.12369](https://arxiv.org/abs/2601.12369) — TaxoBench (taxonomy organization)
- [2510.08521](https://arxiv.org/abs/2510.08521) — FlowSearch (dynamic knowledge flow)
- [2602.21230](https://arxiv.org/abs/2602.21230) — Evaluation crisis for DRAs
- [2602.07549](https://arxiv.org/abs/2602.07549) — Illusory completion in search agents
- [2511.02805](https://arxiv.org/abs/2511.02805) — MemSearcher (memory inefficiency)
- [2512.13059](https://arxiv.org/abs/2512.13059) — Iterative retrieval for long-form QA
- [2601.04888](https://arxiv.org/abs/2601.04888) — Inaccurate intermediate queries
- [2510.12979](https://arxiv.org/abs/2510.12979) — High entropy in planning tokens
- [2602.13855](https://arxiv.org/abs/2602.13855) — Auditability bottleneck

---

## Confidence Assessment

**High confidence** on: The general patterns (structured output, confidence scoring, separation of concerns) are well-supported by the literature across all six architectures.

**Medium confidence** on: Specific architecture performance rankings — these depend heavily on domain, query type, and model capability. The comparison table provides guidance but not guarantees.

**Low confidence** on: Optimal parameter choices (tree depth, confidence thresholds, pass counts) — these require empirical tuning per use case and are not well-studied in the literature.
