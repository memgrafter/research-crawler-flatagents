---
id: rpav-0wkt
status: closed
deps: [rpav-8sq6]
links: [rpav-8sq6]
created: 2026-06-29T22:49:30Z
type: feature
priority: 1
assignee: trent
tags: [paper-machine, services, lifecycle]
---
# Build Paper Machine Manager with listener_os Pattern

Build the paper machine manager that orchestrates per-paper execution through the three-phase pipeline using the FlatMachines listener_os pattern.

## Context

This is the core infrastructure component for v3. Each paper gets its own paper machine that:
1. Parks on `wait_for` when waiting for a sub-machine to complete
2. Resumes via OS-activated dispatcher when the sub-machine signals completion
3. Transitions through phases: ranking → extraction → digest
4. Uses the single queue (SQLite) for state tracking and routing decisions

See epic rpav-8sq6 for full architecture context.

## Design Decisions

### listener_os Pattern (Not Custom Supervisor)
We use the FlatMachines signal system + OS activation instead of a custom supervisor process. The paper machine parks on checkpoints, sub-machines signal via `send_and_notify`, and the OS wakes the dispatcher to resume parked machines.

### Per-Paper Signal Channels
Each paper has its own signal channel: `paper/{arxiv_id}`. This allows independent wakeup per paper without cross-talk.

### Single Queue with State-Based Routing
One SQLite queue tracks paper state through all phases. The routing logic is:
```python
def route_paper(paper):
    if paper.state == 'pending':
        return 'rank_machine'
    elif paper.state == 'ranked' and paper.fmr_score >= 0.60:
        return 'extract_machine'
    elif paper.state == 'extracted':
        return 'digest_machine'
    else:
        return None
```

### Sub-Machines Write Directly to Storage
The extraction sub-machine writes docling JSON directly to `data/papers_docling_json/{arxiv_id}.json`. The paper machine only tracks the path, not the content.

## Implementation Tasks

### 1. Paper Machine YAML (paper_machine.yml)
```yaml
spec: flatmachine
spec_version: 4.1.0
data:
  name: paper-orchestrator
  persistence:
    enabled: true
    backend: sqlite
  context:
    paper_id: "{{ input.paper_id }}"
    title: "{{ input.title }}"
    abstract: "{{ input.abstract }}"
    phase: "pending"
    ranking_complete: false
    extraction_complete: false
    fmr_score: null
    docling_json_path: null
    digest_path: null
  machines:
    ranking_machine: ./ranking_machine.yml
    extraction_machine: ./extraction_machine.yml
    digest_machine: ./digest_machine.yml
  states:
    start:
      type: initial
      transitions:
        - to: check_phase

    check_phase:
      hooks: paper-manager
      action: check_current_phase_status
      transitions:
        # Ranking complete → launch extraction
        - condition: context.phase == 'ranking' and context.ranking_complete
          to: launch_extraction

        # Extraction complete → launch digest
        - condition: context.phase == 'extraction' and context.extraction_complete
          to: launch_digest

        # Nothing ready yet → park on signal
        - to: wait_for_wakeup

    wait_for_wakeup:
      wait_for: "paper/{{ context.paper_id }}"
      timeout: 86400  # 24h max wait
      output_to_context:
        phase_complete: "{{ output.phase_complete }}"
        result_path: "{{ output.result_path }}"
      transitions:
        - condition: context.phase == 'ranking' and context.phase_complete
          to: launch_extraction
        - condition: context.phase == 'extraction' and context.phase_complete
          to: launch_digest
        - to: done  # Digest complete → final

    launch_extraction:
      machine: extraction_machine
      input:
        paper_id: context.paper_id
        title: context.title
        abstract: context.abstract
      output_to_context:
        docling_json_path: output.docling_json_path
      on_error: phase_failed
      transitions:
        - to: check_phase

    launch_digest:
      machine: digest_machine
      input:
        paper_id: context.paper_id
        title: context.title
        abstract: context.abstract
        docling_json_path: context.docling_json_path
      output_to_context:
        digest_path: output.digest_path
      on_error: phase_failed
      transitions:
        - to: done

    phase_failed:
      hooks: paper-manager
      action: mark_phase_failed
      transitions:
        - to: failed

    done:
      type: final
      output:
        paper_id: context.paper_id
        fmr_score: context.fmr_score
        docling_json_path: context.docling_json_path
        digest_path: context.digest_path

    failed:
      type: final
      output:
        paper_id: context.paper_id
        error: context.last_error
        phase: context.phase
```

### 2. Paper Manager Hooks (paper_manager.py)
```python
class PaperManagerHooks(LoggingHooks):
    """Hooks for the paper machine manager."""

    def __init__(self, db_path: str, trigger_base: str):
        super().__init__()
        self._db_path = db_path
        self._trigger_base = trigger_base
        self._signal_backend = SQLiteSignalBackend(db_path=db_path)
        self._trigger_backend = FileTrigger(base_path=trigger_base)

    async def check_current_phase_status(self, context: dict) -> dict:
        """Check if the current phase sub-machine has completed."""
        paper_id = context['paper_id']
        phase = context['phase']

        # Read paper state from queue
        conn = sqlite3.connect(self._db_path)
        row = conn.execute(
            "SELECT state, fmr_score, docling_json_path FROM papers WHERE arxiv_id = ?",
            (paper_id,)
        ).fetchone()
        conn.close()

        if row:
            context['phase'] = row[0]
            if row[1]:
                context['fmr_score'] = row[1]
                context['ranking_complete'] = True
            if row[2]:
                context['docling_json_path'] = row[2]
                context['extraction_complete'] = True

        return context

    async def mark_phase_failed(self, context: dict) -> dict:
        """Mark the current phase as failed in the queue."""
        paper_id = context['paper_id']
        phase = context['phase']
        error = context.get('last_error', 'unknown')

        # Update paper state in queue
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "UPDATE papers SET state = 'failed', updated_at = ? WHERE arxiv_id = ?",
            (datetime.now(timezone.utc).isoformat(), paper_id)
        )
        conn.commit()
        conn.close()

        # Send failure signal to wake up any waiting machines
        await send_and_notify(
            signal_backend=self._signal_backend,
            trigger_backend=self._trigger_backend,
            channel=f"paper/{paper_id}",
            data={"phase_complete": False, "error": error},
        )

        return context

    async def on_action(self, state_name: str, action_name: str, context: dict) -> dict:
        handlers = {
            "check_current_phase_status": self.check_current_phase_status,
            "mark_phase_failed": self.mark_phase_failed,
        }
        handler = handlers.get(action_name)
        if handler:
            return await handler(context)
        return await super().on_action(state_name, action_name, context)
```

### 3. Sub-Machine Completion Hooks
Each sub-machine (ranking, extraction, digest) needs a `mark_complete` action that calls `send_and_notify`:

```python
# In ranking_machine hooks:
async def mark_ranking_complete(self, context: dict) -> dict:
    paper_id = context['paper_id']
    fmr_score = context['fmr_score']

    # Update queue with ranking result
    conn = sqlite3.connect(self._db_path)
    conn.execute(
        "UPDATE papers SET state = 'ranked', fmr_score = ?, updated_at = ? WHERE arxiv_id = ?",
        (fmr_score, datetime.now(timezone.utc).isoformat(), paper_id)
    )
    conn.commit()
    conn.close()

    # Signal the paper machine to resume
    await send_and_notify(
        signal_backend=self._signal_backend,
        trigger_backend=self._trigger_backend,
        channel=f"paper/{paper_id}",
        data={"phase_complete": True, "fmr_score": fmr_score},
    )

    return context
```

### 4. OS Activation Setup (systemd/launchd)
Use the listener_os pattern:
- FileTrigger watches `data/trigger` directory
- systemd/launchd starts `python -m flatmachines.dispatch_signals --once` on file change
- Dispatcher drains pending signals and resumes matching paper machines

### 5. Queue Schema (SQLite)
```sql
CREATE TABLE papers (
    arxiv_id TEXT PRIMARY KEY,
    title TEXT,
    abstract TEXT,
    fmr_score REAL,
    state TEXT DEFAULT 'pending',  -- pending | ranked | extracting | extracted | digesting | done | failed
    docling_json_path TEXT,         -- path to the docling JSON file
    digest_path TEXT,               -- path to the final digest
    updated_at TIMESTAMP
);
```

## Acceptance Criteria

1. Paper machine YAML is valid and can be loaded by FlatMachines
2. Paper machine parks on `wait_for` when waiting for sub-machine completion
3. Sub-machine completion signals resume the paper machine via OS-activated dispatcher
4. Paper state is tracked in the SQLite queue
5. Phase transitions work correctly: ranking → extraction → digest
6. Failed phases are marked in the queue and don't block other papers
7. 24h timeout prevents machines from parking forever
8. Per-paper signal channels prevent cross-talk between paper machines

## Dependencies

- rpav-8sq6 (epic) — architecture definition
- FlatMachines SDK with listener_os pattern support (already implemented)
- SQLite database for queue and checkpoint persistence
