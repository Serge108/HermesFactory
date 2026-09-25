# Hermes Factory

Windows-native control plane for persistent teams of Hermes agents.

The project adapts the useful operating ideas from OpenRig without introducing a second agent harness or requiring tmux/Linux.

## Core mapping

| Factory concept | Hermes primitive |
|---|---|
| Seat | Hermes profile plus a stable role |
| Occupant | A Kanban-dispatched Hermes session |
| Rig | One declarative rig plus one Hermes Kanban board |
| Pod | A named workflow and explicit task dependency edges |
| Mission and slice | A mission specification compiled into a Kanban task graph |
| Handoff | Kanban summaries, metadata, reviews, and attachments |
| Refocus | Complete project, mission, and slice intent in task context |
| Health | Kanban diagnostics plus deterministic proof audits |

## MVP

1. Load and validate `.hermes/rig.yaml`.
2. Map stable seats to existing Hermes profiles.
3. Create or reuse one Hermes Kanban board per rig.
4. Compile mission and slice YAML into an idempotent Kanban task graph.
5. Render the complete intent chain and worker protocol into each task.
6. Use Kanban summaries, metadata, reviews, and attachments for handoffs and proof.
7. Report health from Kanban diagnostics and deterministic proof audits.

## Operator workflow

```bash
uv sync --extra dev
uv run hermes-factory validate --rig .hermes/rig.yaml
uv run hermes-factory apply --rig .hermes/rig.yaml
uv run hermes-factory mission plan .hermes/missions/my-mission.yaml --rig .hermes/rig.yaml --json
uv run hermes-factory mission apply .hermes/missions/my-mission.yaml --rig .hermes/rig.yaml --json
uv run hermes-factory status --rig .hermes/rig.yaml
uv run hermes-factory report --rig .hermes/rig.yaml
hermes kanban --board <board-slug> dispatch --dry-run --json
hermes kanban --board <board-slug> watch
hermes dashboard --no-open
```

`hermes-factory` compiles and audits work; it does not keep idle interactive agents alive. Real execution is owned by the Hermes gateway dispatcher, which spawns and supervises Kanban task sessions. `status` exits non-zero when it finds critical or error findings, while `report` is a read-only metrics projection. Factory does not ship a second web dashboard. Use the native Hermes Kanban dashboard and its live task-event stream to observe task links, worker activity, comments, and handoffs; use `hermes kanban watch` for the same orchestration activity in a terminal.

## Handoff rules

Each task carries the full intent chain, acceptance criteria, proof contract, and scope boundaries in its body. Completed work moves along parent edges through Kanban reviews, summaries, structured metadata, and attachments:

```text
Implementation occupant
  → completes its implementation card with summary and metadata
  → completion releases the pre-created reviewer child
  → reviewer independently reads the parent result and evidence
  → reviewer completes the review or blocks it with actionable findings
  → dependent slice receives parent summaries
  → blocked human acceptance task is resolved by the operator
```

Implementation and review results must publish structured completion metadata:

```json
{
  "changed_files": ["path/to/file.py"],
  "commands": [{"command": "uv run pytest -q", "exit_code": 0}],
  "limitations": []
}
```

Required artifacts must be uploaded as Hermes Kanban attachments. Self-reported artifact paths in completion metadata do not satisfy the proof audit.

## Constraints

- Windows-native: no tmux, WSL, Docker, or POSIX shell dependency.
- Hermes remains the only agent runtime and source of session truth.
- Persistent identity belongs to a seat, not to one model context.
- Repository artifacts and Hermes Kanban records are authoritative; agent chat is not.
- Human decisions cannot be silently delegated to another agent.

Hermes Kanban owns queue persistence, worker dispatch, retries, claims, heartbeats, and task history. Hermes Factory must not duplicate those systems.

## Status

The first Hermes-native vertical slice is implemented and operationally smoke-tested: rig and mission validation, deterministic Kanban compilation, implementation/review/acceptance topology, proof auditing, metrics, repeated idempotent apply, authoritative attachment proof, and disposable-board archival.
