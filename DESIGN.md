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

Compiles project, mission, and slice intent into an idempotent Kanban task graph. Workflow groups and dependency edges define the topology. Each task receives its full intent chain, acceptance criteria, proof contract, scope boundaries, and handoff target.

### Kanban adapter

Uses supported Hermes Kanban CLI commands to create or reuse one board per rig and to apply tasks. Hermes Kanban owns persistence, dispatch, claims, retries, heartbeats, task history, reviews, and worker process tracking.

### Handoff service

Uses Kanban completion summaries, structured metadata, run summaries, and attachments to transfer completed work, verified evidence, failed attempts, unresolved decisions, and the recommended next action. Handoffs travel along dependency edges only: an implementation task completes with evidence to release its pre-created reviewer child; the reviewer reads the parent result and evidence, then completes the review or blocks it with actionable findings; a dependent slice receives parent summaries; a blocked human acceptance task is resolved by the operator, never by another agent.

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

Hermes Factory compiles and audits work. It never keeps idle interactive agents alive and never supervises worker processes: real execution and process lifetime belong to the Hermes gateway dispatcher. The factory owns declarative state only. Operators trigger execution explicitly with `hermes kanban dispatch`; `status` reports health without starting any task. Human acceptance decisions are not delegated to agents.

### Health monitor

Combines Hermes Kanban diagnostics with deterministic audits for missing proof and stalled work. Factory embeds the declared proof contract in each task body, checks commands and changed files against structured metadata on the latest successful Hermes run, and checks required artifacts against persisted Kanban attachment records. It does not implement separate worker liveness or process supervision.

### Recovery reconciler

`hermes-factory reconcile` is a short, deterministic policy reaction over authoritative Kanban state. It lists blocked cards, considers only cards created by `hermes-factory`, reads each card's event history, and reacts only to a terminal `gave_up` event whose trigger is `timed_out`. Manual/product blocks and reconciler-created cards are ignored.

For each eligible timeout, Factory creates or reuses one high-priority coordinator card. Its idempotency key includes the project, blocked task id, and `gave_up` event timestamp, so repeated cron ticks do not create duplicates. The recovery card has no dependency on the blocked task, because that edge would prevent it from running. It instructs the coordinator to inspect the task log and live diff, preserve valid partial work, create smaller sequential TDD implementation/review pairs, reconnect downstream dependencies, verify the replacement graph, and only then delete superseded cards.

The optional `--dispatch` flag calls the native Kanban dispatcher after confirming that a recovery card is `ready`. Factory still does not own claims, worker processes, retries, heartbeats, or run history. A durable Hermes no-agent cron job invokes the short reconciliation command; the long-running decomposition remains an ordinary Kanban-dispatched coordinator session.

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
