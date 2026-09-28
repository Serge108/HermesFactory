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

Uses Kanban completion summaries, structured metadata, run summaries, and attachments to transfer completed work, verified evidence, failed attempts, unresolved decisions, and the recommended next action. Handoffs travel along dependency edges only: an implementation task releases its pre-created reviewer child; the reviewer completes or blocks; an optional local commit task records the reviewed slice; then dependent work or acceptance starts. `decision_authority: coordinator` is the default and delegates every in-scope implementation, product, recovery, and acceptance decision to the coordinator without authorizing push or irreversible external writes. Workers never request interim operator input: they choose the safest reversible or non-destructive option, record assumptions, and continue every independently safe step. `decision_authority: human` creates a real operator gate only when `human_acceptance: true` was explicitly declared; it does not authorize builders or reviewers to ask the operator questions during execution.

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

`hermes-factory reconcile` is a short, deterministic policy reaction over authoritative Kanban state. It lists blocked cards, considers only cards created by `hermes-factory` or proven to be Factory work, reads each card's event history, and classifies every one of them from Hermes's own terminal evidence: a `gave_up` event whose trigger is `timed_out` is `worker_timeout`; a breaker `gave_up` carrying the terminal-provider mark (`terminal_provider: true`, `trigger_outcome: crashed`, `sticky: true`) is `terminal_provider_park`; a review card blocked by its own worker with a reason beginning with the review-rejection contract prefix is `review_rejected`; any other blocked Factory card that is not an explicit, Factory-authored human gate becomes `unknown_operational_stall`. The explicit human gate outranks every recovery class, so a gate is never re-opened by a provider recovery. A card the Kanban surface blocked carries a task-scoped `blocked` event, and a card the failure breaker parked after retry exhaustion (`crashed`, `spawn_failed`, `reclaimed`) carries only a `gave_up` event; both are terminal evidence, so neither can hide from the catch-all. A pass skips the pre-declared human acceptance gate and answers reconciler-created cards with a reported failure, so an unexpected block cannot be silently ignored and an unmarked manual or product block reaches the coordinator instead of the operator.

Classification is isolated per card: a card whose terminal event is missing or malformed, or a malformed list entry without an id, is recorded and reported after the dispatch phase, so one anomalous record cannot wedge board-wide recovery. A lookalike gate marker written by a worker is not authorship and is classified for the coordinator like any other unexpected block. Classification also always answers the newest terminal event: an older timeout never outranks a newer incident, so the recovery card is keyed to the block that is live now. A blocked recovery card of the reconciler itself is reported but never answered with another recovery card, because that recursion is unbounded. A terminal provider park is the one class that answers nothing with a card at all: the outage kills any delegated recovery too, so the reconciler re-opens the parked card itself with a recorded decision, once per card, and reports a second park instead of re-opening it again. That applies to a Factory recovery card as well - the case where a parked recovery card used to leave its whole target chain gated - and the card's target is re-derived as the anchor before the mutation.

For each eligible card, Factory creates or reuses one high-priority coordinator card, except for the provider class, which the reconciler re-opens in place without creating any card. Its idempotency key includes the project, blocked task id, stall-class token, and the triggering event's timestamp, so repeated cron ticks do not create duplicates and a newer block event starts a new recovery. The recovery card has no dependency on the blocked task. For a timeout it instructs the coordinator to inspect the log and live diff, preserve valid partial work, and create smaller sequential TDD implementation/review pairs; for a rejected review it instructs the coordinator to create one narrow correction implementation and a fresh independent review that re-runs the required proof; for an unknown stall it hands over the blocked event as inert evidence and requires exactly one bounded structured outcome - `resume`, `retry`, `replace`, `reassign`, `decompose`, or `terminate` - with no operator contact and no new taxonomy class during the mission. All classes reconnect downstream dependencies, verify the replacement graph, and complete with exact `replacement_task_ids`, `rewires`, `superseded_task_ids`, and `limitations` metadata.

Delegated workers are intentionally fenced from `unlink`, archive, purge, comment, and dispatch. A completed recovery plan is therefore finalized by the trusted no-agent reconciler: it validates the original target and replacement boundary, verifies replacement edges before mutation, unlinks only declared stale edges, reads each child back, archives and purges superseded cards, and records a finalization marker. Repeated passes are idempotent. An unblock that lands the card in its restored resumable phase - `ready`, `todo`, or `review`, or already claimed by the dispatcher - counts as success, because only a parked status proves the unblock failed. Trigger-time scoping plus a parked target decides staleness for every class: a recovery answers the block transition recorded in its own key, and only a `blocked` or `archived` target may be recovered, because resolving a card writes no terminal event - `unblock` records `unblocked` and a parent promotion records `promoted` - so a resumed or finished card would otherwise read as fresh while the plan destroyed live work. A decision that no longer matches live state is rejected without mutation and its card is archived - a terminal status no delegated worker can write - with the reason left as a `recovery-rejected` comment, so a superseded decision cannot fail every later pass and a forged comment cannot suppress a pending recovery, while a newer block still gets its own keyed recovery. When an interrupted earlier pass already applied a rewire, the reason records that too and the remaining superseded cards are left to the newer incident; the plan is still retired, because a decision that can never match live state must not wedge the pass. If a child cannot be read back or answers with a different id, neither state is proven, so the reason says a partial application cannot be excluded rather than claiming one. A plan that goes stale only after its own first applied mutation in the same pass cannot be retired that way: the pass reports `stale after a partial mutation` and leaves the card and the partially rewired graph visible, so the next pass either finishes the application or reports again. A completed recovery card whose recorded plan cannot be parsed at all - unknown or extra metadata keys, wrong types, a missing required key, an inconsistent `verified_cards` manifest, malformed `rewires`, a truncated replacement policy, or a completion that does not bind to its own run - is the one case where strict parsing alone would wedge the board forever: the card can never be applied, yet it holds the idempotency key of its blocked target. The reconciler therefore retires it once through the same privileged disposal path, records the exact parse error as the reason, frees the key, and lets the next pass create and dispatch a fresh recovery for the same block; nothing else is mutated, and plan validation is never weakened into repairing a bad record. That disposition is bounded to one per exact recovery key - an archived card carrying the same Factory recovery key is the authoritative record that the decision was already disposed, so a second unreadable generation is reported instead of archived again; a plain key and a generation of the same block are separate keys, so each still needs its own card to move before anything is retired, and it refuses unless Factory authorship, a parked target, the absence of a live run, and the target's live terminal event are all re-derived from live records. A recorded unlink is not time-boxed either - it stays valid proof that an edge was removed rather than dropped by a purge, because a rewire depends on graph state, not on which pass removed it. The optional `--dispatch` flag then asks the native dispatcher to start recovery or newly released work. Factory still does not own claims, worker processes, retries, heartbeats, or run history.

### Recovery generations

Every privileged mutation is re-derived from a fresh read immediately before it is issued, because Hermes offers no conditional cross-record mutation: classifying a blocked card reads its target while a recovery card's own re-open or archival reads that same target, so either entity can move in between. When the card a pass validated moves, Factory refuses the mutation and answers the same block with a new recovery *generation*. The decision is still live, the record that was supposed to answer it can no longer be vouched for, and reusing its idempotency key would hand the same stale record straight back, because Hermes returns any existing non-archived card carrying the requested key. A generation's key therefore carries one trailing `generation:<card id>` segment after the standard six, naming the card it answers; that derivation is stable for as long as the card exists, so a repeated pass finds the same generation instead of asking for another one, and a completed generation is read as evidence exactly like the card it answers. A create that Hermes resolves to a card already in flight is reported as the block's recovery, except when that card carries a completed run whose recorded plan cannot be read: such a card resumed after recording a decision that can never be applied, which is precisely the record the disposal path exists to retire, so the block gets a generation and that record is left to its own disposition.

### Stall taxonomy

`hermes-factory stall-taxonomy --json` exposes the stable operational contract. Every class records `reason_code`, detection, cause, recovery owner, recovery, authority, verification, fallback, regression test, and whether recovery is automatic. An incident that no dedicated detector explains is handled at runtime by the `unknown_operational_stall` catch-all instead of a one-off manual repair. Adding or refining a dedicated class, detector, recovery procedure, and regression test is deliberate engineering work, not a runtime coordinator obligation.

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
