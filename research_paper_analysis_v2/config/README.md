# Research Paper Analysis V2 Config

Strict FlatMachines/FlatAgents v4 config layout.

## Layout

- `profiles.yml`: `spec: flatprofile`, `spec_version: 4.1.0`; OpenAI Codex OAuth profiles for `gpt-5.4-mini`.
- `prompts/*.prompt.yml`: prompt-only files (`spec: prompt`, `spec_version: 4.1.0`) containing former FlatAgent `system` and `user` templates.
- `agents/*.flatagent.yml`: reusable FlatAgent wrappers (`spec: flatagent`, `spec_version: 4.1.0`) pointing at prompt files and profile names.
- `*_machine.yml`: FlatMachine configs (`spec_version: 4.1.0`) with v4 flatagent file references and named hook references.

## Hooks

The old `data.hooks: {module, class}` block was removed. Machines now use named hook references:

- `data.lifecycle_hooks: v2-hooks`
- `states.<state>.hooks: v2-hooks`

The Python runner must register `v2-hooks` to `research_paper_analysis_v2.hooks.V2Hooks` in the FlatMachines `HooksRegistry` before execution.

## Notes

- Temperature parameters were removed from all profile files.
- The old inline FlatAgent files (`*_writer.yml`, `report_assembler.yml`, `completeness_judge.yml`, `targeted_repair.yml`) were replaced by `prompts/` + `agents/`.
- `profiles_stepfun_test.yml` remains as a non-runtime reference profile and was normalized to `flatprofile` v4 shape with temperatures removed.
