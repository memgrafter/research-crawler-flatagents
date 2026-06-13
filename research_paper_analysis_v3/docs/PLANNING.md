
 What I want is:

 1. download, extract, clean paper
 2. analyzer (all outputs come from this sequentially, and we will also be changing outputs
 3. judge(s?) - we can use a pinned context here, there is an api through my proxy to pin a kv cache in my serving backend
 4. repair possibly goes back to the analyzer

 I think the right way to do this is to have a prep machine, analyzer machine, and have the judge be a 2nd agent in the analyzer machine.  We already have download and extract here, so there's really just a clean step being added. Let's not focus on that for now, let's focus on the single
 analyzer machine with a single agent/chat and the judge agent.

 dont edit tickets, let's plan


 Good refinement. Let me think through this carefully.

 Analyzer Machine Design

 The key insight is: one agent, one conversation. The analyzer agent reads the paper and produces all outputs in a single exchange. Then the judge agent evaluates. If repair is needed, we go back to the analyzer — same agent, same conversation context — so it can revise.

 ### The KV Cache Pinning Angle

 This is important. If your proxy supports pinning KV cache, the conversation with the analyzer can be pinned across turns. That means:

 - Turn 1: Paper + system prompt → full analysis output (cache pinned)
 - Turn 2 (judge says REPAIR): Send judge feedback + repair instructions → analyzer revises (uses pinned cache of paper + prior output, only re-pays for the new delta)

 This is the real efficiency win — not just fewer calls, but reusing the expensive KV cache across turns. The paper text never needs to be re-processed.

 ### Machine Flow

 ```
   start → analyzer_write → judge → normalize_decision
     ├─ PASS → prepend_frontmatter → done
     ├─ FAIL → failed_incomplete
     └─ REPAIR (and !repair_attempted) → analyzer_repair → mark_repair → judge → ...
 ```

 Both analyzer_write and analyzer_repair use the same agent — same conversation. The difference is input: first turn gets paper + key_outcome + corpus_signals; repair turn gets judge feedback + repair instructions.

 ### Agent Design

 One agent, two modes (distinguished by prompt template, not by agent definition):

 - Write mode: System prompt = "You are a research analyst. Analyze this paper and produce all sections."
     - Input: title, abstract, cleaned_paper_text, key_outcome, corpus_signals, corpus_neighbors
     - Output: full structured analysis with all required sections
 - Repair mode: Same agent, same conversation, different user message
     - System prompt stays the same (or we adjust — see below)
     - Input: judge feedback + repair instructions
     - Output: revised full analysis

 ### The Prompt

 The analyzer prompt needs to produce everything that currently comes from 5 agents (why_hypothesis, reproduction, open_questions, limits_confidence, report_assembler). But unlike the current system where each agent produces its section independently, this one must produce a coherent whole —
 which is actually better for quality since the model can cross-reference.

 The prompt should specify all required sections with their structure (same headings as current judge expects):

 ```
   ## Quick Facts
   ## Executive Summary
   ## Method Summary
   ## Key Results
   ## Why This Works (Mechanism)
      ### Mechanism 1/2/3 (with claim, mechanism, assumption, evidence, break condition)
   ## Foundational Learning
   ## Architecture Onboarding
      - component map, critical path, tradeoffs, failure signatures, first 3 experiments
   ## Open Questions the Paper Calls Out
   ## Limitations
   ## Confidence
   ## Next Checks
 ```

 The prompt is longer than any single current prompt but that's fine — it's one system message.

 ### Judge Integration

 Two options:

 Option A: Judge as separate agent in same machine (what you suggested)

 Clean separation. Analyzer produces, judge evaluates. The machine routes between them. This is the standard approach and keeps concerns separate.

 Option B: Self-judging analyzer (consider but probably reject)

 The analyzer produces output AND self-evaluates. Saves one call but loses independent quality gate. Probably not worth it — the whole point of the judge is independent evaluation.

 Go with Option A. Judge is a separate agent in the same machine.

 ### Repair Flow — Back to Analyzer

 Here's where the KV cache matters most:

 ```
   analyzer_write (turn 1) → output cached, KV pinned
     ↓
   judge → REPAIR
     ↓
   analyzer_repair (turn 2, same conversation, uses pinned cache)
     ↓
   judge → PASS/FAIL
 ```

 The repair turn only sends the judge's feedback and repair instructions — not the paper again. The model sees its prior output in context and revises. This is much cheaper than re-sending everything.

 ### Key Differences from Current System

 1. No key_outcome separate step — the analyzer produces this as part of its output (or we keep it from prep, let's think about this)
 2. No report_assembler — the analyzer produces the final report directly in the right format
 3. No limits_confidence separate step — included in analyzer output
 4. Judge is co-located in the same machine — no warm-start / checkpointing needed between machines

 ### On key_outcome

 I think we should still produce key_outcome in prep. Reasons:
 - It's cheap (short output, cheap model)
 - It's useful as a seed/anchor for the analyzer — gives it a starting point
 - The current system already does this and it works fine

 The analyzer takes key_outcome as input context, same as the current why_hypothesis_writer does. This helps the model align its analysis with the prep phase understanding.

 ### Machine Config Sketch

 ```yaml
   spec: flatmachine
   data:
     name: analyzer-pipeline
     settings:
       max_steps: 10
     context:
       arxiv_id: input.arxiv_id
       execution_id: input.execution_id
       title: input.title
       authors: input.authors
       abstract: input.abstract
       cleaned_paper_text: input.cleaned_paper_text
       key_outcome: input.key_outcome
       corpus_signals: input.corpus_signals
       corpus_neighbors: input.corpus_neighbors
       reference_count: input.reference_count

       # Analyzer outputs
       analysis_output: null
       report_body: null

       # Judge state
       judge_decision_raw: null
       judge_decision: null
       repair_attempted: false

       # Final
       formatted_report: null
     agents:
       analyzer: ./agents/analyzer.flatagent.yml
       completeness_judge: ./agents/completeness_judge.flatagent.yml  # reuse existing
     states:
       start:
         type: initial
         transitions: [to: analyzer_write]

       analyzer_write:
         agent: analyzer
         input:
           title: context.title
           authors: context.authors
           abstract: context.abstract
           paper_text: context.cleaned_paper_text
           key_outcome: context.key_outcome
           corpus_signals: context.corpus_signals
           corpus_neighbors: context.corpus_neighbors
           reference_count: context.reference_count
         output_to_context:
           report_body: output.content
         transitions: [to: judge_report]

       judge_report:
         agent: completeness_judge
         input:
           report_body: context.report_body
         output_to_context:
           judge_decision_raw: output.content
         transitions: [to: normalize_judge_decision]

       normalize_judge_decision:
         action: normalize_judge_decision
         transitions:
           - condition: context.judge_decision == 'PASS'
             to: prepend_frontmatter
           - condition: context.judge_decision == 'FAIL'
             to: failed_incomplete
           - condition: context.repair_attempted == true
             to: failed_incomplete
           - to: analyzer_repair

       analyzer_repair:
         agent: analyzer
         input:
           judge_feedback: context.judge_decision_raw
           current_report: context.report_body
         output_to_context:
           report_body: output.content
         transitions: [to: mark_repair_attempted]

       mark_repair_attempted:
         action: set_repair_attempted
         transitions: [to: judge_report]

       prepend_frontmatter:
         action: prepend_frontmatter_v2
         transitions: [to: save_result]

       save_result:
         action: save_wrap_result
         transitions: [to: done]

       done:
         type: final

       failed_incomplete:
         type: final
 ```

 ### The Analyzer Prompt — Two Modes in One Agent

 This is the tricky part. The agent needs to handle both write and repair as different conversation turns. The system prompt stays constant. The user message changes based on mode.

 System prompt (constant):

 ```
   You are a technical research analyst. Produce a complete, structured analysis of
   the paper with all required sections listed below. Be precise, evidence-aware,
   and concrete. Never use markdown code fences.

   Required sections: [list all sections with brief descriptions]
 ```

 Write mode user message:

 ```
   Paper: {{title}} by {{authors}}

   Abstract: {{abstract}}

   Key outcome: {{key_outcome}}

   Corpus signals: {{corpus_signals}}

   Paper text:
   {{paper_text}}

   Produce the full analysis with all required sections.
 ```

 Repair mode user message:

 ```
   Your previous analysis has been reviewed. Here is the feedback:

   Judge feedback: {{judge_feedback}}

   Current report:
   {{current_report}}

   Revise your analysis to address the issues. Keep good content, fix weak sections.
   Return the full revised report.
 ```

 The agent sees its prior output in the conversation history (thanks to KV cache), so it can revise intelligently.

 ### What About the Prompt Template?

 We need a single .flatagent.yml that works for both modes. The difference is only in the user message template. We could:

 1. One prompt with conditional logic — include both templates and use a mode variable to select
 2. Two prompts, one agent — switch prompt based on state (if flatagents supports this)
 3. One prompt, embed mode context in input — always send both the paper and any judge feedback; if no feedback, it's write mode

 I think option 3 is cleanest — the analyzer always sees the full context. In write mode, judge_feedback is null/empty so it writes fresh. In repair mode, it has feedback so it revises. The system prompt handles both cases.

 ### Open Questions

 1. Max steps: Current expensive_machine has max_steps: 15, wrap has 25. With judge + repair loop, we need maybe max_steps: 10 (write → judge → repair → judge → done = 4 steps plus actions). But if the analyzer call is very long (large paper), retry backoffs could eat steps.
 2. Retry strategy: The analyzer call is the most expensive. Should it have more aggressive retries? Current system has backoffs [12, 24] for wrap agents.
 3. Can we do multiple repair passes? Currently it's one repair attempt max. With KV cache pinning, a second repair is cheap (just delta tokens). Worth considering but maybe keep it simple for v1.
 4. The judge prompt — current judge checks for specific section headers. Since the analyzer now produces the full report directly (not assembled from parts), the judge criteria stay the same. Good.
 5. What if we want the judge to be more nuanced? Currently it's PASS/REPAIR/FAIL. With a pinned context, could we have the judge produce structured feedback that the analyzer uses more effectively? That's a nice-to-have for later.

 Thoughts on this design?



 Like I said, it's going to be writing key outcome. That is a generated field, it will be generating it. I want to improve the design now. I think that the agent should be sequentially called in subsequent machine actions, so that it can see its analysis history.  So, we take each portion of the
 paper that used to be written by another agent and then it is instead written by this agent, sequentially.  Or, alternatively, we pin the context with the paper, and we fan out the j agent calls for each paper section.  Then the agent never sees the composed paper, since we would use the same
 pinned kv cache for the repair.  That is also workable, since the paper is really only like 1000 or 2000 (tbd) tokens.  Rather than what you have with the agent doing a big bang on the paper structure. We would also make the paper assembler non-agentic.


 This is a much better design. Let me think through both approaches clearly.

 The Key Insight

 The paper (cleaned) is only ~1000-2000 tokens. That's tiny. The real cost is the analysis output, which grows across turns. So the question is: what goes into the pinned KV cache?

 Approach A: Sequential Agent Calls (same conversation)

 ```
   analyzer: "Write key outcome"        → key_outcome in context
   analyzer: "Write why/mechanism"     → why in context
   analyzer: "Write reproduction"      → repro in context
   analyzer: "Write open questions"    → open_q in context
   analyzer: "Write limits/confidence" → limits in context
   judge: evaluate full report
   repair: "Fix X"                     → revised sections in context
 ```

 Pinned cache: paper + growing analysis. Each turn adds to the conversation. The agent sees all prior sections when writing each new one, so it can cross-reference and stay consistent.

 Problem: By the time you get to limits/confidence (turn 5), the context has paper + 4 prior sections = potentially 8-10K tokens before the section even starts. Each subsequent turn gets more expensive because the cache grows. Also, if repair needs to fix section 2, the agent has to rewrite it
 but can't "delete" prior turns from context — the old version is still there.

 Approach B: Pinned Paper + Fan-Out Section Calls

 ```
   pin KV cache: [paper text]

   analyzer: "Write key outcome"         → cached paper + short prompt
   analyzer: "Write why/mechanism"       → cached paper + short prompt
   analyzer: "Write reproduction"        → cached paper + short prompt
   analyzer: "Write open questions"      → cached paper + short prompt
   analyzer: "Write limits/confidence"   → cached paper + short prompt

   non-agentic assembler → compose sections into report_body
   judge: evaluate report_body
   repair: "Fix section X, here's the feedback..." → cached paper + repair delta
 ```

 Pinned cache: just the paper (~1-2K tokens). Every analyzer call pays for the paper once (from cache) plus its own short prompt. The agent never sees prior sections — each section is independent.

 This is better because:
 1. Constant context size per call — no growing conversation. Each section call is ~2K (paper) + ~1K (prompt) = ~3K input, same cost every time.
 2. No stale content in context — repair only rewrites the broken section, doesn't have to deal with old versions still in the conversation.
 3. Parallelizable — sections that don't depend on each other could theoretically run in parallel (key_outcome is independent; why/repro/open_q all just need paper + key_outcome).
 4. Non-agentic assembler — just string concatenation with section headers. No LLM call needed to compose the report.

 The Dependency Graph Between Sections

 Not all sections are independent:

 ```
   key_outcome ──┬── why/mechanism (needs key_outcome)
                 ├── reproduction   (needs key_outcome)
                 ├── open_questions (needs key_outcome + abstract)
                 └── limits/confidence (needs key_outcome + why + repro)
 ```

 So the real flow with fan-out is:

 ```
   Turn 1:  analyzer → key_outcome          (paper only)
   Turn 2-4: analyzer → why, repro, open_q  (paper + key_outcome, can be parallel)
   Turn 5:  analyzer → limits/confidence    (paper + key_outcome + why + repro)
 ```

 With KV cache pinning, turns 2-4 are cheap because they share the same cached paper. Turn 5 is slightly more input but still bounded.

 Machine Flow with Fan-Out

 ```yaml
   states:
     start → write_key_outcome

     write_key_outcome:
       agent: analyzer
       mode: key_outcome
       transitions: [to: write_sections]

     write_sections:
       machine:
         - write_why
         - write_reproduction
         - write_open_questions
       mode: settled  # all three complete

     write_why / write_reproduction / write_open_questions:
       agent: analyzer
       mode: why | reproduction | open_questions
       input: paper + key_outcome

     write_limits_confidence:
       agent: analyzer
       mode: limits_confidence
       input: paper + key_outcome + why + repro

     # Non-agentic assembly
     assemble_report:
       action: assemble_report_sections  # just string concat

     judge → normalize → PASS/REPAIR/FAIL
     REPAIR → repair_section (back to analyzer with pinned cache)
 ```

 The Analyzer Agent Design

 One agent, multiple modes selected by the machine action. Each mode uses a different user prompt template but the same system prompt.

 The system prompt is minimal — it just sets the persona:

 ```
   You are a technical research analyst for ML papers. Be precise and evidence-aware.
   Never use markdown code fences.
 ```

 Each mode has its own targeted prompt (much shorter than the current monolithic prompts because the paper is already in context):

 key_outcome mode: "Write key outcome section. Include: problem, core method, primary results with metrics."

 why mode: "Write why/mechanism section with 3 mechanisms (claim, mechanism, assumption, evidence anchors, break condition), foundational learning (3-6 concepts), and architecture onboarding (component map, critical path, tradeoffs, failure signatures, first 3 experiments)."

 reproduction mode: "Write reproduction section with: what is directly specified, minimum viable plan, unknowns blocking reproduction, common failure modes."

 open_questions mode: "Write 3-5 open questions, each with: question, basis in paper (explicit/inferred), why unresolved, what evidence would resolve it."

 limits_confidence mode: "Write limits/confidence/next checks section. Include major uncertainties, confidence labels (High/Medium/Low) for claim clusters, exactly 3 concrete next validation checks."

 repair mode: "The judge found issues in this report. Fix the specific sections mentioned. Here's the feedback: ..."

 The Non-Agentic Assembler

 This is just a hook action that concatenates sections with headers:

 ```python
   def assemble_report_sections(context):
       parts = [
           f"# {context['title']}",
           "## Quick Facts\n- arXiv: ...\n- Authors: ...\n- References: ...",
           f"## Executive Summary\n{context['key_outcome']}",  # key_outcome IS the exec summary
           f"## Key Results\n{...}",
           f"## Why This Works (Mechanism)\n{context['why_hypotheses']}",
           f"## Foundational Learning\n{...}",
           f"## Architecture Onboarding\n{...}",
           f"## Open Questions the Paper Calls Out\n{context['open_questions']}",
           f"## Limitations\n{...}",
           f"## Confidence\n{...}",
           f"## Next Checks\n{...}",
       ]
       context["report_body"] = "\n\n".join(parts)
 ```

 No LLM call. No prompt. Just string formatting. Saves one whole agent call and eliminates assembly errors.

 Repair with Pinned Cache

 This is where it really shines:

 ```
   analyzer (repair mode):
     - Paper in pinned cache (already paid for)
     - Input: judge feedback + the broken section(s) to fix
     - Output: revised section content only

   assembler re-composes → judge re-evaluates
 ```

 The repair call is tiny — no paper, no prior output, just "fix this section given this feedback." Very cheap.

 Token Budget Comparison

 Current system per paper (rough):
 - 7 agent calls × ~14K input tokens each = ~98K input tokens
 - Plus growing context in some calls

 Fan-out system per paper:
 - key_outcome: ~2K (paper) + 0.5K prompt = ~2.5K
 - why + repro + open_q (parallel): 3 × (~2K + 1K) = ~9K
 - limits: ~2K + 2K (key_outcome + why + repro) = ~4K
 - judge: ~4K
 - repair if needed: ~1K (just feedback + broken section)
 - Total: ~20.5K input tokens vs ~98K

 ~80% reduction in input tokens, and each call is bounded — no growing context.

 What I Need to Clarify

 1. Does your KV cache pinning API support per-execution pinning? i.e., can you pin a prefix for execution A and execution B independently? This matters because multiple papers run concurrently.
 2. Can the fan-out sections (why/repro/open_q) truly be parallel? With pinned cache, they share the same paper prefix but have independent prompts. If the serving backend supports concurrent reads from the same pinned cache, these can run in parallel — same as the current expensive_machine
    parallel call.
 3. Should limits/confidence also be parallel with the other sections? Currently it depends on key_outcome + why + repro. But with the pinned cache approach, we could pass all available context and let the model handle partial inputs (e.g., if why isn't ready yet, it still writes what it can).
    Probably not worth the complexity — sequential is fine since it's one call.
 4. How does the repair mode know which section to fix? The judge currently just says PASS/REPAIR/FAIL with no structured feedback. For effective repair with pinned cache, we'd want the judge to say "Foundational Learning is weak, Architecture Onboarding missing component map." Otherwise the
    repair agent has to re-read the whole report and figure out what's wrong — which defeats the purpose of a small repair call.

