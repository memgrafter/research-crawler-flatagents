Handoff for next assistant:

We are in:

```text
/Users/trentrobbins/code/research_crawler/research_paper_analysis_v2
```

Current user concern:

The user asked for a **specific exception type in `~/code/flatmachines/sdk/python/flatmachines/` for the exact failure mode** that caused degraded reports to pass when child machines failed. I implemented something too generic:

```text
FlatMachineError
ChildMachineExecutionError
ParallelChildMachineError
```

User correctly pushed back:

> doesn't this not at all solve my request, instead introducing 2 general errors?

They want a handoff before compaction. Next assistant should probably revise the FlatMachines SDK changes to introduce a **specific exception type for swallowed/settled child machine failures**, not generic broad errors.

## What happened / root incident

In this repo’s v2 paper analysis pipeline, historical Qwen35 and GPT5.4 outputs were contaminated because:

1. `expensive_machine.yml` used parallel child machines:

```yaml
machine:
  - why_hypothesis_machine
  - reproduction_machine
  - open_questions_machine
mode: settled
```

2. Nested FlatAgent prompt path resolution failed:

```text
Referenced config file not found:
config/agents/./prompts/why_hypothesis_writer.prompt.yml
```

3. FlatMachines `settled` mode caught child exceptions and returned them as data:

```json
{
  "why_hypothesis_machine": {
    "_error": "Referenced config file not found: ...",
    "_error_type": "FileNotFoundError"
  }
}
```

4. Project hook `_unpack_expensive_results()` logged child errors, turned them into `None`, and saved `expensive_output` as valid:

```json
{
  "why_hypotheses": null,
  "reproduction_notes": null,
  "open_questions": null
}
```

5. Wrap then assembled final reports from summaries, not full paper text; judge checked only markdown structure, so degraded outputs passed.

Audited contamination:

```text
data/v2_executions_qwen35b_single_smoke.sqlite       10/10 contaminated
data/v2_executions_qwen35b_reasoning_10w.sqlite      10/10 contaminated
data/v2_executions_qwen35b_reasoning_50w10.sqlite   200/200 contaminated
data/v2_executions_gpt54mini_first_smoke.sqlite      10/10 contaminated
```

Clean DBs:

```text
data/v2_executions_qwen10.sqlite
data/v2_executions_stepfun_test.sqlite
data/v2_executions_gpt55_high_first.sqlite
data/v2_executions_llamacpp_first.sqlite
```

DeepSeek had 1/10 with one missing field.

## Current bad SDK change to review/revise

Location:

```text
/Users/trentrobbins/code/flatmachines/sdk/python/flatmachines
```

I added:

```text
flatmachines/exceptions.py
```

with:

```python
FlatMachineError
ChildMachineExecutionError
ParallelChildMachineError
```

and modified:

```text
flatmachines/flatmachine.py
flatmachines/__init__.py
```

Behavior I added:
- `ChildMachineExecutionError` wraps any child exception in parallel/foreach settled results.
- `ParallelChildMachineError` is raised if `settings.parallel_fallback == "error"` and any child failed.
- It serializes result payloads with `_error_type: ChildMachineExecutionError` and `_child_error_type: FileNotFoundError`.

User likely considers this too generic and not solving the exact request.

## Suggested better fix

Replace/rework the generic types into a **specific exception name for this failure mode**, e.g.:

```python
class SettledChildMachineFailure(FlatMachineError):
    """
    Raised or serialized when a child machine fails under settled parallel/foreach
    execution and would otherwise be returned as ordinary output data.
    """
```

Possible names:
- `SettledChildMachineFailure`
- `SettledChildMachineError`
- `SettledParallelChildFailure`
- `SettledChildFailureError`

Avoid vague `ChildMachineExecutionError` / `ParallelChildMachineError` unless needed.

Better design:

1. Keep a base:

```python
class FlatMachineError(Exception): ...
```

2. Add one specific exception:

```python
class SettledChildMachineFailure(FlatMachineError):
    def __init__(self, child_ref, child_id, original, *, item_key=None):
        ...
    def to_result(self):
        return {
            "_error": str(self),
            "_error_type": "SettledChildMachineFailure",
            "_child_error": str(original),
            "_child_error_type": type(original).__name__,
            "_child_machine": child_ref,
            "_child_id": child_id,
            ...
        }
```

3. In `_invoke_machines_parallel(... mode="settled")`, when `return_exceptions=True` returns an exception, serialize:

```python
failure = SettledChildMachineFailure(...)
results[machine_name] = failure.to_result()
```

4. If raising is desired, maybe raise a single specific aggregate with same name? Or avoid aggregate entirely:
   - If `parallel_fallback == "error"`, raise `SettledChildMachineFailure` for first failure.
   - This keeps the failure type specific and avoids adding an unnecessary broad aggregate.

Example:

```python
if failures and self.settings.get("parallel_fallback") == "error":
    raise next(iter(failures.values()))
```

5. Apply same behavior to `_invoke_foreach`.

6. Export only:

```python
FlatMachineError
SettledChildMachineFailure
```

from `__init__.py`.

7. Remove or rename the currently added generic exceptions.

## Why this is more aligned

The user asked for a specific exception type “for this going forwards,” i.e. for:

```text
settled parallel child machine failure being converted into ordinary output data
```

A specific serialized/raised type lets application code guard:

```python
if result.get("_error_type") == "SettledChildMachineFailure":
    fail hard
```

or catch:

```python
except SettledChildMachineFailure:
    ...
```

This is more directly tied to the historical incident.

## Current project-side state

In `research_paper_analysis_v2`:
- `config/profiles.yml` currently points to local llama.cpp profile:
  ```yaml
  provider: openai
  name: qwen3.6-28b-reap-iq3xxs-turbo3-35k
  base_url: http://127.0.0.1:8081/v1
  api_key: llama.cpp
  timeout: 600
  max_tokens: 12000
  disable_aiohttp_transport: true
  extra_body:
    chat_template_kwargs:
      enable_thinking: true
  ```
- Backup of GPT5.5 high profile:
  ```text
  config/profiles_gpt55_codex_oauth_high_backup.yml
  ```
- `config/expensive_machine.yml` currently modified to **sequential** local llama.cpp execution:
  ```text
  write_why_hypotheses -> write_reproduction_notes -> write_open_questions
  ```
- Backup:
  ```text
  config/expensive_machine_parallel_backup.yml
  ```
- Local llama.cpp first run completed:
  ```text
  data/v2_executions_llamacpp_first.sqlite
  llamacpp-qwen3.6-28b-reap-iq3xxs-turbo3-35k/
  ```
- Output evaluated as strong but below GPT5.5/DeepSeek/Qwen27:
  ```text
  local llama.cpp Qwen28 REAP score ~4/5 for 2601.02105
  ```

Do **not** continue model eval until the user asks. Focus next on revising the FlatMachines SDK exception design.

## Commands already run for SDK change

Compile check passed:

```bash
cd /Users/trentrobbins/code/flatmachines/sdk/python/flatmachines
python -m compileall flatmachines
```

Status of SDK changed files:

```text
 M sdk/python/flatmachines/flatmachines/__init__.py
 M sdk/python/flatmachines/flatmachines/flatmachine.py
?? sdk/python/flatmachines/flatmachines/exceptions.py
```

Need to revise these before committing.

## User preferences / tone

- User is frustrated by broad/generic fixes that do not exactly solve the problem.
- They want production-grade, specific, low-indirection changes.
- Avoid declaring success.
- Tell them what to check.
- Keep scope to:
  ```text
  ~/code/flatmachines/sdk/python/flatmachines/
  ```
  for this SDK exception work.
