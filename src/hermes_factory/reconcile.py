from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_factory.board import _parse_task_creation, _subscribe_task
from hermes_factory.errors import HermesCommandError, SpecError
from hermes_factory.hermes_cli import HermesClient
from hermes_factory.models import RigSpec

_RECOVERY_CREATOR = "hermes-factory-reconciler"
_RECOVERY_SKILL = "hermes-native-agent-factories"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    scanned: int
    eligible: int
    recovery_task_ids: dict[str, str]
    dispatched: int


def _task_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise HermesCommandError("Hermes returned malformed blocked task list JSON")
    return value


def _task_show(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HermesCommandError("Hermes returned malformed task detail JSON")
    task = value.get("task")
    events = value.get("events")
    parents = value.get("parents")
    children = value.get("children")
    if (
        not isinstance(task, dict)
        or not isinstance(events, list)
        or not isinstance(parents, list)
        or not isinstance(children, list)
        or any(not isinstance(event, dict) for event in events)
        or any(not isinstance(task_id, str) for task_id in parents + children)
    ):
        raise HermesCommandError("Hermes returned malformed task detail JSON")
    return value


def _timeout_gave_up_event(detail: dict[str, Any]) -> dict[str, Any] | None:
    for event in reversed(detail["events"]):
        if event.get("kind") != "gave_up":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("trigger_outcome") == "timed_out":
            return event
    return None


def _coordinator_profile(rig: RigSpec) -> str:
    seat_names = {workflow.acceptance_seat for workflow in rig.workflows.values()}
    profiles = {rig.seats[seat_name].profile for seat_name in seat_names}
    if len(profiles) != 1:
        raise SpecError(
            "reconcile requires every workflow acceptance seat to resolve to one coordinator profile"
        )
    return next(iter(profiles))


def _recovery_body(
    rig: RigSpec,
    task: dict[str, Any],
    parents: list[str],
    children: list[str],
    gave_up: dict[str, Any],
) -> str:
    task_id = task["id"]
    failure = task.get("last_failure_error") or (gave_up.get("payload") or {}).get("error")
    parent_text = ", ".join(parents) or "(none)"
    child_text = ", ".join(children) or "(none)"
    return f"""<!-- hermes-factory:recovery -->
# Recovery target
Board: {rig.board.slug}
Superseded task: {task_id}
Title: {task.get('title', '(missing title)')}
Failure: {failure or '(not reported)'}
Incoming parents: {parent_text}
Current children: {child_text}
Project directory: {rig.project.cwd}

# Required coordinator action
The implementation task reached its one-attempt failure limit. Do not retry the same scope.

1. Read the target with `hermes kanban --board {rig.board.slug} show {task_id} --json` and `log {task_id}`.
2. Inspect the live repository diff. Preserve valid partial work; do not reset or overwrite unrelated changes.
3. Decompose only the unfinished scope into smaller sequential TDD implementation tasks, each followed by an independent review task.
4. Recreate the dependency path: retain completed incoming prerequisites, chain each replacement review to the next implementation, and reconnect downstream children to the final replacement review.
5. Verify the replacement task bodies, assignees, runtime/retry limits, and dependency graph from authoritative Kanban records.
6. Only after the replacement graph is verified, archive and permanently remove the superseded implementation and review cards that became obsolete.
7. Complete this recovery card with the replacement task ids, rewired dependencies, deleted task ids, and remaining limitations.

Use Hermes Kanban as the only task-state store. Do not edit its SQLite database directly.
<!-- /hermes-factory:recovery -->"""


def _create_recovery_task(
    client: HermesClient,
    rig: RigSpec,
    detail: dict[str, Any],
    gave_up: dict[str, Any],
    coordinator_profile: str,
) -> str:
    task = detail["task"]
    task_id = task.get("id")
    title = task.get("title")
    event_time = gave_up.get("created_at")
    if not isinstance(task_id, str) or not task_id or not isinstance(title, str) or not title:
        raise HermesCommandError("Hermes returned malformed task detail JSON")
    if type(event_time) is not int:
        raise HermesCommandError("Hermes returned malformed gave_up event JSON")
    body = _recovery_body(
        rig,
        task,
        detail["parents"],
        detail["children"],
        gave_up,
    )
    argv = [
        "kanban",
        "--board",
        rig.board.slug,
        "create",
        f"Decompose blocked task {task_id}",
        "--body",
        body,
        "--assignee",
        coordinator_profile,
        "--skill",
        _RECOVERY_SKILL,
        "--idempotency-key",
        f"factory:{rig.project.slug}:reconcile:{task_id}:gave-up:{event_time}",
        "--max-retries",
        str(rig.policies.max_retries),
        "--max-runtime",
        rig.policies.max_runtime,
        "--completion-contract",
        rig.policies.completion_contract,
        "--workspace",
        f"dir:{rig.project.cwd}",
        "--created-by",
        _RECOVERY_CREATOR,
        "--priority",
        "100",
        "--json",
    ]
    recovery_id = _parse_task_creation(client.run_json(argv))
    _subscribe_task(client, rig, recovery_id)
    return recovery_id


def reconcile_board(
    client: HermesClient, rig: RigSpec, *, dispatch: bool = False
) -> ReconcileResult:
    board = ["kanban", "--board", rig.board.slug]
    blocked = _task_list(
        client.run_json(board + ["list", "--status", "blocked", "--json"])
    )
    coordinator_profile = _coordinator_profile(rig)
    recovery_task_ids: dict[str, str] = {}
    eligible = 0
    for task in blocked:
        if task.get("created_by") != "hermes-factory":
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise HermesCommandError("Hermes returned malformed blocked task list JSON")
        detail = _task_show(client.run_json(board + ["show", task_id, "--json"]))
        gave_up = _timeout_gave_up_event(detail)
        if gave_up is None:
            continue
        eligible += 1
        recovery_task_ids[task_id] = _create_recovery_task(
            client, rig, detail, gave_up, coordinator_profile
        )
    dispatched = 0
    if dispatch and recovery_task_ids:
        ready = 0
        for recovery_id in recovery_task_ids.values():
            detail = _task_show(
                client.run_json(board + ["show", recovery_id, "--json"])
            )
            if detail["task"].get("status") == "ready":
                ready += 1
        if ready:
            dispatch_result = client.run_json(
                board + ["dispatch", "--max", str(ready), "--json"]
            )
            if not isinstance(dispatch_result, dict):
                raise HermesCommandError("Hermes returned malformed dispatch JSON")
            spawned = dispatch_result.get("spawned")
            if not isinstance(spawned, list) or any(
                not isinstance(item, dict) for item in spawned
            ):
                raise HermesCommandError("Hermes returned malformed dispatch JSON")
            dispatched = len(spawned)
    return ReconcileResult(
        scanned=len(blocked),
        eligible=eligible,
        recovery_task_ids=recovery_task_ids,
        dispatched=dispatched,
    )
