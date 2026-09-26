# Design

## Objective

Provide OpenRig-style persistent seats, topology, handoffs, refocus, and health monitoring for Hermes on native Windows.

## Architectural boundary

Hermes Factory does not wrap model APIs, emulate Hermes, supervise worker processes, or keep its own task database. It compiles declarative rig and mission files through supported Hermes CLI commands.

```text
Operator
   |
Hermes Factory CLI
   |
Rig and mission compiler
   |
Hermes Kanban CLI and dispatcher
   |
Hermes profiles and task sessions
```

## Initial components

### Rig loader

Loads and validates `.hermes/rig.yaml`: project identity, cwd, board, seats, profile bindings, roles, workflows, task-notification subscriptions, and policies.

### Seat resolver

Maps each stable seat and declared role to an existing Hermes profile. A Kanban-dispatched task session is the replaceable occupant of that seat.

### Mission compiler

Compiles project, mission, and slice intent into an idempotent Kanban task graph. Workflow groups and dependency edges define the topology. Each task receives its full intent chain, acceptance criteria, proof contract, scope boundaries, and handoff target. With `git_commit_policy: reviewed-slices`, every independent review releases a coordinator-owned local commit task; downstream slices depend on that commit rather than directly on the review.

### Kanban adapter

Uses supported Hermes Kanban CLI commands to create or reuse one board per rig and to apply tasks. Hermes Kanban owns persistence, dispatch, claims, retries, heartbeats, task history, reviews, and worker process tracking.

### Handoff service

Uses Kanban completion summaries, structured metadata, run summaries, and attachments to transfer completed work, verified evidence, failed attempts, unresolved decisions, and the recommended next action. Handoffs travel along dependency edges only: an implementation task releases its pre-created reviewer child; the reviewer completes or blocks; an optional local commit task records the reviewed slice; then dependent work or acceptance starts. `decision_authority: human` preserves a real operator gate. `decision_authority: coordinator` explicitly delegates in-scope decisions and acceptance to the coordinator without authorizing push or irreversible external writes.

Results publish this completion metadata contract:

```json
{
  "changed_files": ["path/to/file.py"],
  "commands": [{"command": "uv run pytest -q", "exit_code": 0}],
  "limitations": []
}
```

Required artifacts are persisted as Hermes Kanban attachments, not trusted from worker-reported paths.

### Safety boundaries

Hermes Factory compiles and audits work. It never keeps idle interactive agents alive and never supervises worker processes: real execution and process lifetime belong to the Hermes gateway dispatcher. The factory owns declarative policy and deterministic recovery reactions. Decision authority is explicit in the rig. Coordinator authority covers only accepted mission scope; it does not authorize push, secret publication, history rewriting, or irreversible external actions.

### Health monitor

Combines Hermes Kanban diagnostics with deterministic audits for missing proof and stalled work. Factory embeds the declared proof contract in each task body, checks commands and changed files against structured metadata on the latest successful Hermes run, and checks required artifacts against persisted Kanban attachment records. It does not implement separate worker liveness or process supervision.

### Recovery reconciler

`hermes-factory reconcile` is a short, deterministic policy reaction over authoritative Kanban state. It lists blocked cards, considers only cards created by `hermes-factory`, reads each card's event history, and reacts only to a terminal `gave_up` event whose trigger is `timed_out`. Manual/product blocks and reconciler-created cards are ignored.

For each eligible timeout, Factory creates or reuses one high-priority coordinator card. Its idempotency key includes the project, blocked task id, and `gave_up` event timestamp, so repeated cron ticks do not create duplicates. The recovery card has no dependency on the blocked task. It instructs the coordinator to inspect the log and live diff, preserve valid partial work, create smaller sequential TDD implementation/review pairs, reconnect downstream dependencies, verify the replacement graph, and complete with exact `replacement_task_ids`, `rewires`, `superseded_task_ids`, and `limitations` metadata.

Delegated workers are intentionally fenced from `unlink`, archive, purge, comment, and dispatch. A completed recovery plan is therefore finalized by the trusted no-agent reconciler: it validates the original target and replacement boundary, verifies replacement edges before mutation, unlinks only declared stale edges, reads each child back, archives and purges superseded cards, and records a finalization marker. Repeated passes are idempotent. The optional `--dispatch` flag then asks the native dispatcher to start recovery or newly released work. Factory still does not own claims, worker processes, retries, heartbeats, or run history.

### Stall taxonomy

`hermes-factory stall-taxonomy --json` exposes the stable operational contract. Every class records `reason_code`, detection, cause, recovery owner, recovery, authority, verification, fallback, regression test, and whether recovery is automatic. New operational incidents are not accepted as one-off fixes: they must add or refine a class, detector, recovery procedure, and regression test.

### Operator views

`status` and `report` are deterministic read-only projections of Hermes Kanban JSON. Factory deliberately does not provide another web dashboard. Live orchestration belongs to Hermes itself: the native Kanban dashboard streams task events and exposes links, comments, runs, and handoffs; `hermes kanban --board <slug> watch` provides the terminal event stream. This keeps observation attached to the authoritative task-event history instead of presenting a polling snapshot as live execution.

## Explicit non-goals for MVP

- Claude Code, Codex, Pi, or OpenCode runtime adapters.
- Linux or tmux compatibility.
- Arbitrary agent-to-agent terminal typing.
- Large autonomous swarms.
- A second memory or skills system.
- A second task queue, state database, dispatcher, or worker process supervisor.
- Automatic approval of product, security, or destructive decisions.

## First vertical slice

Given a valid rig, Hermes Factory resolves declared seats to existing profiles, creates or reuses one Hermes Kanban board, and compiles a mission slice into idempotent implementation, review, and acceptance tasks. Tests cover validation, Windows paths containing spaces, dependency topology, handoff context, and proof requirements.
