from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StallClass:
    code: str
    detection: str
    cause: str
    recovery_owner: str
    recovery: str
    authority: str
    verification: str
    fallback: str
    regression_test: str
    automatic: bool


_CLASSES = (
    StallClass(
        code="worker_timeout",
        detection="A Factory task is blocked after a gave_up event whose trigger_outcome is timed_out.",
        cause="The worker exceeded its bounded runtime and exhausted the configured failure limit.",
        recovery_owner="coordinator",
        recovery="Create a smaller replacement implementation/review graph and submit a structured recovery plan.",
        authority="The coordinator may create replacement tasks; Factory performs privileged cleanup.",
        verification="Factory validates replacement tasks, rewires downstream children, removes superseded cards, and dispatches released work.",
        fallback="Leave the original cards and partial repository work intact and report a recovery error.",
        regression_test="test_reconcile_creates_idempotent_coordinator_task_for_gave_up_factory_task",
        automatic=True,
    ),
    StallClass(
        code="recovery_coordinator_insufficient_kanban_authority",
        detection="A recovery worker cannot unlink, archive, or purge because delegated child Kanban mutation is fenced.",
        cause="The recovery plan requires privileged graph mutation that a delegated worker is intentionally forbidden to perform.",
        recovery_owner="factory",
        recovery="Apply the validated rewire-and-supersede plan from the trusted no-agent reconciler.",
        authority="Factory may invoke exact Kanban unlink, archive, purge, comment, and dispatch operations declared by a validated plan.",
        verification="Read back every child parent set, confirm superseded ids are absent, and record the finalization marker.",
        fallback="Fail closed before destructive cleanup and retain the plan for the next idempotent reconciliation pass.",
        regression_test="test_reconcile_finalizes_completed_recovery_plan_outside_worker_fence",
        automatic=True,
    ),
    StallClass(
        code="awaiting_dispatch",
        detection="A task is ready, assigned, and has no claimed or spawned event.",
        cause="Gateway dispatch is disabled, unavailable, or has not yet selected the ready task.",
        recovery_owner="factory",
        recovery="Run one native Kanban dispatcher pass after checking the configured dispatch policy.",
        authority="Factory may request native dispatch but does not claim tasks or supervise worker processes.",
        verification="Require ready followed by claimed, spawned, and heartbeat events.",
        fallback="Leave the task ready and emit the reason code with the missing lifecycle event.",
        regression_test="test_reconcile_dispatches_only_ready_recovery_tasks",
        automatic=True,
    ),
    StallClass(
        code="normal_dependency_wait",
        detection="A todo task has one or more non-terminal parents and the dependency graph is internally consistent.",
        cause="Required upstream work has not completed.",
        recovery_owner="factory",
        recovery="Do not mutate the graph; continue observing the authoritative parent lifecycle.",
        authority="No recovery mutation is authorized for a valid dependency wait.",
        verification="The task becomes ready only after every parent reaches the required terminal state.",
        fallback="Escalate to corrupt_state if task status and parent states contradict each other.",
        regression_test="test_mission_tasks_are_created_in_dependency_order_with_flags",
        automatic=True,
    ),
    StallClass(
        code="stale_runner",
        detection="A task has a claim or run without a live heartbeat or terminal event beyond the Hermes stale threshold.",
        cause="The worker process disappeared or stopped reporting lifecycle progress.",
        recovery_owner="hermes",
        recovery="Use native Hermes reclaim, timeout, and retry handling; reconcile only after an authoritative terminal event.",
        authority="Hermes owns claims, heartbeats, process lifetime, retries, and terminal run state.",
        verification="The stale run receives a terminal event and the task is retried, blocked, or completed consistently.",
        fallback="Report corrupt_state if native diagnostics and run history disagree.",
        regression_test="test_native_hermes_diagnostics_are_flattened",
        automatic=True,
    ),
    StallClass(
        code="stale_dependency_edge",
        detection="A downstream child still depends on a superseded task after its replacement boundary is complete.",
        cause="Recovery created a replacement graph but did not remove every obsolete parent edge.",
        recovery_owner="factory",
        recovery="Validate the declared replacement edge, unlink the stale parent, and read the child back before cleanup.",
        authority="Factory may mutate only edges named by a validated recovery plan.",
        verification="The child depends on the final replacement review and on no superseded parent.",
        fallback="Stop before archive or purge and preserve both graphs for diagnosis.",
        regression_test="test_reconcile_finalizes_completed_recovery_plan_outside_worker_fence",
        automatic=True,
    ),
    StallClass(
        code="review_rejected",
        detection="A Factory review task is blocked by its own worker with a reason beginning with the review-rejection prefix and non-empty actionable findings after it.",
        cause="The implementation failed an acceptance criterion, proof requirement, or correctness invariant.",
        recovery_owner="coordinator",
        recovery="Create a narrow correction implementation task and a fresh independent review task.",
        authority="The coordinator may reshape in-scope work but may not approve its own correction.",
        verification="A fresh reviewer completes the correction review with the required proof.",
        fallback="Keep downstream work blocked and preserve the review findings.",
        regression_test="test_reconcile_finalizes_rejected_review_recovery_plan",
        automatic=True,
    ),
    StallClass(
        code="terminal_provider_park",
        detection="A Factory card, or a Factory recovery card, whose newest terminal event is a breaker `gave_up` record carrying `terminal_provider: true`, `trigger_outcome: \"crashed\"`, `sticky: true`, and no unexpected `exit_code`.",
        cause="The worker's model provider rejected the seat profile's credential or model, so the run died on a terminal provider error instead of completing any task-level work.",
        recovery_owner="factory",
        recovery="The trusted no-agent reconciler re-opens the parked card once with a recorded decision and dispatches it; a second terminal provider park of the same card is reported and left parked, because a provider outage is an environment fault rather than task work.",
        authority="Only the trusted reconciler may unblock and dispatch the parked card; no work is created from a worker session and no other card is unlinked, archived, or purged.",
        verification="Hermes records an `unblocked` event at or after the decision time, the card leaves the parked status, and the released card continues through the normal lifecycle.",
        fallback="Refuse the mutation and report the pass as failed when the live newest terminal event moved on, when a recovery card's target no longer carries the terminal event its key names, or when the card was already re-opened once.",
        regression_test="test_reconcile_reopens_provider_parked_factory_card_once",
        automatic=True,
    ),
    StallClass(
        code="unparseable_recovery_plan",
        detection="A completed Factory recovery card whose newest successful run records a plan that cannot be parsed, while its blocked target still carries the terminal event the recovery key names.",
        cause="The recovery worker recorded a decision the reconciler cannot read - unknown or extra metadata keys, wrong types, missing required keys, an inconsistent verified_cards manifest, malformed rewires, or a truncated replacement policy - so the plan can never be applied and would otherwise fail every pass forever.",
        recovery_owner="factory",
        recovery="Archive the unreadable card once through the privileged disposal path with the exact parse error recorded as the reason, leaving the rest of the graph untouched, so the freed idempotency key lets the next pass create and dispatch a fresh recovery for the same block; a second unreadable generation of the same decision is reported instead of archived again.",
        authority="Only the trusted no-agent reconciler may archive the card, and only after re-deriving its Factory authorship, its parked target, the absence of a live run, and the target's live terminal event from authoritative records.",
        verification="Hermes records the card as archived with the rejection marker and the parse error, the target stays parked, and the next pass creates and dispatches a fresh recovery card for the same block.",
        fallback="Refuse and report the pass as failed when Factory authorship, the parked target, the absence of a live run, or the live terminal event cannot be re-derived, when the target or the board cannot be read back (a partial application cannot be excluded), or when the same decision was already disposed.",
        regression_test="test_reconcile_disposes_an_unparseable_recovery_plan_once",
        automatic=True,
    ),
    StallClass(
        code="no_agent_activity",
        detection="No task is running while todo or non-human-gate blocked work remains unchanged beyond the rig's no-agent grace period.",
        cause="Dispatch, dependency, recovery, or orchestration state left the board with backlog but no active worker.",
        recovery_owner="coordinator",
        recovery="Create one idempotent board-level incident for the coordinator to diagnose and restore Hermes-native execution.",
        authority="The coordinator may diagnose and create bounded in-scope work; Factory retains privileged graph cleanup.",
        verification="A task reaches running state or the remaining backlog is resolved, intentionally gated, or terminated with evidence.",
        fallback="Preserve every card and report the incident without editing the board database or bypassing mutation fences.",
        regression_test="test_reconcile_registers_board_incident_when_no_agent_is_running_past_grace",
        automatic=True,
    ),
    StallClass(
        code="human_gate",
        detection="A blocked acceptance task belongs to a rig whose decision_authority is human and human_acceptance is true, and Factory - not a worker session - authored the card.",
        cause="The mission explicitly reserved product acceptance for the operator.",
        recovery_owner="operator",
        recovery="Wait for an explicit operator decision; coordinator-authority rigs must not create this stop.",
        authority="Only the operator may complete or reject the human acceptance gate.",
        verification="Acceptance has a recorded operator decision and a terminal lifecycle event.",
        fallback="Remain blocked without retries or automatic approval.",
        regression_test="test_human_acceptance_blocks_acceptance_task_without_assignee",
        automatic=False,
    ),
    StallClass(
        code="corrupt_state",
        detection="Task status, events, runs, or dependency edges contradict each other.",
        cause="Partial mutation, malformed records, or an invariant violation produced inconsistent authoritative state.",
        recovery_owner="factory",
        recovery="Fail closed, preserve evidence, and run deterministic diagnostics before any repair.",
        authority="Only a validated repair procedure may alter contradictory Kanban state.",
        verification="All lifecycle and dependency invariants pass after repair and remain covered by a regression test.",
        fallback="Require an operator only when no safe deterministic repair can be proven.",
        regression_test="test_malformed_generated_contract_fails_closed",
        automatic=False,
    ),
    StallClass(
        code="unknown_operational_stall",
        detection="A blocked Factory task carries an authoritative terminal event (a task-scoped `blocked` event or a `gave_up` event from the failure breaker) but is neither an explicit, Factory-authored human gate nor explained by a registered recovery detector.",
        cause="The task encountered an operational condition without a dedicated deterministic recovery class.",
        recovery_owner="coordinator",
        recovery="Create one idempotent coordinator recovery card for the blocked event, choose a bounded structured outcome, and let Factory validate and apply it.",
        authority="The coordinator may resume, retry, reassign, create a validated replacement graph, decompose the work, or terminate a safe leaf; Factory performs privileged mutations and read-back verification.",
        verification="Factory revalidates the triggering terminal event and live graph, applies the structured outcome, reads back the target or replacement boundary, and records finalization.",
        fallback="Keep the catch-all class, preserve the blocked task and evidence, and reject any unsafe or stale outcome without waiting for human input.",
        regression_test="test_reconcile_routes_unclassified_block_to_coordinator",
        automatic=True,
    ),
)


def stall_taxonomy() -> tuple[StallClass, ...]:
    return _CLASSES