# V2 Pipeline Architecture

```mermaid
graph TD
    subgraph PREP["Prep Machine - cheap model"]
        P_START([start]) --> P_DL[download_pdf]
        P_DL --> P_EXT[extract_text]
        P_EXT --> P_CORPUS[collect_corpus_signals]
        P_CORPUS --> P_KEY["key_outcome_writer (LLM)"]
        P_KEY --> P_SAVE[save_prep_result]
        P_SAVE --> P_DONE([done])
        P_DL -.->|error| P_FAIL_ACT[mark_execution_failed]
        P_EXT -.->|error| P_FAIL_ACT
        P_KEY -.->|error| P_FAIL_ACT
        P_FAIL_ACT --> P_FAILED([failed])
    end

    subgraph EXPENSIVE["Expensive Machine - pony-alpha parallel"]
        E_START([start]) --> E_PAR["parallel_expensive_writers (settled)"]
        E_PAR --> E_WHY["why_hypothesis_machine (LLM)"]
        E_PAR --> E_REPRO["reproduction_machine (LLM)"]
        E_PAR --> E_OQ["open_questions_machine (LLM)"]
        E_WHY --> E_UNPACK[unpack_expensive_results]
        E_REPRO --> E_UNPACK
        E_OQ --> E_UNPACK
        E_UNPACK --> E_SAVE[save_expensive_result]
        E_SAVE --> E_DONE([done])
        E_PAR -.->|error| E_FAIL_ACT[mark_execution_failed]
        E_FAIL_ACT --> E_FAILED([failed])
    end

    subgraph WRAP["Wrap Machine - cheap model"]
        W_START([start]) -->|has report_body| W_JUDGE
        W_START -->|has limits_confidence| W_TAGS
        W_START -->|default| W_LIMITS

        W_LIMITS["limits_confidence_writer (LLM)"] --> W_TAGS[derive_terminology_tags]
        W_TAGS --> W_ASSEMBLE["report_assembler (LLM)"]
        W_ASSEMBLE --> W_JUDGE["completeness_judge (LLM)"]
        W_JUDGE --> W_NORM{normalize_judge_decision}
        W_NORM -->|PASS| W_FRONT[prepend_frontmatter_v2]
        W_NORM -->|FAIL or repair_attempted| W_FAIL_ACT
        W_NORM -->|needs repair| W_REPAIR["targeted_repair (LLM)"]
        W_REPAIR --> W_MARK[mark_repair_attempted]
        W_MARK --> W_JUDGE
        W_FRONT --> W_SAVE_RESULT[save_wrap_result]
        W_SAVE_RESULT --> W_DONE(["done"])

        W_LIMITS -.->|error| W_FAIL_ACT[mark_execution_failed]
        W_ASSEMBLE -.->|error| W_FAIL_ACT
        W_JUDGE -.->|error| W_FAIL_ACT
        W_REPAIR -.->|error| W_FAIL_ACT
        W_FAIL_ACT --> W_FAILED([failed])
    end

    P_DONE ==>|"status: prepped"| E_START
    E_DONE ==>|"status: analyzed"| W_START
    W_DONE ==>|"status: done"| FINAL([Report Saved])

    style PREP fill:#e8f5e9,stroke:#2e7d32
    style EXPENSIVE fill:#fce4ec,stroke:#c62828
    style WRAP fill:#e8f5e9,stroke:#2e7d32
```

## LLM Calls Per Paper

| Stage | Model | Agent Calls | Notes |
|---|---|---|---|
| **Prep** | cheap | 1 | key_outcome_writer |
| **Expensive** | pony-alpha | 3 | why + reproduction + open_questions (parallel) |
| **Wrap** | cheap | 3-5 | limits + assembler + judge + (repair + re-judge) |
| **Total** | | **7-9** | Per paper, assuming 1 repair pass max |
