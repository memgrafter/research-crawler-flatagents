---
arxiv_id: "N/A"
core_contribution: "Analysis of Recursive Language Model (RLM) strategies for deep research, proposing multiple architectures that exploit REPL-based symbolic recursion and shared state to scale beyond context windows. Builds on 2512.24601 RLM paper with deep research as the target application."
tags: ["rlm", "recursive-language-models", "deep-research", "repl", "shared-state", "test-time-scaling"]
---

# RLM Strategies for Deep Research

### Problem Space

Deep research — generating comprehensive, well-organized reports requiring multi-step information gathering and synthesis — is fundamentally a **long-context problem**. The model must: (1) understand the research question, (2) decompose it into sub-questions, (3) gather evidence across many sources, (4) synthesize findings, and (5) produce a structured output. Each step adds context, and naive approaches saturate the attention window.

Recursive Language Models ([2512.24601](https://arxiv.org/abs/2512.24601)) solve this by treating prompts as **environment variables in a REPL** — offloading state to external memory so only metadata enters the neural network's attention. This enables symbolic recursion and programmatic decomposition of inputs up to two orders of magnitude beyond context limits.

This analysis proposes **five RLM-based architectures for deep research**, each exploiting different RLM capabilities: shared state, symbolic recursion, variable accumulation, and code-driven control flow.

---

## Core RLM Primitives

Before the architectures, the primitives they compose:

1. **Symbolic Handle to Prompt**: The long context (research question + gathered evidence) lives as a `context` variable in a REPL. The model receives only constant-size metadata (length, prefix, access methods). This is the key escape from context window constraints.

2. **Symbolic Recursion via `llm_query()`**: Code execution can include sub-LLM calls, enabling programmatic decomposition. Unlike autoregressive delegation (which hits output length limits), code-based recursion launches arbitrarily many sub-calls bounded only by compute.

3. **Variable-Based State Accumulation**: Intermediate results stored in REPL variables enable unbounded output construction. The system iterates until a `Final` variable is set, accumulating findings across iterations.

4. **Shared REPL State**: Multiple RLM instances share the same REPL environment — variables persist across sub-calls, enabling coordination without passing full context between agents.

---

## Architecture 1: Recursive Evidence Explorer (REE)

### Overview

The primary architecture for general-purpose deep research. A root RLM decomposes the research question into a tree of sub-queries, each explored by recursive sub-calls that share evidence in the REPL state.

### Mechanism

```python
# Root RLM receives: research_question, context = ""
# It generates code like:

context['question'] = "What are the latest advances in X?"
context['findings'] = []
context['sources'] = []

# Decompose into sub-questions
sub_questions = llm_query(
    "Decompose this research question into 5-8 focused sub-queries",
    f"Question: {context['question']}"
)

# Explore each sub-question recursively
for sq in sub_questions:
    result = llm_query(
        "Research this sub-question and return structured findings",
        f"Sub-question: {sq}\nExisting findings: {context['findings'][:500]}"
    )
    context['findings'].append(result)
    context['sources'].extend(result.get('sources', []))

# Synthesize into final report
Final = llm_query(
    "Synthesize all findings into a comprehensive research report",
    f"Findings: {context['findings']}"
)
```

### Why It Works for Deep Research

- **Evidence accumulation**: Each sub-call adds to `context['findings']` without saturating the root's context window.
- **Cross-question awareness**: Sub-calls see a summary of existing findings, avoiding redundant exploration.
- **Unbounded depth**: The recursion depth is limited only by compute, not context.

### Key Design Decisions

- **Summary passing, not full context**: Sub-calls receive `context['findings'][:500]` — a compressed view of existing evidence. This prevents sub-call context overflow while maintaining cross-question awareness.
- **Structured findings schema**: Each sub-call returns `{claim, evidence, sources, confidence}` — enabling programmatic synthesis rather than free-text merging.
- **Convergence detection**: The root RLM checks if new sub-calls are adding novel information; if 3 consecutive calls add <5% new claims, it stops and synthesizes.

### Limitations

- Sub-call hallucinations propagate into `context['findings']`. A verification pass is needed before synthesis.
- The summary passing (truncation to 500 chars) may lose nuance across sub-calls.

---

## Architecture 2: Incremental Report Builder (IRB)

### Overview

Instead of gathering evidence first then synthesizing, this architecture builds the report **incrementally** — each RLM call writes a section of the final report, and subsequent calls read and revise previous sections.

### Mechanism

```python
# Root RLM generates code that builds the report section by section

context['report'] = {
    'title': '',
    'sections': [],
    'references': []
}

# Step 1: Generate outline
outline = llm_query(
    "Generate a detailed outline for a research report on this topic",
    f"Topic: {context['question']}"
)
context['report']['title'] = outline['title']
context['report']['sections'] = [{'title': s['title'], 'content': ''} for s in outline['sections']]

# Step 2: Write each section, with access to the growing report
for i, section in enumerate(context['report']['sections']):
    # Pass only adjacent sections for context, not the whole report
    prior = context['report']['sections'][max(0,i-1):i] if i > 0 else []
    content = llm_query(
        f"Write section: {section['title']}\nPrior sections summary: {prior}",
        "Use your internal knowledge and web search to gather evidence"
    )
    context['report']['sections'][i]['content'] = content

# Step 3: Revision pass — read the whole report and suggest improvements
revisions = llm_query(
    "Review this full report and identify gaps, contradictions, or missing perspectives",
    f"Report: {context['report']}"
)

# Step 4: Apply revisions
for rev in revisions:
    if rev['type'] == 'gap':
        new_section = llm_query(
            f"Write a section addressing this gap: {rev['description']}",
            "Gather evidence and write the content"
        )
        context['report']['sections'].append(new_section)

Final = context['report']
```

### Why It Works for Deep Research

- **Progressive output**: After each section, there's a usable partial report — graceful under time constraints.
- **No evidence accumulation bottleneck**: Each section is written directly, not stored as intermediate evidence first.
- **Revision pass catches errors**: The review step identifies gaps before finalization.

### Key Design Decisions

- **Adjacent-section context**: When writing section N, only sections N-1 and N+1 are passed (not the whole report). This keeps sub-call contexts small while maintaining coherence.
- **Structured revision schema**: Revisions return `{type: gap|contradiction|missing-perspective, description, section_index}` — enabling programmatic application.

### Limitations

- Later sections may contradict earlier ones if the model's knowledge shifts during the process.
- The revision pass requires reading the full report, which could exceed context limits for very long reports.

---

## Architecture 3: Debate-and-Synthesize RLM (DSR)

### Overview

Multiple RLM sub-calls are prompted with different analytical perspectives. They write to shared REPL state, and a synthesizer reconciles the differences.

### Mechanism

```python
context['perspectives'] = {}
context['critiques'] = {}

# Define perspectives
perspectives = [
    {'name': 'optimist', 'prompt': 'Focus on opportunities and positive developments'},
    {'name': 'skeptic', 'prompt': 'Focus on limitations, risks, and counterarguments'},
    {'name': 'pragmatist', 'prompt': 'Focus on practical implications and feasibility'},
]

# Each perspective writes its analysis to shared state
for p in perspectives:
    context['perspectives'][p['name']] = llm_query(
        f"Analyze this topic from a {p['name']} perspective.\n{p['prompt']}",
        f"Topic: {context['question']}"
    )

# Cross-examination: each perspective critiques the others
for p in perspectives:
    others = {k: v for k, v in context['perspectives'].items() if k != p['name']}
    context['critiques'][p['name']] = llm_query(
        f"Critique these analyses from a {p['name']} perspective",
        f"Your analysis: {context['perspectives'][p['name']]}\nOthers: {others}"
    )

# Synthesis
Final = llm_query(
    "Synthesize all perspectives and critiques into a balanced report. "
    "Explicitly note areas of disagreement.",
    f"Perspectives: {context['perspectives']}\nCritiques: {context['critiques']}"
)
```

### Why It Works for Deep Research

- **Reduces single-perspective bias**: The model's default tendency toward agreeable outputs is countered by structured disagreement.
- **Natural report structure**: Perspectives map directly to report sections ("Arguments for X", "Counterarguments", "Synthesis").
- **Shared state enables cross-referencing**: Each critique can reference specific claims from other perspectives.

### Key Design Decisions

- **Fixed perspective set**: 3 perspectives (optimist, skeptic, pragmatist) covers most research domains without excessive compute.
- **Critique schema**: `{target_perspective, claim, counter-claim, evidence}` — enables the synthesizer to track specific disagreements.
- **Synthesis includes disagreement map**: The final report explicitly flags areas where perspectives conflict.

### Limitations

- Debate can diverge into unproductive tangents without careful constraint on sub-call prompts.
- Requires 2N+1 LLM calls (N for perspectives, N for critiques, 1 for synthesis) — expensive for large N.

---

## Architecture 4: Iterative Gap-Closing RLM (IGC)

### Overview

The model generates a draft report, then iteratively identifies and fills gaps. Each iteration produces a better report, with convergence detected automatically.

### Mechanism

```python
context['report'] = None
context['gap_count_history'] = []
max_iterations = 10

# Initial draft from internal knowledge
context['report'] = llm_query(
    "Write a comprehensive research report on this topic. "
    "Mark uncertain claims with [UNCERTAIN] tags.",
    f"Topic: {context['question']}"
)

for iteration in range(max_iterations):
    # Gap analysis
    gaps = llm_query(
        "Analyze this report and identify:
         1. Claims marked [UNCERTAIN]
         2. Missing evidence for key claims
         3. Missing perspectives or counterarguments
         4. Logical holes in the argument",
        f"Report: {context['report']}"
    )
    
    context['gap_count_history'].append(len(gaps))
    
    # Convergence check: if gaps decreased <5% for 2 iterations, stop
    if len(context['gap_count_history']) >= 2:
        recent = context['gap_count_history'][-2:]
        if recent[1] > 0 and (recent[0] - recent[1]) / recent[0] < 0.05:
            break
    
    # Fill each gap with targeted research
    for gap in gaps:
        evidence = llm_query(
            f"Research this specific gap: {gap['description']}\n"
            f"Existing context from report: {gap['nearby_text']}",
            "Find evidence and write a replacement or addition"
        )
        # Apply the fix to the report in shared state
        if gap['type'] == 'uncertain':
            context['report'] = context['report'].replace(
                f"[UNCERTAIN]{gap['claim']}[/UNCERTAIN]",
                f"{evidence['claim']} [{evidence['source']}]"
            )
        elif gap['type'] == 'missing':
            # Insert new content at the indicated position
            context['report'] = insert_at(context['report'], gap['position'], evidence['content'])
    
    # Revision pass to smooth transitions after edits
    context['report'] = llm_query(
        "Review this report and fix any transition issues, contradictions, or formatting problems",
        f"Report: {context['report']}"
    )

Final = context['report']
```

### Why It Works for Deep Research

- **Progressive refinement**: Each iteration produces a usable output — the system is graceful under time constraints.
- **Targeted gap-filling**: The model doesn't waste tokens on blind exploration; it targets known gaps.
- **Self-correction**: Errors in early drafts are explicitly corrected, not accumulated.

### Key Design Decisions

- **Uncertainty tagging**: The initial draft marks uncertain claims with `[UNCERTAIN]` tags, making gap detection programmatic.
- **Convergence threshold**: 2 consecutive iterations with <5% gap reduction triggers stopping — prevents infinite loops.
- **Position-aware insertion**: Gaps include a `position` field so new content is inserted at the right place in the report.

### Limitations

- Early drafts may contain hallucinations that misdirect subsequent gap analysis (the model critiques its own errors).
- String-based report manipulation (`replace`, `insert_at`) is fragile — a structured document format would be more robust.

---

## Architecture 5: Knowledge Graph RLM (KGR)

### Overview

The model constructs a working knowledge graph in the REPL state, then generates the report from the graph structure. This leverages RLM's ability to accumulate complex structured state across iterations.

### Mechanism

```python
context['graph'] = {
    'entities': {},
    'relations': [],
    'evidence': {}
}

# Step 1: Extract key entities from the research question
entities = llm_query(
    "Identify key entities (people, concepts, organizations, events) relevant to this topic",
    f"Topic: {context['question']}"
)
for e in entities:
    context['graph']['entities'][e['id']] = {
        'name': e['name'],
        'type': e['type'],
        'description': '',
        'confidence': 0.5
    }

# Step 2: Enrich each entity with details (parallel sub-calls)
enrichment_calls = []
for eid, ent in context['graph']['entities'].items():
    enrichment_calls.append(llm_query(
        f"Research this entity and provide detailed information",
        f"Entity: {ent['name']} ({ent['type']})\nTopic context: {context['question']}"
    ))

for eid, enrichment in zip(context['graph']['entities'].keys(), enrichment_calls):
    context['graph']['entities'][eid]['description'] = enrichment['description']
    context['graph']['entities'][eid]['confidence'] = enrichment['confidence']
    context['graph']['evidence'][eid] = enrichment.get('sources', [])

# Step 3: Infer relationships between entities
relations = llm_query(
    "Identify relationships between these entities",
    f"Entities: {context['graph']['entities']}"
)
for r in relations:
    context['graph']['relations'].append({
        'from': r['from'],
        'to': r['to'],
        'type': r['type'],  # causal, temporal, hierarchical, etc.
        'description': r['description'],
        'confidence': r['confidence']
    })

# Step 4: Identify missing information via graph sparsity analysis
sparse_areas = llm_query(
    "Analyze this knowledge graph and identify:
     1. Entities with few connections (isolated topics)
     2. Missing relationships between related entities
     3. Low-confidence claims needing verification",
    f"Graph: {context['graph']}"
)

# Step 5: Enrich sparse areas
for area in sparse_areas:
    new_info = llm_query(
        f"Research this missing area: {area['description']}",
        "Find entities, relationships, and evidence"
    )
    # Add to graph state
    for e in new_info.get('entities', []):
        context['graph']['entities'][e['id']] = e
    for r in new_info.get('relations', []):
        context['graph']['relations'].append(r)

# Step 6: Generate report from graph structure
Final = llm_query(
    "Generate a comprehensive research report from this knowledge graph. "
    "Follow the graph's natural structure (topological order) for organization.",
    f"Graph: {context['graph']}"
)
```

### Why It Works for Deep Research

- **Explicit knowledge representation**: The graph makes the model's internal reasoning traceable and auditable.
- **Natural organization**: Graphs encode relationships explicitly, which is exactly what deep research requires.
- **Incremental improvement**: More compute = more entities and edges, directly improving coverage.
- **Sparsity analysis**: The model can programmatically identify missing information by analyzing graph structure.

### Key Design Decisions

- **Confidence-aware edges**: Every relationship includes a confidence score; low-confidence edges are flagged for the user.
- **Topological ordering**: Report generation follows the graph's natural structure, not arbitrary section order.
- **Parallel enrichment**: Entity research calls can run in parallel since they're independent.

### Limitations

- Graph construction can diverge if the model hallucinates entities (error propagation through shared state).
- Complex graph queries require custom code for traversal and sparsity analysis.
- The final report generation step must read the entire graph, which could exceed context limits for very large graphs.

---

## Architecture Comparison

| Architecture | Best For | Compute Cost | Error Isolation | Output Quality |
|---|---|---|---|---|
| **REE** | Complex multi-faceted topics | Medium (O(sub-queries)) | High (branch isolation) | Very High (hierarchical synthesis) |
| **IRB** | Well-scoped reports, time-constrained | Low-Medium (O(sections)) | Medium (adjacent-section context) | High (progressive refinement) |
| **DSR** | Controversial topics, balanced analysis | High (2N+1 calls) | High (cross-examination) | High (perspective diversity) |
| **IGC** | Fact-heavy domains, self-correction | Medium-High (O(iterations * gaps)) | Low-Medium (iterative correction) | Very High (gap-driven refinement) |
| **KGR** | Relationship-heavy analysis, auditability | High (entity + relation enrichment) | Medium (graph propagation) | Very High (structured knowledge) |

---

## Shared REPL Tool Design for pi

To implement these architectures in pi, the shared REPL tool needs:

### Core Interface

```python
class RLM_REPL:
    """Shared state environment for RLM deep research."""
    
    def __init__(self):
        self.state = {}  # Persistent variables
        self.max_depth = 3  # Max recursion depth
        self.call_count = 0  # Sub-call counter (cost tracking)
        self.max_calls = 50  # Safety limit
    
    def llm_query(self, system_prompt: str, user_content: str) -> dict:
        """Make a sub-LLM call with access to shared state."""
        if self.call_count >= self.max_calls:
            raise RuntimeError(f"Max sub-calls ({self.max_calls}) exceeded")
        
        self.call_count += 1
        # The sub-call receives: system prompt + user content + state summary
        # Returns: structured result merged into shared state
        ...
    
    def get_state_summary(self, max_chars: int = 2000) -> str:
        """Compress shared state for passing to sub-calls."""
        ...
    
    def set_final(self, value: any):
        """Signal completion by setting the Final variable."""
        self.state['Final'] = value
```

### Key Design Considerations

1. **State serialization**: The REPL state must be serializable for passing between sub-calls. Python dicts are sufficient for most use cases.
2. **Cost tracking**: Each `llm_query()` increments a counter; the root RLM can monitor cost and stop early if needed.
3. **Recursion depth limit**: Prevent infinite recursion with a configurable depth limit (default 3).
4. **State compression**: Sub-calls receive a compressed view of state, not the full history — prevents context overflow in sub-calls.
5. **Structured output enforcement**: All sub-calls must return structured data (dicts), not free text — enables programmatic manipulation by subsequent code.

---

## Key Patterns Across RLM Architectures

### 1. Code-Driven Control Flow, Not Prompt-Driven

The fundamental shift from conventional deep research agents: the model generates **code** that controls execution flow, not just text responses. This enables:
- Loops over sub-queries without verbose prompt engineering
- Conditional branching based on intermediate results
- Programmatic state manipulation (append, replace, insert)

### 2. Shared State as Coordination Mechanism

Multiple RLM sub-calls coordinate through shared REPL variables, not through passing full context between agents. This is the key insight from [2512.24601](https://arxiv.org/abs/2512.24601): **offload state to external memory**.

### 3. Convergence Detection Is Essential

All architectures need a stopping criterion:
- REE: <5% new claims in 3 consecutive sub-calls
- IRB: All sections written + revision pass complete
- DSR: Fixed number of perspectives (no convergence needed)
- IGC: <5% gap reduction for 2 iterations
- KGR: Graph sparsity below threshold

### 4. Structured Output at Every Step

Free-text sub-call outputs are impossible to compose programmatically. Every sub-call must return structured data (claims, evidence, confidence scores) that subsequent code can manipulate.

---

## Practical Recommendations

### Choosing an Architecture

- **General purpose**: Start with **REE** — it's the most general and has the clearest structure.
- **Time-constrained**: Use **IRB** — early sections produce usable output.
- **Controversial topics**: Use **DSR** — debate reduces bias.
- **Fact-heavy domains**: Use **IGC** — iterative gap-filling self-corrects.
- **Relationship analysis**: Use **KGR** — graph structure reveals hidden connections.

### Recommended Stack for pi Implementation

1. **Model**: Claude Sonnet 4 or GPT-4o — both have strong code generation and structured output compliance.
2. **REPL**: Python with `exec()` in a sandboxed environment — the paper's approach uses a simple REPL with variable persistence.
3. **Safety**: Rate limiting on sub-calls, recursion depth limits, and max call counts prevent runaway costs.
4. **Cost tracking**: Each `llm_query()` increments a counter; the root RLM can stop early if budget is exceeded.

### Token Budgeting

- Allocate ~15% to code generation (the root RLM writing control flow)
- Allocate ~60% to sub-call content generation (research, writing, analysis)
- Allocate ~25% to synthesis and revision passes
- Use a hard token limit per sub-call with graceful degradation

---

## Open Research Questions

1. **Optimal state compression**: How much of the REPL state should be passed to each sub-call? Too little = redundant work; too much = context overflow. The paper uses metadata-only passing, but deep research may need richer state sharing.

2. **Deeper recursion depths**: The paper limits to depth=1. Would depth=3 (root → sub-RLM → sub-sub-RLM) improve deep research quality for complex topics? Error propagation is the concern.

3. **Asynchronous sub-calls**: All architectures above use sequential sub-calls. Parallel execution of independent sub-calls (e.g., entity enrichment in KGR) could dramatically reduce wall-clock time.

4. **Cross-architecture composition**: Can REE + IGC produce better results than either alone? Use REE for evidence gathering, then IGC for iterative refinement of the synthesized report.

5. **RLM training for deep research**: The paper demonstrates fine-tuning on 1,000 trajectories. Would domain-specific fine-tuning (e.g., on scientific paper analysis) improve RLM performance for deep research?

6. **State verification**: How to detect and correct corrupted REPL state? A hallucinated entity in KGR propagates through all subsequent operations.

---

## References

- [2512.24601](https://arxiv.org/abs/2512.24601) — Recursive Language Models (core RLM paper, REPL-based approach)
- [2508.12752](https://arxiv.org/abs/2508.12752) — Systematic examination of the deep research paradigm
- [2603.28361](https://arxiv.org/abs/2603.28361) — Unified definition and framework for "deep research"
- [2511.18303](https://arxiv.org/abs/2511.18303) — Hierarchical deep research agent
- [2602.13830](https://arxiv.org/abs/2602.13830) — Open-Ended Deep Research (lost-in-the-middle)
- [2511.18743](https://arxiv.org/abs/2511.18743) — RhinoInsight (error accumulation in deep research)
- [2601.17879](https://arxiv.org/abs/2601.17879) — Self-Manager (sequential loop limitations)
- [2510.14337](https://arxiv.org/abs/2510.14337) — Stop-RAG (value-based stopping criteria)
- [2603.05912](https://arxiv.org/abs/2603.05912) — DRR factuality verification
- [2603.28376](https://arxiv.org/abs/2603.28376) — Error propagation in data synthesis
- [2512.13059](https://arxiv.org/abs/2512.13059) — Iterative retrieval for long-form QA
- [2601.12369](https://arxiv.org/abs/2601.12369) — TaxoBench (taxonomy organization in DRAs)
- [2602.07549](https://arxiv.org/abs/2602.07549) — Illusory completion in search agents
- [2511.02805](https://arxiv.org/abs/2511.02805) — MemSearcher (memory inefficiency from full history concatenation)

Full deep research reference list (~535 papers) at [`spot_analyses/deep_research_references.md`](../spot_analyses/deep_research_references.md).

---

## Confidence Assessment

**High confidence** on: The RLM paradigm (REPL-based state offloading) is technically sound and directly applicable to deep research. The five architectures are well-motivated by the paper's mechanisms.

**Medium confidence** on: Specific architecture performance rankings — these depend heavily on domain, query type, and model capability. The comparison table provides guidance but not guarantees.

**Low confidence** on: Optimal parameter choices (recursion depth, state compression ratios, convergence thresholds) — these require empirical tuning per use case and are not well-studied in the literature.
