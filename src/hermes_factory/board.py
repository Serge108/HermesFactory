from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_factory.compiler import MissionPlan, PlannedTask
from hermes_factory.errors import HermesCommandError
from hermes_factory.hermes_cli import HermesClient
from hermes_factory.models import RigSpec


@dataclass(frozen=True, slots=True)
class BoardApplyResult:
    slug: str
    created: bool


@dataclass(frozen=True, slots=True)
class AppliedMission:
    task_ids: dict[str, str]


def _parse_boards(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise HermesCommandError("Hermes returned malformed board list JSON")
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("slug"), str)
            or ("archived" in item and type(item["archived"]) is not bool)
        ):
            raise HermesCommandError("Hermes returned malformed board list JSON")
    return value


def ensure_board(client: HermesClient, rig: RigSpec) -> BoardApplyResult:
    boards = _parse_boards(
        client.run_json(["kanban", "boards", "list", "--json", "--all"])
    )
    if any(
        item["slug"] == rig.board.slug and not item.get("archived", False)
        for item in boards
    ):
        return BoardApplyResult(rig.board.slug, False)
    client.run(
        [
            "kanban",
            "boards",
            "create",
            rig.board.slug,
            "--name",
            rig.board.name,
            "--description",
            "Managed by Hermes Factory",
            "--default-workdir",
            str(rig.project.cwd),
        ]
    )
    return BoardApplyResult(rig.board.slug, True)


def _parse_task_creation(value: Any) -> str:
    if not isinstance(value, dict):
        raise HermesCommandError("Hermes returned malformed task JSON")
    task_id = value.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise HermesCommandError("Hermes returned malformed task JSON")
    if not isinstance(value.get("status"), str) or not value.get("status"):
        raise HermesCommandError("Hermes returned malformed task JSON")
    if not isinstance(value.get("title"), str) or not value.get("title"):
        raise HermesCommandError("Hermes returned malformed task JSON")
    return task_id


def _task_runtime(rig: RigSpec, task: PlannedTask) -> str:
    """Coordinator-owned cards get the coordinator budget; slice cards keep the slice one."""
    if task.key == "acceptance" or task.key.endswith(":commit"):
        return rig.policies.coordinator_runtime
    return rig.policies.max_runtime


def _create_task(
    client: HermesClient,
    rig: RigSpec,
    task: PlannedTask,
    resolved_parents: tuple[str, ...],
) -> str:
    argv: list[str] = ["kanban", "--board", rig.board.slug, "create", task.title]
    argv += ["--body", task.body]
    for parent in resolved_parents:
        argv += ["--parent", parent]
    for skill in task.skills:
        argv += ["--skill", skill]
    argv += ["--idempotency-key", task.idempotency_key]
    argv += ["--max-retries", str(rig.policies.max_retries)]
    argv += ["--max-runtime", _task_runtime(rig, task)]
    argv += ["--completion-contract", rig.policies.completion_contract]
    argv += ["--workspace", f"dir:{rig.project.cwd}"]
    argv += ["--created-by", "hermes-factory"]
    if task.profile is not None:
        argv += ["--assignee", task.profile]
    if task.initial_status is not None:
        argv += ["--initial-status", task.initial_status]
    argv += ["--json"]
    return _parse_task_creation(client.run_json(argv))


def _subscribe_task(client: HermesClient, rig: RigSpec, task_id: str) -> None:
    for subscription in rig.notifications.task_subscriptions:
        client.run(
            [
                "kanban",
                "--board",
                rig.board.slug,
                "notify-subscribe",
                task_id,
                "--platform",
                subscription.platform,
                "--chat-id",
                subscription.chat_id,
                "--user-id",
                subscription.user_id,
                "--chat-type",
                subscription.chat_type,
                "--notifier-profile",
                subscription.notifier_profile,
                "--delivery-mode",
                subscription.delivery_mode,
            ]
        )


def apply_mission(
    client: HermesClient, rig: RigSpec, plan: MissionPlan
) -> AppliedMission:
    task_ids: dict[str, str] = {}
    for task in plan.tasks:
        resolved_parents = tuple(task_ids[parent] for parent in task.parents)
        task_id = _create_task(client, rig, task, resolved_parents)
        _subscribe_task(client, rig, task_id)
        task_ids[task.key] = task_id
    return AppliedMission(task_ids=task_ids)
