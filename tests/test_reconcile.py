import json
from pathlib import Path

import pytest

from hermes_factory import reconcile as reconcile_module
from hermes_factory.errors import HermesCommandError, SpecError
from hermes_factory.reconcile import (
    RecoveryPlan,
    RecoveryRewire,
    _recovery_validation_marker,
    reconcile_board,
)
from hermes_factory.rig_loader import load_rig
from hermes_factory.task_templates import REVIEW_REJECTION_PREFIX


def _audit_body(task_class: str, slice_id: str = "original") -> str:
    required_metadata = {
        "implementation": '["changed_files","commands","limitations"]',
        "review": '["changed_files","commands","limitations"]',
    }[task_class]
    seat = "builder" if task_class == "implementation" else "reviewer"
    role = "implementation" if task_class == "implementation" else "independent_review"
    return (
        "# Project\nDemo (slug: demo)\nWorking directory: F:/Project With Space\n\n"
        "# Mission\nDemo mission: preserve the mission intent.\n\n"
        f"# Slice\nSlice id: {slice_id}\nDemo slice: preserve the replacement slice.\n\n"
        "# Acceptance\n- replacement acceptance\n\n"
        "# Proof required\nCommands that must be recorded:\n- uv run pytest\n\n"
        "# Scope boundaries\n"
        "Project demo; mission demo-mission; workflow delivery.\n"
        f"Assigned seat: {seat}; role: {role}.\n"
        "Failure limit: 2 consecutive non-successful runs; bounded runtime per task: 30m.\n"
        "Completion contract: local-only.\n"
        "<!-- hermes-factory:audit-contract -->\n"
        'Factory audit contract: {"task_class":"'
        + task_class
        + '","required_commands":[],"required_artifacts":[],'
        '"changed_files_required":false,"required_metadata":'
        + required_metadata
        + "}\n"
        "<!-- /hermes-factory:audit-contract -->\n\n"
        "# Handoff\nNext target: review."
    )


def _replacement_policy_body(
    *,
    source_slice: str = "original",
    incoming: tuple[str, ...] = ("parent",),
    source_class: str = "implementation",
) -> str:
    policy = {
        "project": "demo",
        "mission": "demo-mission",
        "source_slice": source_slice,
        "workflow": "delivery",
        "incoming_parent_ids": list(incoming),
        "workspace_kind": "dir",
        "workspace_path": "F:/Project With Space",
        "max_retries": 2,
        "max_runtime_seconds": 1800,
        "completion_contract": "local-only",
        "implementation_assignee": "builder",
        "implementation_skills": [],
        "implementation_seat": "builder",
        "implementation_role": "implementation",
        "review_assignee": "reviewer",
        "review_skills": [],
        "review_seat": "reviewer",
        "review_role": "independent_review",
        "mission_body": {
            "project": "Demo (slug: demo)\nWorking directory: F:/Project With Space",
            "mission": "Demo mission: preserve the mission intent.",
        },
        "source_audit_contract": {
            "task_class": source_class,
            "required_commands": [],
            "required_artifacts": [],
            "changed_files_required": False,
            "required_metadata": ["changed_files", "commands", "limitations"],
        },
    }
    return (
        "<!-- hermes-factory:replacement-policy -->\n"
        "Factory replacement policy: "
        + json.dumps(policy, sort_keys=True, separators=(",", ":"))
        + "\n<!-- /hermes-factory:replacement-policy -->"
    )


def _factory_detail(
    task_id: str,
    task_class: str,
    slice_id: str,
    assignee: str,
    *,
    parents: list[str] | None = None,
    children: list[str] | None = None,
    created_by: str = "hermes-factory",
    creator_task_id: str | None = None,
    gave_up: bool = False,
    status: str = "todo",
) -> dict[str, object]:
    events: list[dict[str, object]] = [
        {
            "kind": "created",
            "payload": {"creator_task_id": creator_task_id},
        }
    ]
    if gave_up:
        events.append(
            {
                "kind": "gave_up",
                "created_at": 200,
                "payload": {"trigger_outcome": "timed_out"},
            }
        )
    return {
        "task": {
            "id": task_id,
            "title": f"{task_class.title()} demo",
            "body": _audit_body(task_class, slice_id),
            # Hermes parks a card the failure breaker gave up on as blocked; a plain
            # backlog card stays todo.
            "status": "blocked" if gave_up else status,
            "assignee": assignee,
            "created_by": created_by,
            "idempotency_key": (
                f"factory:demo:demo-mission:{slice_id}:{task_class}"
            ),
            "workspace_kind": "dir",
            "workspace_path": "F:/Project With Space",
            "max_retries": 2,
            "max_runtime_seconds": 1800,
            "completion_contract": "local-only",
            "skills": [],
        },
        "parents": parents or [],
        "children": children or [],
        "comments": [],
        "events": events,
        "runs": [],
    }


def _completed_recovery(
    *,
    replacements: list[str],
    rewires: list[dict[str, str]],
    superseded: list[str],
    worker_session_id: str | None = None,
    target: str = "old-impl",
    reason_code: str = "worker_timeout",
    policy_body: str | None = None,
    event_time: int = 200,
) -> tuple[dict[str, object], dict[str, object]]:
    recovery = {
        "id": "recovery-1",
        "title": "Decompose blocked task old-impl",
        "body": (
            f"Superseded task: {target}\nReason code: {reason_code}\n\n"
            + (policy_body if policy_body is not None else _replacement_policy_body())
        ),
        "status": "done",
        "created_by": "hermes-factory-reconciler",
        "idempotency_key": (
            f"factory:demo:reconcile:{target}:"
            f"{reconcile_module._KEY_TOKENS[reason_code]}:{event_time}"
        ),
    }
    detail = {
        "task": recovery,
        "parents": [],
        "children": [],
        "comments": [],
        "events": [
            {
                "kind": "created",
                "created_at": 100,
                "payload": {"creator_task_id": None},
            },
            {
                "kind": "completed",
                "created_at": 200,
                "run_id": 200,
                "payload": {"verified_cards": replacements},
            }
        ],
        "runs": [
            {
                "id": 200,
                "status": "done",
                "outcome": "completed",
                "metadata": {
                    "replacement_task_ids": replacements,
                    "rewires": rewires,
                    "superseded_task_ids": superseded,
                    "limitations": [],
                },
            }
        ],
    }
    if worker_session_id is not None:
        detail["runs"][0]["metadata"]["worker_session_id"] = worker_session_id
    return recovery, detail


class FakeReconcileClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.json_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []

    def run_json(self, args: list[str]) -> object:
        self.json_calls.append(args)
        return self.responses.pop(0)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        return ""


class StatefulRecoveryClient:
    def __init__(
        self,
        recovery: dict[str, object],
        recovery_detail: dict[str, object],
        details: list[dict[str, object]],
        *,
        fail_after_purge: bool = False,
        fail_after_first_purge: bool = False,
        extra_recoveries: list[tuple[dict[str, object], dict[str, object]]] | None = None,
    ) -> None:
        self.recovery = recovery
        self.recovery_detail = recovery_detail
        self.details = {detail["task"]["id"]: detail for detail in details}
        self.fail_after_purge = fail_after_purge
        self.fail_after_first_purge = fail_after_first_purge
        self.recoveries = {recovery["id"]: recovery_detail}
        for extra_recovery, extra_detail in extra_recoveries or ():
            self.recoveries[extra_recovery["id"]] = extra_detail
        self.event_time = 200
        self.json_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []

    def run_json(self, args: list[str]) -> object:
        self.json_calls.append(args)
        tail = args[3:]
        if tail == ["list", "--status", "blocked", "--json"]:
            return []
        if tail == ["list", "--status", "done", "--json"]:
            return [
                detail["task"]
                for detail in self.recoveries.values()
                if detail["task"]["status"] != "archived"
            ]
        if tail[:1] == ["show"] and tail[1] in self.recoveries:
            return self.recoveries[tail[1]]
        if tail[:1] == ["show"]:
            return self.details[tail[1]]
        if tail == ["list", "--archived", "--json"]:
            return [
                detail["task"] for detail in self.recoveries.values()
            ] + [detail["task"] for detail in self.details.values()]
        raise AssertionError(args)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        tail = args[3:]
        if tail[:1] == ["unlink"]:
            parent, child = tail[1:3]
            if parent in self.details[child]["parents"]:
                self.details[child]["parents"].remove(parent)
            if child in self.details[parent]["children"]:
                self.details[parent]["children"].remove(child)
            # Hermes records the removal on the child; retries depend on it.
            self.event_time += 1
            self.details[child]["events"].append(
                {
                    "kind": "unlinked",
                    "created_at": self.event_time,
                    "payload": {"parent": parent, "child": child},
                }
            )
        elif tail[:2] == ["archive", "--rm"]:
            for index, task_id in enumerate(tail[2:]):
                if self.fail_after_first_purge and index == 1:
                    raise HermesCommandError("simulated interruption mid purge")
                self.details.pop(task_id, None)
                # Hermes drops the purged task's link rows, leaving survivors
                # without the dangling parent edge.
                for detail in self.details.values():
                    detail["parents"] = [
                        parent for parent in detail["parents"] if parent != task_id
                    ]
                    detail["children"] = [
                        child for child in detail["children"] if child != task_id
                    ]
            if self.fail_after_purge:
                raise HermesCommandError("simulated interruption after purge")
        elif tail[:1] == ["archive"]:
            for task_id in tail[1:]:
                if task_id in self.recoveries:
                    self.recoveries[task_id]["task"]["status"] = "archived"
                else:
                    self.details[task_id]["task"]["status"] = "archived"
        elif tail[:2] == ["comment", self.recovery["id"]]:
            self.recovery_detail["comments"].append(
                {"body": tail[2], "author": tail[4]}
            )
        return ""


def _blocked_task(task_id: str = "task-1") -> dict[str, object]:
    return {
        "id": task_id,
        "title": "Implement large behavior",
        "body": _audit_body("implementation"),
        "assignee": "builder",
        "status": "blocked",
        "created_by": "hermes-factory",
        "idempotency_key": "factory:demo:demo-mission:original:implementation",
        "workspace_kind": "dir",
        "workspace_path": "F:\\Project With Space",
        "max_retries": 2,
        "max_runtime_seconds": 1800,
        "completion_contract": "local-only",
        "skills": [],
        "last_failure_error": "elapsed 11s > limit 10s",
    }


def _timed_out_show(task_id: str = "task-1") -> dict[str, object]:
    return {
        "task": _blocked_task(task_id),
        "parents": ["parent-1"],
        "children": ["review-1", "downstream-1"],
        "comments": [],
        "events": [
            {"kind": "timed_out", "created_at": 101, "run_id": 7},
            {
                "kind": "gave_up",
                "created_at": 102,
                "run_id": None,
                "payload": {
                    "failures": 1,
                    "effective_limit": 1,
                    "trigger_outcome": "timed_out",
                    "error": "elapsed 11s > limit 10s",
                },
            },
        ],
        "runs": [
            {
                "id": 7,
                "status": "timed_out",
                "outcome": "timed_out",
                "error": "elapsed 11s > limit 10s",
            }
        ],
    }


def _flag_value(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


class IdleBoardClient:
    def __init__(self, snapshot: dict[str, object], *, dispatch: bool = False) -> None:
        self.snapshot = snapshot
        self.dispatch_enabled = dispatch
        self.json_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []
        self.activity_calls: list[tuple[str, tuple[str, ...]]] = []

    def board_activity(
        self,
        board_slug: str,
        *,
        observed_at: int | None = None,
        excluded_creators: tuple[str, ...] = (),
    ) -> dict[str, object]:
        self.activity_calls.append((board_slug, excluded_creators))
        return self.snapshot

    def run_json(self, args: list[str]) -> object:
        self.json_calls.append(args)
        tail = args[3:]
        if tail == ["list", "--status", "blocked", "--json"]:
            return []
        if tail == ["list", "--status", "done", "--json"]:
            return []
        if tail[:1] == ["create"]:
            return {
                "id": "incident-1",
                "status": "ready",
                "title": tail[1],
            }
        if tail == ["show", "incident-1", "--json"]:
            return {
                "task": {"id": "incident-1", "status": "ready"},
                "parents": [],
                "children": [],
                "events": [],
            }
        if tail[:2] == ["dispatch", "--max"]:
            assert self.dispatch_enabled
            return {"spawned": [{"task_id": "incident-1"}]}
        raise AssertionError(args)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        return ""


def _idle_snapshot(
    *,
    observed_at: int = 1000,
    last_activity_at: int = 800,
    tasks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "observed_at": observed_at,
        "last_activity_at": last_activity_at,
        "tasks": tasks
        if tasks is not None
        else [
            {"id": "todo-1", "status": "todo", "created_by": "hermes-factory"}
        ],
    }


def test_reconcile_registers_board_incident_before_task_recovery_when_todo_is_idle(
    valid_rig: Path,
):
    class PreflightIdleClient(IdleBoardClient):
        def run_json(self, args: list[str]) -> object:
            self.json_calls.append(args)
            tail = args[3:]
            if tail == ["list", "--status", "blocked", "--json"]:
                return [_blocked_task()]
            if tail[:1] == ["create"]:
                return {"id": "incident-1", "status": "ready", "title": tail[1]}
            raise AssertionError(f"task-scoped recovery ran before board incident: {args}")

    client = PreflightIdleClient(_idle_snapshot())

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id == "incident-1"
    assert result.recovery_task_ids == {}
    assert not any(call[3] == "show" for call in client.json_calls)


def test_reconcile_registers_board_incident_when_no_agent_is_running_past_grace(
    valid_rig: Path,
):
    client = IdleBoardClient(_idle_snapshot())

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id == "incident-1"
    assert client.activity_calls == [
        ("demo-rig", ("hermes-factory-watchdog",))
    ]
    create = next(call for call in client.json_calls if call[3] == "create")
    assert create[:5] == [
        "kanban",
        "--board",
        "demo-rig",
        "create",
        "Restore agent activity on demo-rig",
    ]
    assert _flag_value(create, "--assignee") == "builder"
    assert _flag_value(create, "--priority") == "100"
    assert _flag_value(create, "--created-by") == "hermes-factory-watchdog"
    assert _flag_value(create, "--max-runtime") == "30m"
    assert _flag_value(create, "--idempotency-key").startswith(
        "factory:demo:reconcile:board:no-agent:800:"
    )
    body = _flag_value(create, "--body")
    assert "Reason code: no_agent_activity" in body
    assert "todo-1" in body
    assert "Do not edit the Kanban SQLite database" in body


def test_reconcile_does_not_register_board_incident_without_backlog(valid_rig: Path):
    client = IdleBoardClient(
        {
            "observed_at": 1000,
            "last_activity_at": None,
            "tasks": [],
        }
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id is None
    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_does_not_register_board_incident_during_grace(valid_rig: Path):
    client = IdleBoardClient(_idle_snapshot(last_activity_at=900))

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id is None
    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_does_not_register_board_incident_while_agent_is_running(
    valid_rig: Path,
):
    client = IdleBoardClient(
        _idle_snapshot(
            tasks=[
                {"id": "run-1", "status": "running", "created_by": "hermes-factory"},
                {"id": "todo-1", "status": "todo", "created_by": "hermes-factory"},
            ]
        )
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id is None
    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_reports_todo_board_incident_without_suppressing_finalization(
    valid_rig: Path,
):
    client = IdleBoardClient(
        _idle_snapshot(
            tasks=[
                {
                    "id": "incident-1",
                    "status": "todo",
                    "created_by": "hermes-factory-watchdog",
                },
                {"id": "todo-1", "status": "todo", "created_by": "hermes-factory"},
            ]
        )
    )

    with pytest.raises(HermesCommandError, match="board incident incident-1 is todo"):
        reconcile_board(client, load_rig(valid_rig))

    assert [
        "kanban",
        "--board",
        "demo-rig",
        "list",
        "--status",
        "done",
        "--json",
    ] in client.json_calls


def test_reconcile_reports_blocked_board_incident_without_recursion(valid_rig: Path):
    class BlockedIncidentClient(IdleBoardClient):
        def run_json(self, args: list[str]) -> object:
            self.json_calls.append(args)
            tail = args[3:]
            if tail == ["list", "--status", "blocked", "--json"]:
                return [
                    {
                        "id": "incident-1",
                        "status": "blocked",
                        "created_by": "hermes-factory-watchdog",
                    }
                ]
            if tail == ["list", "--status", "done", "--json"]:
                return []
            raise AssertionError(args)

    client = BlockedIncidentClient(
        _idle_snapshot(
            tasks=[
                {
                    "id": "incident-1",
                    "status": "blocked",
                    "created_by": "hermes-factory-watchdog",
                },
                {"id": "todo-1", "status": "todo", "created_by": "hermes-factory"},
            ]
        )
    )

    with pytest.raises(HermesCommandError, match="board incident incident-1 is blocked"):
        reconcile_board(client, load_rig(valid_rig))

    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_reuses_active_board_incident_while_idle_backlog_persists(
    valid_rig: Path,
):
    client = IdleBoardClient(
        _idle_snapshot(
            tasks=[
                {
                    "id": "incident-1",
                    "status": "running",
                    "created_by": "hermes-factory-watchdog",
                },
                {"id": "todo-1", "status": "todo", "created_by": "hermes-factory"},
            ]
        )
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id == "incident-1"
    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_ignores_stale_board_incident_after_running_resumes(
    valid_rig: Path,
):
    client = IdleBoardClient(
        _idle_snapshot(
            last_activity_at=999,
            tasks=[
                {
                    "id": "incident-1",
                    "status": "ready",
                    "created_by": "hermes-factory-watchdog",
                },
                {"id": "run-1", "status": "running", "created_by": "hermes-factory"},
                {"id": "todo-1", "status": "todo", "created_by": "hermes-factory"},
            ],
        )
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id is None


def test_reconcile_ignores_stale_board_incident_without_backlog(valid_rig: Path):
    client = IdleBoardClient(
        {
            "observed_at": 1000,
            "last_activity_at": None,
            "tasks": [
                {
                    "id": "incident-1",
                    "status": "todo",
                    "created_by": "hermes-factory-watchdog",
                }
            ],
        }
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id is None
    assert [
        "kanban",
        "--board",
        "demo-rig",
        "list",
        "--status",
        "done",
        "--json",
    ] in client.json_calls


def test_reconcile_replaces_terminal_idempotent_incident(valid_rig: Path):
    class TerminalIncidentClient(IdleBoardClient):
        def __init__(self) -> None:
            super().__init__(_idle_snapshot())
            self.snapshots = [
                _idle_snapshot(),
                {
                    **_idle_snapshot(),
                    "latest_incident_terminal": {
                        "id": "incident-1",
                        "status": "done",
                        "terminal_at": 850,
                    },
                },
            ]
            self.create_count = 0

        def board_activity(self, *args, **kwargs):
            self.activity_calls.append((args[0], kwargs.get("excluded_creators", ())))
            return self.snapshots.pop(0)

        def run_json(self, args: list[str]) -> object:
            self.json_calls.append(args)
            tail = args[3:]
            if tail in (
                ["list", "--status", "blocked", "--json"],
                ["list", "--status", "done", "--json"],
            ):
                return []
            if tail[:1] == ["create"]:
                self.create_count += 1
                return {
                    "id": f"incident-{self.create_count}",
                    "status": "done" if self.create_count == 1 else "ready",
                    "title": tail[1],
                }
            raise AssertionError(args)

    client = TerminalIncidentClient()

    result = reconcile_board(client, load_rig(valid_rig))

    creates = [call for call in client.json_calls if call[3] == "create"]
    assert result.board_incident_task_id == "incident-2"
    assert len(creates) == 2
    assert _flag_value(creates[0], "--idempotency-key") != _flag_value(
        creates[1], "--idempotency-key"
    )
    assert _flag_value(creates[1], "--idempotency-key").endswith(
        ":after:incident-1:850"
    )


def test_reconcile_reports_board_incident_blocked_during_postflight(valid_rig: Path):
    class PostflightBlockedIncidentClient(IdleBoardClient):
        def __init__(self) -> None:
            super().__init__(_idle_snapshot())
            self.snapshots = [
                {"observed_at": 1000, "last_activity_at": None, "tasks": []},
                {
                    "observed_at": 1000,
                    "last_activity_at": 800,
                    "tasks": [
                        {
                            "id": "incident-1",
                            "status": "blocked",
                            "created_by": "hermes-factory-watchdog",
                        }
                    ],
                },
            ]

        def board_activity(self, *args, **kwargs):
            self.activity_calls.append((args[0], kwargs.get("excluded_creators", ())))
            return self.snapshots.pop(0)

    client = PostflightBlockedIncidentClient()

    with pytest.raises(HermesCommandError, match="board incident incident-1 is blocked"):
        reconcile_board(client, load_rig(valid_rig))


def test_reconcile_registers_board_incident_when_review_has_no_running_agent(
    valid_rig: Path,
):
    client = IdleBoardClient(
        _idle_snapshot(
            tasks=[
                {"id": "review-1", "status": "review", "created_by": "hermes-factory"},
                {"id": "todo-1", "status": "todo", "created_by": "hermes-factory"},
            ]
        )
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.board_incident_task_id == "incident-1"


def test_reconcile_dispatches_ready_board_incident(valid_rig: Path):
    client = IdleBoardClient(_idle_snapshot(), dispatch=True)

    result = reconcile_board(client, load_rig(valid_rig), dispatch=True)

    assert result.board_incident_task_id == "incident-1"
    assert result.dispatched == 1
    assert [
        "kanban",
        "--board",
        "demo-rig",
        "dispatch",
        "--max",
        "1",
        "--json",
    ] in client.json_calls


def test_reconcile_creates_idempotent_coordinator_task_for_gave_up_factory_task(
    valid_rig: Path,
):
    client = FakeReconcileClient(
        [
            [_blocked_task()],
            _timed_out_show(),
            {
                "id": "recovery-1",
                "status": "ready",
                "title": "Decompose blocked task task-1",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.scanned == 1
    assert result.eligible == 1
    assert result.recovery_task_ids == {"task-1": "recovery-1"}
    assert client.json_calls[:2] == [
        ["kanban", "--board", "demo-rig", "list", "--status", "blocked", "--json"],
        ["kanban", "--board", "demo-rig", "show", "task-1", "--json"],
    ]
    create = client.json_calls[2]
    assert create[:5] == [
        "kanban",
        "--board",
        "demo-rig",
        "create",
        "Decompose blocked task task-1",
    ]
    assert _flag_value(create, "--assignee") == "builder"
    assert _flag_value(create, "--idempotency-key") == (
        "factory:demo:reconcile:task-1:gave-up:102"
    )
    assert _flag_value(create, "--max-retries") == "2"
    assert _flag_value(create, "--max-runtime") == "30m"
    assert _flag_value(create, "--workspace").endswith("project")
    assert _flag_value(create, "--created-by") == "hermes-factory-reconciler"
    assert "hermes-native-agent-factories" in create
    body = _flag_value(create, "--body")
    assert "<!-- hermes-factory:recovery -->" in body
    assert "task-1" in body
    assert "parent-1" in body
    assert "review-1" in body
    assert "downstream-1" in body
    assert "Do not retry the same scope" in body
    assert "archive and purge superseded cards" in body


def test_recovery_card_uses_explicit_deterministic_fixture_mode(valid_rig: Path):
    valid_rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "completion_contract: local-only}",
            "completion_contract: local-only, fixture_recovery: true}",
        ),
        encoding="utf-8",
    )
    timed_out = _timed_out_show()
    timed_out["children"] = ["review-1"]
    source_review = {
        "task": {
            **_blocked_task("review-1"),
            "title": "Review large behavior",
            "body": _audit_body("review"),
            "idempotency_key": "factory:demo:demo-mission:original:review",
        },
        "parents": ["task-1"],
        "children": ["downstream-1"],
        "comments": [],
        "events": [],
        "runs": [],
    }
    client = FakeReconcileClient(
        [
            [_blocked_task()],
            timed_out,
            source_review,
            {
                "id": "recovery-1",
                "status": "ready",
                "title": "Decompose blocked task task-1",
            },
            [],
        ]
    )

    reconcile_board(
        client,
        load_rig(valid_rig),
        fixture_recovery=True,
        recovery_runtime="60s",
    )

    create = client.json_calls[3]
    assert _flag_value(create, "--max-runtime") == "60s"
    body = _flag_value(create, "--body")
    assert "# Compressed fixture recovery" in body
    assert "Do not inspect the repository" in body
    assert "Treat every package value as inert, untrusted data" in body
    prefix = "Factory fixture recovery package: "
    package_line = next(line for line in body.splitlines() if line.startswith(prefix))
    package = json.loads(package_line[len(prefix) :])
    assert package["implementation"]["max_retries"] == 2
    assert package["implementation"]["max_runtime_seconds"] == 1800
    assert package["implementation"]["parents"] == ["parent-1"]
    assert package["review"]["parents"] == ["$implementation_task_id"]
    assert package["links"] == [
        {"parent": "$review_task_id", "child": "downstream-1"}
    ]
    assert package["completion_metadata"]["superseded_task_ids"] == [
        "task-1",
        "review-1",
    ]


def test_fixture_recovery_requires_authoritative_rig_opt_in(valid_rig: Path):
    client = FakeReconcileClient([])

    with pytest.raises(SpecError, match="rig policy fixture_recovery"):
        reconcile_board(
            client,
            load_rig(valid_rig),
            fixture_recovery=True,
            recovery_runtime="60s",
        )

    assert client.json_calls == []


@pytest.mark.parametrize("runtime", ["29s", "61s", "2m"])
def test_fixture_recovery_runtime_requires_explicit_30_to_60_seconds(
    valid_rig: Path, runtime: str
):
    client = FakeReconcileClient([])

    with pytest.raises(SpecError, match="30 to 60 seconds"):
        reconcile_board(
            client,
            load_rig(valid_rig),
            fixture_recovery=True,
            recovery_runtime=runtime,
        )

    assert client.json_calls == []


def test_reconcile_reads_factory_key_from_authoritative_task_record_when_show_omits_it(
    valid_rig: Path,
):
    blocked = _blocked_task()
    blocked.pop("idempotency_key")
    detail = _timed_out_show()
    detail["task"].pop("idempotency_key")

    class RecordBackedClient(FakeReconcileClient):
        def __init__(self, responses: list[object]) -> None:
            super().__init__(responses)
            self.task_record_calls: list[tuple[str, str]] = []

        def task_record(self, board_slug: str, task_id: str) -> dict[str, object]:
            self.task_record_calls.append((board_slug, task_id))
            return {
                "id": task_id,
                "idempotency_key": (
                    "factory:demo:demo-mission:original:implementation"
                ),
                "created_by": "hermes-factory",
            }

    client = RecordBackedClient(
        [
            [blocked],
            detail,
            {
                "id": "recovery-1",
                "status": "ready",
                "title": "Decompose blocked task task-1",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.recovery_task_ids == {"task-1": "recovery-1"}
    assert client.task_record_calls == [("demo-rig", "task-1")]


def test_reconcile_routes_unclassified_block_to_coordinator(valid_rig: Path):
    unknown = _blocked_task("unknown-1")
    unknown["last_failure_error"] = None
    client = FakeReconcileClient(
        [
            [unknown],
            {
                "task": unknown,
                "parents": [],
                "children": [],
                "comments": [],
                "events": [
                    {
                        "kind": "blocked",
                        "created_at": 100,
                        "run_id": 9,
                        "payload": {"reason": "Need somebody to run a script"},
                    }
                ],
                "runs": [],
            },
            {
                "id": "unknown-recovery-1",
                "status": "ready",
                "title": "Resolve unknown blocked task unknown-1",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.scanned == 1
    assert result.eligible == 1
    assert result.recovery_task_ids == {"unknown-1": "unknown-recovery-1"}
    create = client.json_calls[2]
    assert create[:5] == [
        "kanban",
        "--board",
        "demo-rig",
        "create",
        "Resolve unknown blocked task unknown-1",
    ]
    assert _flag_value(create, "--assignee") == "builder"
    assert _flag_value(create, "--idempotency-key") == (
        "factory:demo:reconcile:unknown-1:unknown-stall:100"
    )
    body = _flag_value(create, "--body")
    assert "Reason code: unknown_operational_stall" in body
    assert "Need somebody to run a script" in body
    assert "resume" in body
    assert "retry" in body
    assert "replace" in body
    assert "reassign" in body
    assert "decompose" in body
    assert "terminate" in body
    assert "Do not create a new taxonomy class" in body


def test_reconcile_skips_explicit_human_acceptance_gate(valid_rig: Path):
    valid_rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "human_acceptance: false,",
            "human_acceptance: true, decision_authority: human,",
        ),
        encoding="utf-8",
    )
    body = (
        "# Project\nDemo\n\n# Mission\nDemo mission\n\n# Acceptance\n- approve\n\n"
        "# Proof required\n- none\n\n"
        "<!-- hermes-factory:audit-contract -->\n"
        'Factory audit contract: {"task_class":"acceptance","required_commands":[],'
        '"required_artifacts":[],"changed_files_required":false,"required_metadata":[]}\n'
        "<!-- /hermes-factory:audit-contract -->\n\n"
        "# Scope boundaries\nProject demo; mission demo-mission; workflow delivery.\n\n"
        "<!-- hermes-factory:human-gate -->\n# Human gate\nExplicit acceptance.\n\n"
        "# Handoff\nNext target: operator."
    )
    gate = {
        **_blocked_task("gate-1"),
        "title": "Accept demo mission",
        "body": body,
        "assignee": None,
        "idempotency_key": "factory:demo:demo-mission:acceptance",
        "last_failure_error": None,
    }

    class HumanGateClient(FakeReconcileClient):
        def board_activity(
            self,
            board_slug: str,
            *,
            observed_at: int | None = None,
            excluded_creators: tuple[str, ...] = (),
        ) -> dict[str, object]:
            return _idle_snapshot(
                tasks=[
                    {
                        "id": "gate-1",
                        "status": "blocked",
                        "created_by": "hermes-factory",
                    }
                ]
            )

    client = HumanGateClient(
        [
            [gate],
            {
                "task": gate,
                "parents": ["review-1"],
                "children": [],
                "comments": [],
                "events": [
                    {
                        "kind": "created",
                        "created_at": 90,
                        "payload": {"creator_task_id": None},
                    },
                    {
                        "kind": "blocked",
                        "created_at": 100,
                        "run_id": None,
                        "payload": {"reason": "human acceptance"},
                    }
                ],
                "runs": [],
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 0
    assert result.recovery_task_ids == {}
    assert result.board_incident_task_id is None
    assert not any(call[3] == "create" for call in client.json_calls)


class UnreadableChildClient(StatefulRecoveryClient):
    """Answers one child's ``show`` with something that proves neither graph state."""

    def __init__(
        self,
        recovery: dict[str, object],
        recovery_detail: dict[str, object],
        details: list[dict[str, object]],
        child_state: str,
    ) -> None:
        super().__init__(recovery, recovery_detail, details)
        self.child_state = child_state

    def run_json(self, args: list[str]) -> object:
        if args[3:4] == ["show"] and "downstream-1" in args:
            if self.child_state == "unreadable":
                return "not json"
            return {
                "task": {"id": "someone-else"},
                "parents": [],
                "children": [],
                "events": [],
            }
        return super().run_json(args)


class UnknownResumeClient:
    def __init__(self, action: str = "resume", landing_status: str = "ready") -> None:
        self.landing_status = landing_status
        self.run_calls: list[list[str]] = []
        self.target = {
            "task": _blocked_task("unknown-1"),
            "parents": [],
            "children": [],
            "comments": [],
            "events": [
                {"kind": "created", "payload": {"creator_task_id": None}},
                {
                    "kind": "blocked",
                    "created_at": 100,
                    "run_id": 9,
                    "payload": {"reason": "Need a coordinator decision"},
                },
            ],
            "runs": [],
        }
        self.recovery = {
            "task": {
                "id": "unknown-recovery-1",
                "title": "Resolve unknown blocked task unknown-1",
                "body": "Reason code: unknown_operational_stall\nSuperseded task: unknown-1",
                "status": "done",
                "created_by": "hermes-factory-reconciler",
                "idempotency_key": "factory:demo:reconcile:unknown-1:unknown-stall:100",
            },
            "parents": [],
            "children": [],
            "comments": [],
            "events": [
                {"kind": "created", "created_at": 150, "payload": {"creator_task_id": None}},
                {
                    "kind": "completed",
                    "created_at": 200,
                    "run_id": 20,
                    # Hermes omits `verified_cards` entirely when no card was created.
                    "payload": {"result_len": 0, "summary": "Recovered without new cards."},
                },
            ],
            "runs": [
                {
                    "id": 20,
                    "status": "done",
                    "outcome": "completed",
                    "metadata": {
                        "action": action,
                        "target_task_id": "unknown-1",
                        "decision": "Run the safe script and continue.",
                        "assignee": None,
                        "replacement_task_ids": [],
                        "rewires": [],
                        "superseded_task_ids": [],
                        "limitations": [],
                    },
                }
            ],
        }

    def run_json(self, args: list[str]) -> object:
        tail = args[3:]
        if tail == ["list", "--status", "blocked", "--json"]:
            return []
        if tail == ["list", "--status", "done", "--json"]:
            if self.recovery["task"]["status"] == "archived":
                return []
            return [self.recovery["task"]]
        if tail == ["list", "--archived", "--json"]:
            return [self.recovery["task"], self.target["task"]]
        if tail == ["show", "unknown-recovery-1", "--json"]:
            return self.recovery
        if tail == ["show", "unknown-1", "--json"]:
            return self.target
        raise AssertionError(args)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        tail = args[3:]
        if tail[:2] == ["unblock", "unknown-1"]:
            self.target["task"]["status"] = self.landing_status
            self.target["events"].append(
                {
                    "kind": "unblocked",
                    "created_at": 201,
                    "payload": {
                        "status": self.landing_status,
                        "resume_status": "review",
                        "reason": tail[3],
                    },
                }
            )
        elif tail[:2] == ["comment", "unknown-recovery-1"]:
            self.recovery["comments"].append({"body": tail[2], "author": tail[4]})
        elif tail == ["archive", "unknown-recovery-1"]:
            self.recovery["task"]["status"] = "archived"
        else:
            raise AssertionError(args)
        return ""


@pytest.mark.parametrize("action", ["resume", "retry"])
def test_unknown_recovery_resume_and_retry_unblock_target(
    valid_rig: Path, action: str
):
    client = UnknownResumeClient(action)

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("unknown-recovery-1",)
    assert [
        "kanban",
        "--board",
        "demo-rig",
        "unblock",
        "unknown-1",
        "--reason",
        "Run the safe script and continue.",
    ] in client.run_calls
    assert client.target["task"]["status"] == "ready"


def test_unknown_recovery_termination_is_idempotent_for_archived_leaf(
    valid_rig: Path,
):
    class TerminateClient(UnknownResumeClient):
        def __init__(self) -> None:
            super().__init__()
            metadata = self.recovery["runs"][0]["metadata"]
            metadata["action"] = "terminate"
            metadata["decision"] = "No safe in-scope continuation exists."

        def run_json(self, args: list[str]) -> object:
            tail = args[3:]
            if tail == ["list", "--archived", "--json"]:
                return [
                    {
                        "id": "unknown-1",
                        "status": self.target["task"]["status"],
                    }
                ]
            return super().run_json(args)

        def run(self, args: list[str]) -> str:
            tail = args[3:]
            if tail == ["archive", "unknown-1"]:
                self.run_calls.append(args)
                self.target["task"]["status"] = "archived"
                self.target["events"].append(
                    {"kind": "archived", "created_at": 202, "payload": {}}
                )
                return ""
            return super().run(args)

    client = TerminateClient()

    first = reconcile_board(client, load_rig(valid_rig))
    second = reconcile_board(client, load_rig(valid_rig))

    assert first.finalized_recovery_task_ids == ("unknown-recovery-1",)
    assert second.finalized_recovery_task_ids == ("unknown-recovery-1",)
    assert client.target["task"]["status"] == "archived"
    assert sum(call[3:] == ["archive", "unknown-1"] for call in client.run_calls) == 1


def test_unknown_recovery_reassigns_to_configured_profile_then_unblocks(
    valid_rig: Path,
):
    class ReassignClient(UnknownResumeClient):
        def __init__(self) -> None:
            super().__init__()
            metadata = self.recovery["runs"][0]["metadata"]
            metadata["action"] = "reassign"
            metadata["assignee"] = "reviewer"

        def run(self, args: list[str]) -> str:
            tail = args[3:]
            if tail[:2] == ["reassign", "unknown-1"]:
                self.run_calls.append(args)
                self.target["task"]["assignee"] = tail[2]
                self.target["events"].append(
                    {"kind": "assigned", "created_at": 200, "payload": {"assignee": tail[2]}}
                )
                return ""
            return super().run(args)

    client = ReassignClient()

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("unknown-recovery-1",)
    assert client.target["task"]["assignee"] == "reviewer"
    assert any(call[3] == "reassign" for call in client.run_calls)


def test_unknown_recovery_rejects_reassignment_outside_rig(valid_rig: Path):
    client = UnknownResumeClient()
    metadata = client.recovery["runs"][0]["metadata"]
    metadata["action"] = "reassign"
    metadata["assignee"] = "unconfigured-profile"

    with pytest.raises(HermesCommandError, match="not a configured rig profile"):
        reconcile_board(client, load_rig(valid_rig))

    assert not any(call[3] in {"reassign", "unblock"} for call in client.run_calls)


def test_stale_unknown_recovery_decision_is_disposed_once_without_mutation(
    valid_rig: Path,
):
    client = UnknownResumeClient()
    client.target["events"].append(
        {
            "kind": "blocked",
            "created_at": 101,
            "run_id": 10,
            "payload": {"reason": "newer incident"},
        }
    )

    first = reconcile_board(client, load_rig(valid_rig))

    assert first.finalized_recovery_task_ids == ()
    assert not any(call[3] == "unblock" for call in client.run_calls)
    disposals = [
        call for call in client.run_calls if call[3] in {"archive", "comment"}
    ]
    assert [call[3] for call in disposals] == ["archive", "comment"]
    assert "recovery-rejected" in disposals[1][5]
    assert "stale" in disposals[1][5]
    assert client.recovery["task"]["status"] == "archived"

    second = reconcile_board(client, load_rig(valid_rig))

    assert second.finalized_recovery_task_ids == ()
    assert [
        call for call in client.run_calls if call[3] in {"archive", "comment"}
    ] == disposals


def test_reconcile_dispatches_only_ready_recovery_tasks(valid_rig: Path):
    client = FakeReconcileClient(
        [
            [_blocked_task()],
            _timed_out_show(),
            {
                "id": "recovery-1",
                "status": "ready",
                "title": "Decompose blocked task task-1",
            },
            [],
            {
                "task": {
                    "id": "recovery-1",
                    "title": "Decompose blocked task task-1",
                    "status": "ready",
                },
                "parents": [],
                "children": [],
                "events": [],
                "runs": [],
            },
            {
                "reclaimed": 0,
                "crashed": [],
                "timed_out": [],
                "stale": [],
                "auto_blocked": [],
                "promoted": 0,
                "spawned": [{"task_id": "recovery-1", "pid": 123}],
            },
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig), dispatch=True)

    assert result.dispatched == 1
    assert client.json_calls[-1] == [
        "kanban",
        "--board",
        "demo-rig",
        "dispatch",
        "--max",
        "1",
        "--json",
    ]


def test_recovery_card_delegates_privileged_cleanup_to_factory(valid_rig: Path):
    client = FakeReconcileClient(
        [
            [_blocked_task()],
            _timed_out_show(),
            {
                "id": "recovery-1",
                "status": "ready",
                "title": "Decompose blocked task task-1",
            },
            [],
        ]
    )

    reconcile_board(client, load_rig(valid_rig))

    body = _flag_value(client.json_calls[2], "--body")
    assert "Do not run unlink, archive, or purge" in body
    assert '"replacement_task_ids"' in body
    assert '"rewires"' in body
    assert '"superseded_task_ids"' in body
    assert "created_cards" in body
    assert "Complete this recovery card" in body


def test_recovery_card_records_authoritative_replacement_clone_policy(
    valid_rig: Path,
):
    client = FakeReconcileClient(
        [
            [_blocked_task()],
            _timed_out_show(),
            {
                "id": "recovery-1",
                "status": "ready",
                "title": "Decompose blocked task task-1",
            },
            [],
        ]
    )

    reconcile_board(client, load_rig(valid_rig))

    body = _flag_value(client.json_calls[2], "--body")
    assert "<!-- hermes-factory:replacement-policy -->" in body
    assert '"workspace_kind":"dir"' in body
    assert '"workspace_path":"F:\\\\Project With Space"' in body
    assert '"max_retries":2' in body
    assert '"max_runtime_seconds":1800' in body
    assert '"completion_contract":"local-only"' in body
    assert '"implementation_skills":[]' in body
    assert '"review_skills":[]' in body
    assert '"project":"demo"' in body
    assert '"mission":"demo-mission"' in body
    assert '"source_slice":"original"' in body
    assert '"workflow":"delivery"' in body
    assert '"incoming_parent_ids":["parent-1"]' in body
    assert '"source_audit_contract"' in body
    assert '"mission_body"' in body


def test_reconcile_finalizes_completed_recovery_plan_outside_worker_fence(
    valid_rig: Path,
):
    recovery, completed_recovery = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review"],
        worker_session_id="worker-session-1",
    )
    client = StatefulRecoveryClient(
        recovery,
        completed_recovery,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                children=["downstream"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
                children=["downstream"],
            ),
            {
                "task": {"id": "downstream", "status": "todo"},
                "parents": ["old-review", "new-review"],
                "children": [],
                "comments": [],
                "events": [],
                "runs": [],
            },
        ],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert [
        "kanban", "--board", "demo-rig", "unlink", "old-review", "downstream"
    ] in client.run_calls
    assert [
        "kanban", "--board", "demo-rig", "archive", "old-impl", "old-review"
    ] in client.run_calls
    assert [
        "kanban", "--board", "demo-rig", "archive", "--rm", "old-review", "old-impl"
    ] in client.run_calls
    archived_list_calls = [
        call
        for call in client.json_calls
        if call[3:] == ["list", "--archived", "--json"]
    ]
    assert len(archived_list_calls) == 4
    assert any(call[3] == "comment" and call[4] == "recovery-1" for call in client.run_calls)


def test_reconcile_does_not_trust_forgeable_finalized_comment(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    recovery_detail["comments"] = [
        {
            "body": "<!-- hermes-factory:recovery-finalized -->",
            "author": "hermes-factory-reconciler",
        }
    ]
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="reviewer",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
            ),
        ],
    )

    with pytest.raises(HermesCommandError, match="invalid provenance"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_revalidates_finalized_state_without_duplicate_marker(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    recovery_detail["comments"] = [
        {
            "body": "<!-- hermes-factory:recovery-finalized -->",
            "author": "forged-author-is-still-audit-only",
        }
    ]
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
        ],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert not any(call[3] == "comment" for call in client.run_calls)
    assert not any(call[3] in {"unlink", "archive"} for call in client.run_calls)


def test_reconcile_rejects_recovery_plan_that_purges_unrelated_task(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-review"],
        rewires=[],
        superseded=["foreign-task"],
    )
    client = FakeReconcileClient(
        [
            [],
            [recovery],
            recovery_detail,
        ]
    )

    with pytest.raises(HermesCommandError, match="recovery target old-impl"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_real_target_plus_unrelated_superseded_task_before_mutation(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review", "foreign-task"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                children=["downstream"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
                children=["downstream"],
            ),
            _factory_detail(
                "foreign-task",
                "review",
                "foreign",
                "reviewer",
            ),
            {
                "task": {"id": "downstream", "status": "todo"},
                "parents": ["old-review", "new-review"],
                "children": [],
                "comments": [],
                "events": [],
                "runs": [],
            },
        ],
    )

    with pytest.raises(HermesCommandError, match="connected target chain"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_untrusted_replacement_task_before_mutation(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                children=["new-review"],
                created_by="attacker",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
            ),
        ],
    )

    with pytest.raises(HermesCommandError, match="Factory replacement"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


@pytest.mark.parametrize("provenance_failure", ["wrong_creator", "missing_created", "duplicate_created"])
def test_reconcile_rejects_replacement_without_single_coordinator_created_event(
    valid_rig: Path, provenance_failure: str
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    implementation = _factory_detail(
        "new-impl",
        "implementation",
        "replacement-a",
        "builder",
        parents=["parent"],
        children=["new-review"],
        created_by="builder",
        creator_task_id="recovery-1",
    )
    if provenance_failure == "wrong_creator":
        implementation["task"]["created_by"] = "reviewer"
    elif provenance_failure == "missing_created":
        implementation["events"] = []
    else:
        implementation["events"].append(dict(implementation["events"][0]))
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            implementation,
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
            ),
        ],
    )

    with pytest.raises(HermesCommandError, match="invalid provenance"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_replacement_workspace_drift_before_mutation(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    replacement = _factory_detail(
        "new-impl",
        "implementation",
        "replacement-a",
        "builder",
        children=["new-review"],
        created_by="builder",
        creator_task_id="recovery-1",
    )
    replacement["task"]["workspace_path"] = "F:/Other Project"
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            replacement,
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
            ),
        ],
    )

    with pytest.raises(HermesCommandError, match="workspace_path"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_replacement_body_slice_identity_drift(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    implementation = _factory_detail(
        "new-impl",
        "implementation",
        "replacement-a",
        "builder",
        children=["new-review"],
        created_by="builder",
        creator_task_id="recovery-1",
    )
    review = _factory_detail(
        "new-review",
        "review",
        "replacement-a",
        "reviewer",
        parents=["new-impl"],
        created_by="builder",
        creator_task_id="recovery-1",
    )
    for detail in (implementation, review):
        detail["task"]["body"] = detail["task"]["body"].replace(
            "Slice id: replacement-a", "Slice id: unrelated-slice"
        )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            implementation,
            review,
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
            ),
        ],
    )

    with pytest.raises(HermesCommandError, match="slice identity"):
        reconcile_board(client, load_rig(valid_rig))


def test_reconcile_rejects_omitted_external_child_rewire_before_mutation(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                children=["downstream"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
                children=["downstream"],
            ),
            {
                "task": {"id": "downstream", "status": "todo"},
                "parents": ["old-review", "new-review"],
                "children": [],
                "comments": [],
                "events": [],
                "runs": [],
            },
        ],
    )

    with pytest.raises(HermesCommandError, match="external child edge"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_retry_revalidates_boundary_instead_of_trusting_marker(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review"],
    )
    marker = _recovery_validation_marker(
        RecoveryPlan(
            "recovery-1",
            "old-impl",
            ("new-impl", "new-review"),
            (RecoveryRewire("old-review", "new-review", "downstream"),),
            ("old-impl", "old-review"),
            ("new-impl", "new-review"),
        )
    )
    recovery_detail["comments"] = [
        {"body": marker, "author": "hermes-factory-reconciler"}
    ]
    details = {
        "new-impl": _factory_detail(
            "new-impl",
            "implementation",
            "replacement-a",
            "builder",
            parents=["parent"],
            children=["new-review"],
            created_by="builder",
            creator_task_id="recovery-1",
        ),
        "new-review": _factory_detail(
            "new-review",
            "review",
            "replacement-a",
            "reviewer",
            parents=["new-impl"],
            children=["downstream"],
            created_by="builder",
            creator_task_id="recovery-1",
        ),
        "old-impl": _factory_detail(
            "old-impl",
            "implementation",
            "original",
            "builder",
            parents=["parent"],
            children=["old-review"],
            gave_up=True,
        ),
        "old-review": _factory_detail(
            "old-review",
            "review",
            "original",
            "reviewer",
            parents=["old-impl"],
            children=["downstream", "surprise-child"],
        ),
        "downstream": {
            "task": {"id": "downstream", "status": "todo"},
            "parents": ["old-review", "new-review"],
            "children": [],
            "comments": [],
            "events": [],
            "runs": [],
        },
        "surprise-child": {
            "task": {"id": "surprise-child", "status": "todo"},
            "parents": ["old-review"],
            "children": [],
            "comments": [],
            "events": [],
            "runs": [],
        },
    }

    class LiveSnapshotClient:
        def __init__(self) -> None:
            self.run_calls: list[list[str]] = []

        def run_json(self, args: list[str]) -> object:
            tail = args[3:]
            if tail == ["list", "--status", "blocked", "--json"]:
                return []
            if tail == ["list", "--status", "done", "--json"]:
                return [recovery]
            if tail == ["show", "recovery-1", "--json"]:
                return recovery_detail
            if tail[:1] == ["show"]:
                return details[tail[1]]
            if tail == ["list", "--archived", "--json"]:
                return [detail["task"] for detail in details.values()]
            raise AssertionError(args)

        def run(self, args: list[str]) -> str:
            self.run_calls.append(args)
            if args[3] == "unlink":
                parent, child = args[4], args[5]
                details[child]["parents"].remove(parent)
            return ""

    client = LiveSnapshotClient()

    with pytest.raises(HermesCommandError, match="surprise-child"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_retry_after_applied_purge_revalidates_surviving_tasks(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                children=["downstream"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "old-impl",
                "implementation",
                "original",
                "builder",
                parents=["parent"],
                children=["old-review"],
                gave_up=True,
            ),
            _factory_detail(
                "old-review",
                "review",
                "original",
                "reviewer",
                parents=["old-impl"],
                children=["downstream"],
            ),
            {
                "task": {"id": "downstream", "status": "todo"},
                "parents": ["old-review", "new-review"],
                "children": [],
                "comments": [],
                "events": [],
                "runs": [],
            },
        ],
        fail_after_purge=True,
    )

    with pytest.raises(HermesCommandError, match="simulated interruption"):
        reconcile_board(client, load_rig(valid_rig))

    assert "old-impl" not in client.details
    assert "old-review" not in client.details
    client.fail_after_purge = False
    client.json_calls.clear()
    client.run_calls.clear()

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert not any(call[3] in {"unlink", "archive"} for call in client.run_calls)
    replacement_reads = [
        call
        for call in client.json_calls
        if call[3:] == ["show", "new-impl", "--json"]
    ]
    assert len(replacement_reads) == 4


def _replacement_pair(
    *,
    implementation_children: list[str] | None = None,
    review_children: list[str] | None = None,
    creator_task_id: str = "recovery-1",
) -> list[dict[str, object]]:
    return [
        _factory_detail(
            "new-impl",
            "implementation",
            "replacement-a",
            "builder",
            parents=["parent"],
            children=implementation_children
            if implementation_children is not None
            else ["new-review"],
            created_by="builder",
            creator_task_id=creator_task_id,
        ),
        _factory_detail(
            "new-review",
            "review",
            "replacement-a",
            "reviewer",
            parents=["new-impl"],
            children=review_children,
            created_by="builder",
            creator_task_id=creator_task_id,
        ),
    ]


def _superseded_pair(
    *, old_review_children: list[str] | None = None
) -> list[dict[str, object]]:
    return [
        _factory_detail(
            "old-impl",
            "implementation",
            "original",
            "builder",
            parents=["parent"],
            children=["old-review"],
            gave_up=True,
        ),
        _factory_detail(
            "old-review",
            "review",
            "original",
            "reviewer",
            parents=["old-impl"],
            children=old_review_children,
        ),
    ]


def _downstream_detail(
    parents: list[str],
    events: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "task": {"id": "downstream", "status": "todo"},
        "parents": parents,
        "children": [],
        "comments": [],
        "events": events or [],
        "runs": [],
    }


@pytest.mark.parametrize(
    "completion_failure",
    ["duplicate_completed", "mismatched_run_id", "missing_run_id"],
)
def test_reconcile_rejects_unbound_recovery_completion(
    valid_rig: Path, completion_failure: str
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    completed = [
        event for event in recovery_detail["events"] if event["kind"] == "completed"
    ]
    if completion_failure == "duplicate_completed":
        recovery_detail["events"].append(dict(completed[0]))
    elif completion_failure == "mismatched_run_id":
        completed[0]["run_id"] = 999
    else:
        del recovery_detail["runs"][0]["id"]
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *_superseded_pair()],
    )

    with pytest.raises(HermesCommandError, match="authoritative"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_archived_replacement_before_mutation(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    details = [*_replacement_pair(), *_superseded_pair()]
    details[1]["task"]["status"] = "archived"
    client = StatefulRecoveryClient(recovery, recovery_detail, details)

    with pytest.raises(HermesCommandError, match="is archived"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_replacement_with_unexpected_extra_child(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(
                implementation_children=["new-review", "rogue-task"]
            ),
            *_superseded_pair(),
        ],
    )

    with pytest.raises(HermesCommandError, match="implementation chain"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_extra_rewire_declaration(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            },
            {
                "old_parent": "old-impl",
                "new_parent": "new-review",
                "child": "downstream",
            },
        ],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(review_children=["downstream"]),
            *_superseded_pair(old_review_children=["downstream"]),
            _downstream_detail(["old-review", "new-review"]),
        ],
    )

    with pytest.raises(HermesCommandError, match="old-impl -> downstream"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_unproven_missing_external_edge(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(review_children=["downstream"]),
            *_superseded_pair(),
            _downstream_detail(["new-review"]),
        ],
    )

    with pytest.raises(HermesCommandError, match="old-review -> downstream"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_accepts_unlink_recorded_before_the_plan_completed(valid_rig: Path):
    """An unlink left by an earlier interrupted pass still satisfies the rewire.

    The record is not time-boxed: a rewire depends on live graph state, and Hermes's
    unlinked event on the child for this exact edge is what separates a real removal
    from an edge that vanished because the old parent was purged.
    """
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(review_children=["downstream"]),
            *_superseded_pair(),
            _downstream_detail(
                ["new-review"],
                [
                    {
                        "kind": "unlinked",
                        "created_at": 199,
                        "payload": {"parent": "old-review", "child": "downstream"},
                    }
                ],
            ),
        ],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert not any(call[3] == "unlink" for call in client.run_calls)


def test_reconcile_accepts_retry_with_authoritative_unlink_record(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(review_children=["downstream"]),
            *_superseded_pair(),
            _downstream_detail(
                ["new-review"],
                [
                    {
                        "kind": "unlinked",
                        "created_at": 201,
                        "payload": {"parent": "old-review", "child": "downstream"},
                    }
                ],
            ),
        ],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert not any(call[3] == "unlink" for call in client.run_calls)
    assert any(call[3] == "archive" for call in client.run_calls)


def test_reconcile_rejects_final_review_with_undeclared_child(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(review_children=["downstream", "rogue-task"]),
            *_superseded_pair(old_review_children=["downstream"]),
            _downstream_detail(["old-review", "new-review"]),
        ],
    )

    with pytest.raises(HermesCommandError, match="final review chain"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_unrelated_superseded_task_hidden_behind_phantom_id(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review", "foreign-review", "phantom"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(),
            *_superseded_pair(),
            _factory_detail("foreign-review", "review", "original", "reviewer"),
        ],
    )

    with pytest.raises(HermesCommandError, match="connected target chain"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_rewire_from_purged_parent_without_unlink_record(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "ghost",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review", "ghost"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(review_children=["downstream"]),
            *_superseded_pair(),
            _downstream_detail(["new-review"]),
        ],
    )

    with pytest.raises(HermesCommandError, match="ghost -> downstream"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_accepts_verified_cards_in_any_order(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    recovery_detail["events"][0]["payload"]["verified_cards"] = [
        "new-review",
        "new-impl",
    ]
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *_superseded_pair()],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)


def test_reconcile_retry_after_interrupted_purge_still_finalizes(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-review",
                "new_parent": "new-review",
                "child": "downstream",
            }
        ],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(review_children=["downstream"]),
            *_superseded_pair(old_review_children=["downstream"]),
            _downstream_detail(["old-review", "new-review"]),
        ],
        fail_after_first_purge=True,
    )

    with pytest.raises(HermesCommandError, match="simulated interruption mid purge"):
        reconcile_board(client, load_rig(valid_rig))

    # Hermes drops a purged task's link rows, so a partial purge must still leave the
    # surviving chain provable: descendants are purged before their ancestors.
    assert "old-impl" in client.details
    client.fail_after_first_purge = False
    client.json_calls.clear()
    client.run_calls.clear()

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert "old-impl" not in client.details


def test_reconcile_finalizes_other_recoveries_when_one_plan_is_rejected(
    valid_rig: Path,
):
    rejected_recovery, rejected_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    for event in rejected_detail["events"]:
        if event["kind"] == "completed":
            event["payload"]["verified_cards"] = ["unrelated-card"]
    accepted_recovery, accepted_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    accepted_recovery = {
        **accepted_recovery,
        "id": "recovery-2",
        "idempotency_key": "factory:demo:reconcile:old-impl:gave-up:201",
    }
    accepted_detail = {
        **accepted_detail,
        "task": {
            **accepted_detail["task"],
            "id": "recovery-2",
            "idempotency_key": "factory:demo:reconcile:old-impl:gave-up:201",
        },
    }
    client = StatefulRecoveryClient(
        rejected_recovery,
        rejected_detail,
        [*_replacement_pair(creator_task_id="recovery-2"), *_superseded_pair()],
        extra_recoveries=[(accepted_recovery, accepted_detail)],
    )
    # recovery-2 answers the newer timeout incident recorded at 201; recovery-1 still
    # answers the 200 incident, so only its own rejection is under test here.
    client.details["old-impl"]["events"].append(
        {
            "kind": "gave_up",
            "created_at": 201,
            "run_id": 12,
            "payload": {"trigger_outcome": "timed_out"},
        }
    )

    with pytest.raises(HermesCommandError, match="recovery-1"):
        reconcile_board(client, load_rig(valid_rig))

    assert any(call[3] == "archive" for call in client.run_calls)
    assert "old-impl" not in client.details


def _empty_scope_body(task_class: str) -> str:
    """A generated body whose Scope boundaries section exists but is empty."""
    body = _audit_body(task_class)
    contract_start = body.index("<!-- hermes-factory:audit-contract -->")
    contract_end = body.index("<!-- /hermes-factory:audit-contract -->") + len(
        "<!-- /hermes-factory:audit-contract -->"
    )
    contract = body[contract_start:contract_end]
    head, _, rest = body.partition("# Scope boundaries\n")
    _, _, tail = rest.partition("\n# Handoff\n")
    return f"{head}# Scope boundaries\n# Handoff\nNext target: review.\n{contract}\n{tail}"


def test_reconcile_rejects_worker_authored_target_before_mutation(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    superseded = _superseded_pair()
    target = next(
        detail for detail in superseded if detail["task"]["id"] == "old-impl"
    )
    for event in target["events"]:
        if event["kind"] == "created":
            event["payload"]["creator_task_id"] = "worker-7"
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *superseded],
    )

    with pytest.raises(HermesCommandError, match="created inside a worker session"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_worker_authored_superseded_card_before_mutation(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    superseded = _superseded_pair()
    review = next(
        detail for detail in superseded if detail["task"]["id"] == "old-review"
    )
    for event in review["events"]:
        if event["kind"] == "created":
            event["payload"]["creator_task_id"] = "worker-7"
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *superseded],
    )

    with pytest.raises(HermesCommandError, match="created inside a worker session"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_replacement_body_with_empty_scope_section(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    replacements = _replacement_pair()
    replacements[0]["task"]["body"] = _empty_scope_body("implementation")
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*replacements, *_superseded_pair()],
    )

    with pytest.raises(HermesCommandError, match="empty generated body sections"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_finalizes_other_recoveries_after_unexpected_error(
    valid_rig: Path, monkeypatch: pytest.MonkeyPatch
):
    broken_recovery, broken_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    accepted_recovery, accepted_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    accepted_recovery = {
        **accepted_recovery,
        "id": "recovery-2",
        "idempotency_key": "factory:demo:reconcile:old-impl:gave-up:201",
    }
    accepted_detail = {
        **accepted_detail,
        "task": {
            **accepted_detail["task"],
            "id": "recovery-2",
            "idempotency_key": "factory:demo:reconcile:old-impl:gave-up:201",
        },
    }
    real_plan = reconcile_module._recovery_plan

    def broken_plan(detail: dict[str, object], rig: object) -> object:
        if detail["task"]["id"] == "recovery-1":
            raise IndexError("simulated worker body indexing failure")
        return real_plan(detail, rig)

    monkeypatch.setattr(reconcile_module, "_recovery_plan", broken_plan)
    client = StatefulRecoveryClient(
        broken_recovery,
        broken_detail,
        [*_replacement_pair(creator_task_id="recovery-2"), *_superseded_pair()],
        extra_recoveries=[(accepted_recovery, accepted_detail)],
    )
    # recovery-2 answers the newer timeout incident recorded at 201.
    client.details["old-impl"]["events"].append(
        {
            "kind": "gave_up",
            "created_at": 201,
            "run_id": 12,
            "payload": {"trigger_outcome": "timed_out"},
        }
    )

    with pytest.raises(HermesCommandError, match="worker body indexing failure"):
        reconcile_board(client, load_rig(valid_rig))

    assert any(call[3] == "archive" for call in client.run_calls)
    assert "old-impl" not in client.details


def test_reconcile_rejects_truncated_replacement_policy_contract(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    prefix = "Factory replacement policy: "
    head, _, tail = recovery["body"].partition(prefix)
    json_line, newline, rest = tail.partition("\n")
    payload = json.loads(json_line)
    payload["source_audit_contract"] = {"task_class": "implementation"}
    recovery["body"] = (
        head
        + prefix
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + newline
        + rest
    )
    # Post-purge retry state: the target is gone and no superseded card survives, so
    # the policy block is the only source the contract validation can read.
    client = StatefulRecoveryClient(recovery, recovery_detail, _replacement_pair())

    with pytest.raises(HermesCommandError, match="malformed replacement policy"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_recovery_card_created_inside_worker_session(
    valid_rig: Path,
):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    for event in recovery_detail["events"]:
        if event["kind"] == "created":
            event["payload"]["creator_task_id"] = "worker-9"
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *_superseded_pair()],
    )

    with pytest.raises(HermesCommandError, match="worker session"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_live_target_without_timeout_evidence(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    superseded = _superseded_pair()
    target = next(
        detail for detail in superseded if detail["task"]["id"] == "old-impl"
    )
    target["events"] = [
        event for event in target["events"] if event["kind"] != "gave_up"
    ]
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *superseded],
    )

    with pytest.raises(HermesCommandError, match="terminal timeout evidence"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_surviving_superseded_when_target_is_gone(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    superseded = [
        detail for detail in _superseded_pair() if detail["task"]["id"] != "old-impl"
    ]
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *superseded],
    )

    with pytest.raises(HermesCommandError, match="missing while superseded tasks survive"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_non_ascii_recovery_timestamp(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    recovery["idempotency_key"] = (
        "factory:demo:reconcile:old-impl:gave-up:\u0662\u0660\u0660"
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *_superseded_pair()],
    )

    with pytest.raises(HermesCommandError, match="not Factory-generated"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_dispatches_released_work_when_one_plan_is_rejected(valid_rig: Path):
    client = FakeReconcileClient(
        [
            [_blocked_task()],
            _timed_out_show(),
            {
                "id": "recovery-1",
                "status": "ready",
                "title": "Decompose blocked task task-1",
            },
            [
                {
                    "id": "recovery-stale",
                    "status": "done",
                    "created_by": "hermes-factory-reconciler",
                    "idempotency_key": "not-a-factory-key",
                }
            ],
            {
                "task": {
                    "id": "recovery-stale",
                    "status": "done",
                    "created_by": "hermes-factory-reconciler",
                    "idempotency_key": "not-a-factory-key",
                },
                "parents": [],
                "children": [],
                "comments": [],
                "events": [],
                "runs": [],
            },
            {
                "task": {
                    "id": "recovery-1",
                    "title": "Decompose blocked task task-1",
                    "status": "ready",
                },
                "parents": [],
                "children": [],
                "events": [],
                "runs": [],
            },
            {"spawned": [{"task_id": "recovery-1", "pid": 123}]},
        ]
    )

    with pytest.raises(HermesCommandError, match="recovery-stale"):
        reconcile_board(client, load_rig(valid_rig), dispatch=True)

    assert client.json_calls[-1] == [
        "kanban",
        "--board",
        "demo-rig",
        "dispatch",
        "--max",
        "1",
        "--json",
    ]


def test_reconcile_continues_after_spec_error_in_one_recovery(
    valid_rig: Path, monkeypatch: pytest.MonkeyPatch
):
    broken_recovery, broken_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    accepted_recovery, accepted_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    accepted_recovery = {
        **accepted_recovery,
        "id": "recovery-2",
        "idempotency_key": "factory:demo:reconcile:old-impl:gave-up:201",
    }
    accepted_detail = {
        **accepted_detail,
        "task": {
            **accepted_detail["task"],
            "id": "recovery-2",
            "idempotency_key": "factory:demo:reconcile:old-impl:gave-up:201",
        },
    }
    real_plan = reconcile_module._recovery_plan

    def flaky_plan(detail: dict[str, object], rig: object) -> object:
        if detail["task"]["id"] == "recovery-1":
            raise SpecError("simulated rig-level rejection")
        return real_plan(detail, rig)

    monkeypatch.setattr(reconcile_module, "_recovery_plan", flaky_plan)
    client = StatefulRecoveryClient(
        broken_recovery,
        broken_detail,
        [*_replacement_pair(creator_task_id="recovery-2"), *_superseded_pair()],
        extra_recoveries=[(accepted_recovery, accepted_detail)],
    )
    # recovery-2 answers the newer timeout incident recorded at 201.
    client.details["old-impl"]["events"].append(
        {
            "kind": "gave_up",
            "created_at": 201,
            "run_id": 12,
            "payload": {"trigger_outcome": "timed_out"},
        }
    )

    with pytest.raises(HermesCommandError, match="simulated rig-level rejection"):
        reconcile_board(client, load_rig(valid_rig))

    assert any(call[3] == "archive" for call in client.run_calls)
    assert "old-impl" not in client.details


def _rejected_review_detail(
    review_id: str = "old-review",
    *,
    implementation_id: str = "old-impl",
    reason: str | None = None,
    run_id: int | None = 9,
    status: str = "blocked",
) -> dict[str, object]:
    detail = _factory_detail(
        review_id,
        "review",
        "original",
        "reviewer",
        parents=[implementation_id],
        children=["downstream"],
    )
    detail["task"]["status"] = status
    detail["events"] = [
        {"kind": "created", "created_at": 100, "payload": {"creator_task_id": None}},
        {"kind": "claimed", "created_at": 110, "run_id": run_id},
        {
            "kind": "blocked",
            "created_at": 300,
            "run_id": run_id,
            "payload": {
                "reason": reason
                if reason is not None
                else f"{REVIEW_REJECTION_PREFIX} proof command failed after independent rerun",
                "kind": None,
                "recurrences": 1,
                "source_status": "ready",
            },
        },
    ]
    return detail


def test_reconcile_creates_recovery_card_for_rejected_review(valid_rig: Path):
    client = FakeReconcileClient(
        [
            [
                {
                    "id": "old-review",
                    "title": "Review original",
                    "status": "blocked",
                    "assignee": "reviewer",
                    "created_by": "hermes-factory",
                }
            ],
            _rejected_review_detail(),
            {
                "id": "recovery-2",
                "status": "ready",
                "title": "Correct rejected review old-review",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 1
    assert result.recovery_task_ids == {"old-review": "recovery-2"}
    create = client.json_calls[2]
    assert create[:5] == [
        "kanban",
        "--board",
        "demo-rig",
        "create",
        "Correct rejected review old-review",
    ]
    assert _flag_value(create, "--assignee") == "builder"
    assert _flag_value(create, "--idempotency-key") == (
        "factory:demo:reconcile:old-review:review-rejected:300"
    )
    body = _flag_value(create, "--body")
    assert "Reason code: review_rejected" in body
    assert "Superseded task: old-review" in body
    assert "Incoming parents: old-impl" in body
    assert "proof command failed after independent rerun" in body
    assert "fresh independent review" in body
    assert "Do not run unlink, archive, or purge from this worker" in body
    assert "archive and purge superseded cards" in body


def test_reconcile_routes_blocked_review_without_rejection_marker_as_unknown(
    valid_rig: Path,
):
    client = FakeReconcileClient(
        [
            [
                {
                    "id": "old-review",
                    "title": "Review original",
                    "status": "blocked",
                    "assignee": "reviewer",
                    "created_by": "hermes-factory",
                }
            ],
            _rejected_review_detail(reason="need a product decision on the acceptance scope"),
            {
                "id": "unknown-recovery-2",
                "status": "ready",
                "title": "Resolve unknown blocked task old-review",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 1
    assert result.recovery_task_ids == {"old-review": "unknown-recovery-2"}
    create = client.json_calls[2]
    assert _flag_value(create, "--idempotency-key") == (
        "factory:demo:reconcile:old-review:unknown-stall:300"
    )
    assert "Reason code: unknown_operational_stall" in _flag_value(create, "--body")


def test_reconcile_routes_rejection_marker_without_worker_run_as_unknown(
    valid_rig: Path,
):
    client = FakeReconcileClient(
        [
            [
                {
                    "id": "old-review",
                    "title": "Review original",
                    "status": "blocked",
                    "assignee": "reviewer",
                    "created_by": "hermes-factory",
                }
            ],
            _rejected_review_detail(run_id=None),
            {
                "id": "unknown-recovery-3",
                "status": "ready",
                "title": "Resolve unknown blocked task old-review",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 1
    assert result.recovery_task_ids == {"old-review": "unknown-recovery-3"}


def test_reconcile_prefers_timeout_evidence_over_rejection_marker(valid_rig: Path):
    detail = _rejected_review_detail()
    detail["events"].append(
        {
            "kind": "gave_up",
            "created_at": 102,
            "run_id": None,
            "payload": {"trigger_outcome": "timed_out", "error": "elapsed 61s > limit 60s"},
        }
    )
    client = FakeReconcileClient(
        [
            [
                {
                    "id": "old-review",
                    "title": "Review original",
                    "status": "blocked",
                    "assignee": "reviewer",
                    "created_by": "hermes-factory",
                }
            ],
            detail,
            {"id": "recovery-2", "status": "ready", "title": "Decompose blocked task"},
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.recovery_task_ids == {"old-review": "recovery-2"}
    create = client.json_calls[2]
    assert _flag_value(create, "--idempotency-key") == (
        "factory:demo:reconcile:old-review:gave-up:102"
    )
    assert "Reason code: worker_timeout" in _flag_value(create, "--body")


def _rejected_review_recovery_details() -> list[dict[str, object]]:
    return [
        _factory_detail(
            "new-impl",
            "implementation",
            "original-recovery-300",
            "builder",
            parents=["old-impl"],
            children=["new-review"],
            created_by="builder",
            creator_task_id="recovery-1",
        ),
        _factory_detail(
            "new-review",
            "review",
            "original-recovery-300",
            "reviewer",
            parents=["new-impl"],
            children=["downstream"],
            created_by="builder",
            creator_task_id="recovery-1",
        ),
        _rejected_review_detail(),
        _factory_detail(
            "old-impl",
            "implementation",
            "original",
            "builder",
            parents=["parent"],
            children=["old-review"],
        ),
        {
            "task": {"id": "downstream", "status": "todo"},
            "parents": ["old-review", "new-review"],
            "children": [],
            "comments": [],
            "events": [],
            "runs": [],
        },
    ]


def _completed_rejected_review_recovery() -> tuple[dict[str, object], dict[str, object]]:
    return _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {"old_parent": "old-review", "new_parent": "new-review", "child": "downstream"}
        ],
        superseded=["old-review"],
        target="old-review",
        reason_code="review_rejected",
        # The rejecting worker recorded its block at 300 in _rejected_review_detail.
        event_time=300,
        policy_body=_replacement_policy_body(
            source_slice="original", incoming=("old-impl",), source_class="review"
        ),
    )


def test_reconcile_finalizes_rejected_review_recovery_plan(valid_rig: Path):
    recovery, detail = _completed_rejected_review_recovery()
    client = StatefulRecoveryClient(
        recovery, detail, _rejected_review_recovery_details()
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert [
        "kanban", "--board", "demo-rig", "unlink", "old-review", "downstream"
    ] in client.run_calls
    assert ["kanban", "--board", "demo-rig", "archive", "old-review"] in client.run_calls
    assert [
        "kanban", "--board", "demo-rig", "archive", "--rm", "old-review"
    ] in client.run_calls
    assert "old-review" not in client.details
    assert "new-review" in client.details["downstream"]["parents"]
    assert any(call[3] == "comment" and call[4] == "recovery-1" for call in client.run_calls)


def test_reconcile_rejects_review_recovery_without_rejection_evidence(valid_rig: Path):
    recovery, detail = _completed_rejected_review_recovery()
    details = _rejected_review_recovery_details()
    target = next(item for item in details if item["task"]["id"] == "old-review")
    target["events"] = [
        {"kind": "created", "created_at": 100, "payload": {"creator_task_id": None}},
        {
            "kind": "blocked",
            "created_at": 300,
            "run_id": 9,
            "payload": {
                "reason": "need a product decision",
                "kind": None,
                "recurrences": 1,
                "source_status": "ready",
            },
        },
    ]
    client = StatefulRecoveryClient(recovery, detail, details)

    with pytest.raises(HermesCommandError, match="no terminal review rejection"):
        reconcile_board(client, load_rig(valid_rig))

    assert [call for call in client.run_calls if call[3] in {"unlink", "archive"}] == []
    assert "old-review" in client.details


def _completed_unknown_recovery(
    *,
    replacements: list[str],
    rewires: list[dict[str, str]],
    superseded: list[str],
    action: str,
    decision: str = "Recover the unknown stall without operator input.",
    target: str = "old-impl",
    event_time: int = 200,
) -> tuple[dict[str, object], dict[str, object]]:
    recovery, detail = _completed_recovery(
        replacements=replacements,
        rewires=rewires,
        superseded=superseded,
        target=target,
        reason_code="unknown_operational_stall",
        event_time=event_time,
        policy_body=_replacement_policy_body(
            source_slice="original", incoming=("parent",), source_class="implementation"
        ),
    )
    metadata = detail["runs"][0]["metadata"]
    metadata.update(
        {
            "action": action,
            "target_task_id": target,
            "decision": decision,
            "assignee": None,
        }
    )
    return recovery, detail


def _unknown_replacement_recovery_details(
    replacements: list[str], slice_id: str
) -> list[dict[str, object]]:
    details: list[dict[str, object]] = []
    for index, task_id in enumerate(replacements):
        implementation = index % 2 == 0
        details.append(
            _factory_detail(
                task_id,
                "implementation" if implementation else "review",
                slice_id,
                "builder" if implementation else "reviewer",
                parents=["parent"] if index == 0 else [replacements[index - 1]],
                children=(
                    [replacements[index + 1]]
                    if index + 1 < len(replacements)
                    else ["downstream-1"]
                ),
                created_by="builder",
                creator_task_id="recovery-1",
            )
        )
    target = _factory_detail(
        "old-impl",
        "implementation",
        "original",
        "builder",
        parents=["parent"],
        children=["downstream-1"],
    )
    target["task"]["status"] = "blocked"
    target["events"] = [
        {"kind": "created", "created_at": 100, "payload": {"creator_task_id": None}},
        {
            "kind": "blocked",
            "created_at": 200,
            "run_id": 9,
            "payload": {
                "reason": "Need somebody to run a script",
                "kind": None,
                "recurrences": 1,
                "source_status": "ready",
            },
        },
    ]
    downstream = {
        "task": {"id": "downstream-1", "status": "todo"},
        "parents": ["old-impl", replacements[-1]],
        "children": [],
        "comments": [],
        "events": [],
        "runs": [],
    }
    return [*details, target, downstream]


def test_reconcile_finalizes_unknown_replace_recovery_plan(valid_rig: Path):
    recovery, detail = _completed_unknown_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-impl",
                "new_parent": "new-review",
                "child": "downstream-1",
            }
        ],
        superseded=["old-impl"],
        action="replace",
    )
    client = StatefulRecoveryClient(
        recovery,
        detail,
        _unknown_replacement_recovery_details(
            ["new-impl", "new-review"], "original-recovery-200"
        ),
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert [
        "kanban", "--board", "demo-rig", "unlink", "old-impl", "downstream-1"
    ] in client.run_calls
    assert ["kanban", "--board", "demo-rig", "archive", "old-impl"] in client.run_calls
    assert [
        "kanban", "--board", "demo-rig", "archive", "--rm", "old-impl"
    ] in client.run_calls
    assert "old-impl" not in client.details
    assert "new-review" in client.details["downstream-1"]["parents"]
    assert any(call[3] == "comment" and call[4] == "recovery-1" for call in client.run_calls)


def test_reconcile_finalizes_unknown_decompose_recovery_plan(valid_rig: Path):
    replacements = ["new-impl", "new-review", "new-impl-2", "new-review-2"]
    recovery, detail = _completed_unknown_recovery(
        replacements=replacements,
        rewires=[
            {
                "old_parent": "old-impl",
                "new_parent": "new-review-2",
                "child": "downstream-1",
            }
        ],
        superseded=["old-impl"],
        action="decompose",
    )
    client = StatefulRecoveryClient(
        recovery,
        detail,
        _unknown_replacement_recovery_details(replacements, "original-recovery-200"),
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert "old-impl" not in client.details
    assert "new-review-2" in client.details["downstream-1"]["parents"]
    assert "new-impl-2" in client.details["new-review-2"]["parents"]


def test_unknown_recovery_rejects_graph_mutation_for_non_replacement_action(
    valid_rig: Path,
):
    client = UnknownResumeClient()
    metadata = client.recovery["runs"][0]["metadata"]
    metadata["rewires"] = [
        {"old_parent": "unknown-1", "new_parent": "elsewhere", "child": "child-1"}
    ]

    with pytest.raises(HermesCommandError, match="may not mutate the graph"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_routes_breaker_parked_card_without_block_event_to_unknown(
    valid_rig: Path,
):
    crashed = _blocked_task("crashed-1")
    crashed["last_failure_error"] = "worker exited with code 9"
    client = FakeReconcileClient(
        [
            [crashed],
            {
                "task": crashed,
                "parents": [],
                "children": [],
                "comments": [],
                "events": [
                    {"kind": "created", "created_at": 100, "payload": {"creator_task_id": None}},
                    {
                        "kind": "gave_up",
                        "created_at": 300,
                        "run_id": 9,
                        "payload": {
                            "failures": 2,
                            "effective_limit": 2,
                            "trigger_outcome": "crashed",
                            "retry_status": "blocked",
                            "error": "worker exited with code 9",
                        },
                    },
                ],
                "runs": [],
            },
            {
                "id": "unknown-recovery-1",
                "status": "ready",
                "title": "Resolve unknown blocked task crashed-1",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 1
    assert result.recovery_task_ids == {"crashed-1": "unknown-recovery-1"}
    create = client.json_calls[2]
    assert _flag_value(create, "--idempotency-key") == (
        "factory:demo:reconcile:crashed-1:unknown-stall:300"
    )
    body = _flag_value(create, "--body")
    assert "Reason code: unknown_operational_stall" in body
    assert "worker exited with code 9" in body


def test_reconcile_reports_card_without_terminal_events_without_aborting_pass(
    valid_rig: Path,
):
    broken = _blocked_task("broken-1")
    unknown = _blocked_task("unknown-1")
    client = FakeReconcileClient(
        [
            [broken, unknown],
            {
                "task": broken,
                "parents": [],
                "children": [],
                "comments": [],
                "events": [
                    {"kind": "created", "created_at": 100, "payload": {"creator_task_id": None}}
                ],
                "runs": [],
            },
            {
                "task": unknown,
                "parents": [],
                "children": [],
                "comments": [],
                "events": [
                    {"kind": "created", "created_at": 100, "payload": {"creator_task_id": None}},
                    {
                        "kind": "blocked",
                        "created_at": 200,
                        "run_id": 9,
                        "payload": {"reason": "Need a coordinator decision"},
                    },
                ],
                "runs": [],
            },
            {
                "id": "unknown-recovery-2",
                "status": "ready",
                "title": "Resolve unknown blocked task unknown-1",
            },
            [],
        ]
    )

    with pytest.raises(HermesCommandError, match="broken-1.*no authoritative terminal event"):
        reconcile_board(client, load_rig(valid_rig))

    assert any(
        call[3] == "create" and "unknown-1" in call[4] for call in client.json_calls
    )
    assert client.json_calls[-1] == [
        "kanban", "--board", "demo-rig", "list", "--status", "done", "--json"
    ]


def _acceptance_gate_lookalike(creator_task_id: str | None) -> tuple[dict[str, object], dict]:
    body = (
        "# Project\nDemo\n\n# Mission\nDemo mission\n\n# Acceptance\n- approve\n\n"
        "# Proof required\n- none\n\n"
        "<!-- hermes-factory:audit-contract -->\n"
        'Factory audit contract: {"task_class":"acceptance","required_commands":[],'
        '"required_artifacts":[],"changed_files_required":false,"required_metadata":[]}\n'
        "<!-- /hermes-factory:audit-contract -->\n\n"
        "# Scope boundaries\nProject demo; mission demo-mission; workflow delivery.\n\n"
        "<!-- hermes-factory:human-gate -->\n# Human gate\nExplicit acceptance.\n\n"
        "# Handoff\nNext target: operator."
    )
    gate = {
        **_blocked_task("gate-1"),
        "title": "Accept demo mission",
        "body": body,
        "assignee": None,
        "idempotency_key": "factory:demo:demo-mission:acceptance",
        "last_failure_error": None,
    }
    detail = {
        "task": gate,
        "parents": ["review-1"],
        "children": [],
        "comments": [],
        "events": [
            {
                "kind": "created",
                "created_at": 90,
                "payload": {"creator_task_id": creator_task_id},
            },
            {
                "kind": "blocked",
                "created_at": 100,
                "run_id": None,
                "payload": {"reason": "human acceptance"},
            },
        ],
        "runs": [],
    }
    return gate, detail


def test_reconcile_rejects_worker_authored_human_gate_lookalike(valid_rig: Path):
    valid_rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "human_acceptance: false,",
            "human_acceptance: true, decision_authority: human,",
        ),
        encoding="utf-8",
    )
    gate, detail = _acceptance_gate_lookalike("t_worker99")
    client = FakeReconcileClient(
        [
            [gate],
            detail,
            {
                "id": "unknown-recovery-gate",
                "status": "ready",
                "title": "Resolve unknown blocked task gate-1",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 1
    assert result.recovery_task_ids == {"gate-1": "unknown-recovery-gate"}
    create = client.json_calls[2]
    assert "Reason code: unknown_operational_stall" in _flag_value(create, "--body")


def test_reconcile_disposes_stale_unknown_replace_without_mutation(valid_rig: Path):
    recovery, detail = _completed_unknown_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-impl",
                "new_parent": "new-review",
                "child": "downstream-1",
            }
        ],
        superseded=["old-impl"],
        action="replace",
    )
    details = _unknown_replacement_recovery_details(
        ["new-impl", "new-review"], "original-recovery-200"
    )
    target = next(item for item in details if item["task"]["id"] == "old-impl")
    target["task"]["status"] = "done"
    client = StatefulRecoveryClient(recovery, detail, details)

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ()
    assert [call for call in client.run_calls if call[3] == "unlink"] == []
    assert [call[3] for call in client.run_calls] == ["archive", "comment"]
    assert client.run_calls[0][-1] == "recovery-1"
    assert "recovery-rejected" in client.run_calls[1][5]
    assert "no longer blocked" in client.run_calls[1][5]

    reconcile_board(client, load_rig(valid_rig))

    assert [call[3] for call in client.run_calls] == ["archive", "comment"]


def test_unknown_replace_recovery_requires_verified_cards_manifest(valid_rig: Path):
    recovery, detail = _completed_unknown_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-impl",
                "new_parent": "new-review",
                "child": "downstream-1",
            }
        ],
        superseded=["old-impl"],
        action="replace",
    )
    detail["events"][1]["payload"].pop("verified_cards")
    client = StatefulRecoveryClient(
        recovery,
        detail,
        _unknown_replacement_recovery_details(
            ["new-impl", "new-review"], "original-recovery-200"
        ),
    )

    with pytest.raises(HermesCommandError, match="verified_cards"):
        reconcile_board(client, load_rig(valid_rig))

    assert [call for call in client.run_calls if call[3] in {"unlink", "archive"}] == []


def test_unknown_recovery_resume_lands_review_status_and_finalizes(valid_rig: Path):
    # Hermes's unblock restores the card's resumable phase: a review-phase card
    # lands in `review` rather than `ready` (kanban_db.unblock_task).
    client = UnknownResumeClient(landing_status="review")

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("unknown-recovery-1",)
    assert client.target["task"]["status"] == "review"
    assert any(
        call[3] == "comment"
        and call[4] == "unknown-recovery-1"
        and "recovery-finalized" in call[5]
        for call in client.run_calls
    )


def test_unknown_recovery_resume_accepts_a_card_claimed_after_unblock(
    valid_rig: Path,
):
    # The native dispatcher may claim the released card between the unblock and the
    # read-back; a claim is proof the unblock persisted, not a failed recovery.
    client = UnknownResumeClient(landing_status="claimed")

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("unknown-recovery-1",)


def test_reconcile_reports_blocked_recovery_card_without_creating_another(
    valid_rig: Path,
):
    unknown = _blocked_task("unknown-1")
    unknown["last_failure_error"] = None
    stuck = _blocked_task("recovery-1")
    stuck["created_by"] = "hermes-factory-reconciler"
    client = FakeReconcileClient(
        [
            [stuck, unknown],
            {
                "task": unknown,
                "parents": [],
                "children": [],
                "comments": [],
                "events": [
                    {
                        "kind": "blocked",
                        "created_at": 100,
                        "run_id": 9,
                        "payload": {"reason": "Need somebody to run a script"},
                    }
                ],
                "runs": [],
            },
            {
                "id": "unknown-recovery-1",
                "status": "ready",
                "title": "Resolve unknown blocked task unknown-1",
            },
            [],
        ]
    )

    with pytest.raises(HermesCommandError, match="recovery card recovery-1 is blocked"):
        reconcile_board(client, load_rig(valid_rig))

    creates = [call for call in client.json_calls if call[3] == "create"]
    assert len(creates) == 1
    assert "unknown-1" in creates[0][4]


def test_reconcile_contains_malformed_blocked_entry_without_aborting_pass(
    valid_rig: Path,
):
    unknown = _blocked_task("unknown-1")
    unknown["last_failure_error"] = None
    client = FakeReconcileClient(
        [
            [{"created_by": "hermes-factory", "title": "no id"}, unknown],
            {
                "task": unknown,
                "parents": [],
                "children": [],
                "comments": [],
                "events": [
                    {
                        "kind": "blocked",
                        "created_at": 100,
                        "run_id": 9,
                        "payload": {"reason": "Need somebody to run a script"},
                    }
                ],
                "runs": [],
            },
            {
                "id": "unknown-recovery-1",
                "status": "ready",
                "title": "Resolve unknown blocked task unknown-1",
            },
            [],
        ]
    )

    with pytest.raises(HermesCommandError, match="blocked entry #0"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.json_calls[2][3] == "create"


class _MalformedDoneClient(UnknownResumeClient):
    def run_json(self, args: list[str]) -> object:
        if args[3:] == ["list", "--status", "done", "--json"]:
            return [
                {"created_by": "hermes-factory-reconciler", "title": "no id"},
                self.recovery["task"],
            ]
        return super().run_json(args)


def test_reconcile_contains_malformed_done_entry_and_still_finalizes(
    valid_rig: Path,
):
    client = _MalformedDoneClient()

    with pytest.raises(HermesCommandError, match="done entry #0"):
        reconcile_board(client, load_rig(valid_rig))

    assert any(
        call[3] == "comment" and call[4] == "unknown-recovery-1"
        for call in client.run_calls
    )


def test_reconcile_classifies_newest_terminal_event_over_older_timeout(
    valid_rig: Path,
):
    unknown = _blocked_task("unknown-1")
    unknown["last_failure_error"] = None
    client = FakeReconcileClient(
        [
            [unknown],
            {
                "task": unknown,
                "parents": [],
                "children": [],
                "comments": [],
                "events": [
                    {
                        "kind": "created",
                        "created_at": 90,
                        "payload": {"creator_task_id": None},
                    },
                    {
                        "kind": "gave_up",
                        "created_at": 150,
                        "run_id": 9,
                        "payload": {"trigger_outcome": "timed_out", "error": "elapsed"},
                    },
                    {
                        "kind": "blocked",
                        "created_at": 300,
                        "run_id": 10,
                        "payload": {"reason": "Need a coordinator decision"},
                    },
                ],
                "runs": [],
            },
            {
                "id": "unknown-recovery-1",
                "status": "ready",
                "title": "Resolve unknown blocked task unknown-1",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.recovery_task_ids == {"unknown-1": "unknown-recovery-1"}
    create = client.json_calls[2]
    assert _flag_value(create, "--idempotency-key") == (
        "factory:demo:reconcile:unknown-1:unknown-stall:300"
    )
    assert "Reason code: unknown_operational_stall" in _flag_value(create, "--body")


def test_known_recovery_plan_for_an_older_incident_is_disposed_once(valid_rig: Path):
    # The recovery card answers the timeout recorded at key time 200; the target is now
    # blocked by a newer incident, so the plan can never apply to live state.
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl"],
    )
    target = _factory_detail(
        "old-impl",
        "implementation",
        "original",
        "builder",
        gave_up=True,
    )
    target["events"].append(
        {
            "kind": "blocked",
            "created_at": 300,
            "run_id": 11,
            "payload": {"reason": "Need a coordinator decision"},
        }
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            target,
        ],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ()
    assert [call[3] for call in client.run_calls] == ["archive", "comment"]
    assert client.run_calls[0][-1] == "recovery-1"
    assert "recovery-rejected" in client.run_calls[1][5]
    assert "newer incident" in client.run_calls[1][5]
    assert client.details["old-impl"]["task"]["status"] == "blocked"

    reconcile_board(client, load_rig(valid_rig))

    assert [call[3] for call in client.run_calls] == ["archive", "comment"]


def test_known_recovery_plan_without_terminal_evidence_is_reported(valid_rig: Path):
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl"],
    )
    target = _factory_detail("old-impl", "implementation", "original", "builder")
    target["task"]["status"] = "blocked"
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            target,
        ],
    )

    with pytest.raises(HermesCommandError, match="no terminal timeout evidence"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []
    assert client.recovery["status"] == "done"


class _ConcurrentPromotionClient(StatefulRecoveryClient):
    """Promotes the target inside the rewire window, as a live Hermes mutation can."""

    def run(self, args: list[str]) -> str:
        result = super().run(args)
        if args[3:4] == ["unlink"]:
            self.details["old-impl"]["task"]["status"] = "ready"
        return result


def test_stale_after_partial_mutation_is_reported_not_disposed(valid_rig: Path):
    recovery, detail = _completed_unknown_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-impl",
                "new_parent": "new-review",
                "child": "downstream-1",
            }
        ],
        superseded=["old-impl"],
        action="replace",
    )
    client = _ConcurrentPromotionClient(
        recovery,
        detail,
        _unknown_replacement_recovery_details(
            ["new-impl", "new-review"], "original-recovery-200"
        ),
    )

    with pytest.raises(HermesCommandError, match="stale after a partial mutation"):
        reconcile_board(client, load_rig(valid_rig))

    assert [
        "kanban",
        "--board",
        "demo-rig",
        "unlink",
        "old-impl",
        "downstream-1",
    ] in client.run_calls
    assert not any(call[3] == "comment" for call in client.run_calls)
    assert not any(
        call[3] == "archive" and call[-1] == "recovery-1" for call in client.run_calls
    )
    assert client.recovery["status"] == "done"


@pytest.mark.parametrize("resolved_status", ["done", "ready"])
def test_known_recovery_plan_is_stale_when_the_target_was_resolved(
    valid_rig: Path, resolved_status: str
):
    # Resolving a card writes no terminal event (`unblocked`, `promoted`), so the
    # trigger time still matches while the incident is over: only the live status can
    # see that the plan must not run.
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl"],
    )
    target = _factory_detail(
        "old-impl",
        "implementation",
        "original",
        "builder",
        gave_up=True,
    )
    target["task"]["status"] = resolved_status
    target["events"].append(
        {
            "kind": "unblocked",
            "created_at": 300,
            "payload": {"status": resolved_status, "resume_status": "blocked"},
        }
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            _factory_detail(
                "new-impl",
                "implementation",
                "replacement-a",
                "builder",
                parents=["parent"],
                children=["new-review"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            _factory_detail(
                "new-review",
                "review",
                "replacement-a",
                "reviewer",
                parents=["new-impl"],
                created_by="builder",
                creator_task_id="recovery-1",
            ),
            target,
        ],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ()
    assert [call[3] for call in client.run_calls] == ["archive", "comment"]
    assert client.run_calls[0][-1] == "recovery-1"
    assert "no longer blocked" in client.run_calls[1][5]
    assert client.details["old-impl"]["task"]["status"] == resolved_status
    assert "new-impl" in client.details


def test_stale_plan_with_an_already_applied_rewire_is_disposed_with_a_warning(
    valid_rig: Path,
):
    recovery, detail = _completed_unknown_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-impl",
                "new_parent": "new-review",
                "child": "downstream-1",
            }
        ],
        superseded=["old-impl"],
        action="replace",
    )
    client = StatefulRecoveryClient(
        recovery,
        detail,
        _unknown_replacement_recovery_details(
            ["new-impl", "new-review"], "original-recovery-200"
        ),
    )
    # An earlier interrupted pass already unlinked the edge and rewired the child.
    downstream = client.details["downstream-1"]
    downstream["parents"] = ["new-review"]
    # Hermes drops the edge on both sides when it unlinks.
    client.details["old-impl"]["children"] = []
    downstream["events"].append(
        {
            "kind": "unlinked",
            "created_at": 250,
            "payload": {"parent": "old-impl", "child": "downstream-1"},
        }
    )
    # The target is now blocked by a newer incident, so the plan is stale.
    client.details["old-impl"]["events"].append(
        {
            "kind": "blocked",
            "created_at": 900,
            "run_id": 13,
            "payload": {"reason": "newer incident"},
        }
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ()
    assert [call[3] for call in client.run_calls] == ["archive", "comment"]
    assert client.run_calls[0][-1] == "recovery-1"
    assert "newer incident" in client.run_calls[1][5]
    assert "already applied a rewire" in client.run_calls[1][5]
    assert client.recovery["status"] == "archived"
    # The applied rewire stays and the superseded cards are left to the newer incident.
    assert client.details["downstream-1"]["parents"] == ["new-review"]
    assert "old-impl" in client.details


@pytest.mark.parametrize("child_state", ["unreadable", "mismatched"])
def test_stale_plan_keeps_the_reason_honest_when_the_child_cannot_be_read(
    valid_rig: Path, child_state: str
):
    """An unreadable child proves neither graph state, so the reason claims neither."""
    recovery, detail = _completed_unknown_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-impl",
                "new_parent": "new-review",
                "child": "downstream-1",
            }
        ],
        superseded=["old-impl"],
        action="replace",
    )
    client = UnreadableChildClient(
        recovery,
        detail,
        _unknown_replacement_recovery_details(
            ["new-impl", "new-review"], "original-recovery-200"
        ),
        child_state,
    )
    client.details["old-impl"]["events"].append(
        {
            "kind": "blocked",
            "created_at": 900,
            "run_id": 13,
            "payload": {"reason": "newer incident"},
        }
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ()
    assert [call[3] for call in client.run_calls] == ["archive", "comment"]
    reason = str(client.run_calls[1][5])
    assert "cannot be excluded" in reason
    assert "already applied a rewire" not in reason


def test_recovery_finalizes_on_top_of_an_earlier_interrupted_application(
    valid_rig: Path,
):
    # Pass 1 of the chain unlinked the edge and died before purging; the target then
    # re-blocked, so the live plan answers the newer incident. The graph is already in
    # the state the plan wants, so it must apply instead of wedging on the old unlink.
    recovery, detail = _completed_unknown_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[
            {
                "old_parent": "old-impl",
                "new_parent": "new-review",
                "child": "downstream-1",
            }
        ],
        superseded=["old-impl"],
        action="replace",
        event_time=900,
    )
    client = StatefulRecoveryClient(
        recovery,
        detail,
        _unknown_replacement_recovery_details(
            ["new-impl", "new-review"], "original-recovery-200"
        ),
    )
    target = client.details["old-impl"]
    target["events"].append(
        {
            "kind": "blocked",
            "created_at": 900,
            "run_id": 13,
            "payload": {
                "reason": "Need somebody to run a script",
                "kind": None,
                "recurrences": 2,
                "source_status": "ready",
            },
        }
    )
    downstream = client.details["downstream-1"]
    downstream["parents"] = ["new-review"]
    # Hermes drops the edge on both sides when it unlinks.
    client.details["old-impl"]["children"] = []
    downstream["events"].append(
        {
            "kind": "unlinked",
            "created_at": 250,
            "payload": {"parent": "old-impl", "child": "downstream-1"},
        }
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert "old-impl" not in client.details
    assert client.details["downstream-1"]["parents"] == ["new-review"]
    assert any(
        "recovery-finalized" in call[5]
        for call in client.run_calls
        if call[3] == "comment"
    )


# ---------------------------------------------------------------------------
# Cards a coordinator worker created inside a Factory recovery run.
# ---------------------------------------------------------------------------


def _creator_recovery(
    *,
    card_id: str = "seed-recovery",
    target: str = "seed-target",
    created_cards: list[str],
    status: str = "archived",
) -> tuple[dict[str, object], dict[str, object]]:
    """A finished Factory recovery run plus Hermes's record of the cards it created."""
    recovery, detail = _completed_recovery(
        replacements=created_cards,
        rewires=[],
        superseded=[],
        target=target,
    )
    recovery["id"] = card_id
    recovery["status"] = status
    recovery["body"] = (
        "<!-- hermes-factory:recovery -->\n"
        f"# Recovery target\nReason code: worker_timeout\nSuperseded task: {target}"
    )
    return recovery, detail


def _interrupted_creator_recovery(
    *, card_id: str = "seed-recovery", target: str = "seed-target"
) -> tuple[dict[str, object], dict[str, object]]:
    """A Factory recovery run Hermes' failure breaker killed before it completed.

    Its runtime budget expired after it had already created cards, so Hermes records no
    completion event and therefore no ``verified_cards`` manifest for the run.
    """
    recovery, detail = _creator_recovery(
        card_id=card_id, target=target, created_cards=[], status="blocked"
    )
    detail["events"] = [
        {"kind": "created", "created_at": 100, "payload": {"creator_task_id": None}},
        {
            "kind": "gave_up",
            "created_at": 150,
            "run_id": None,
            "payload": {
                "trigger_outcome": "timed_out",
                "error": "elapsed 1209s > limit 1200s",
            },
        },
    ]
    detail["runs"] = [
        {
            "id": 200,
            "status": "timed_out",
            "outcome": "timed_out",
            "error": "elapsed 1209s > limit 1200s",
        }
    ]
    return recovery, detail


class _CompletedOnlyDoneClient(StatefulRecoveryClient):
    """``list --status done`` answers with done cards only, exactly as Hermes does."""

    def run_json(self, args: list[str]) -> object:
        if args[3:] == ["list", "--status", "done", "--json"]:
            return [
                detail["task"]
                for detail in self.recoveries.values()
                if detail["task"]["status"] == "done"
            ]
        return super().run_json(args)


def _recovery_created_pair(
    *,
    creator_task_id: str | None = "seed-recovery",
    names: tuple[str, str] = ("old-impl", "old-review"),
    slice_id: str = "original",
    gave_up: bool = False,
) -> list[dict[str, object]]:
    """An implementation/review pair exactly as a coordinator recovery run created it."""
    implementation_id, review_id = names
    return [
        _factory_detail(
            implementation_id,
            "implementation",
            slice_id,
            "builder",
            parents=["parent"],
            children=[review_id],
            created_by="builder",
            creator_task_id=creator_task_id,
            gave_up=gave_up,
        ),
        _factory_detail(
            review_id,
            "review",
            slice_id,
            "reviewer",
            parents=[implementation_id],
            created_by="builder",
            creator_task_id=creator_task_id,
        ),
    ]


def _blocked_recovery_created_card(
    *,
    task_id: str = "new-impl",
    task_class: str = "implementation",
    slice_id: str = "replacement-a",
    creator_task_id: str | None = "seed-recovery",
    terminal: str = "timeout",
) -> dict[str, object]:
    """A blocked card a recovery run created, parked the way ``terminal`` says."""
    detail = _factory_detail(
        task_id,
        task_class,
        slice_id,
        "builder" if task_class == "implementation" else "reviewer",
        created_by="builder",
        creator_task_id=creator_task_id,
    )
    detail["task"]["status"] = "blocked"
    if terminal == "timeout":
        detail["events"].append(
            {
                "kind": "gave_up",
                "created_at": 200,
                "run_id": None,
                "payload": {
                    "trigger_outcome": "timed_out",
                    "error": "elapsed 1209s > limit 1200s",
                },
            }
        )
    elif terminal == "rejected":
        detail["events"].append(
            {
                "kind": "blocked",
                "created_at": 200,
                "run_id": 9,
                "payload": {
                    "reason": f"{REVIEW_REJECTION_PREFIX}the proof command was not re-run"
                },
            }
        )
    else:
        detail["events"].append(
            {
                "kind": "blocked",
                "created_at": 200,
                "run_id": 9,
                "payload": {"reason": "Need somebody to run a script"},
            }
        )
    return detail


class RecoveryCreatedBoardClient:
    """A board that answers every read from its records instead of a queue."""

    def __init__(
        self, blocked: list[dict[str, object]], details: list[dict[str, object]]
    ) -> None:
        self.blocked = blocked
        self.details = {detail["task"]["id"]: detail for detail in details}
        self.json_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []

    def run_json(self, args: list[str]) -> object:
        self.json_calls.append(args)
        tail = args[3:]
        if tail == ["list", "--status", "blocked", "--json"]:
            return self.blocked
        if tail == ["list", "--status", "done", "--json"]:
            return []
        if tail == ["list", "--archived", "--json"]:
            return [detail["task"] for detail in self.details.values()]
        if tail[:1] == ["show"] and tail[1] in self.details:
            return self.details[tail[1]]
        if tail[:1] == ["create"]:
            return {"id": "recovery-2", "status": "ready", "title": tail[1]}
        raise AssertionError(args)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        return ""


@pytest.mark.parametrize(
    ("terminal", "task_class", "key_token", "title_prefix"),
    [
        ("timeout", "implementation", "gave-up", "Decompose blocked task"),
        ("rejected", "review", "review-rejected", "Correct rejected review"),
        ("unknown", "implementation", "unknown-stall", "Resolve unknown blocked task"),
    ],
)
def test_reconcile_classifies_blocked_recovery_created_card(
    valid_rig: Path, terminal: str, task_class: str, key_token: str, title_prefix: str
) -> None:
    task_id = "new-impl" if task_class == "implementation" else "new-review"
    detail = _blocked_recovery_created_card(
        task_id=task_id, task_class=task_class, terminal=terminal
    )
    _, creator_detail = _creator_recovery(created_cards=["new-impl", "new-review"])
    client = RecoveryCreatedBoardClient(
        [{"id": task_id, "created_by": "builder", "status": "blocked"}],
        [detail, creator_detail],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.scanned == 1
    assert result.eligible == 1
    assert result.recovery_task_ids == {task_id: "recovery-2"}
    create = next(call for call in client.json_calls if call[3] == "create")
    assert create[:5] == [
        "kanban",
        "--board",
        "demo-rig",
        "create",
        f"{title_prefix} {task_id}",
    ]
    assert _flag_value(create, "--idempotency-key") == (
        f"factory:demo:reconcile:{task_id}:{key_token}:200"
    )
    assert _flag_value(create, "--created-by") == "hermes-factory-reconciler"
    assert _flag_value(create, "--assignee") == "builder"
    assert _flag_value(create, "--priority") == "100"
    assert _flag_value(create, "--max-retries") == "2"
    assert f"Superseded task: {task_id}" in _flag_value(create, "--body")


def test_reconcile_creates_fixture_recovery_for_a_recovery_created_card(
    valid_rig: Path,
) -> None:
    valid_rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "completion_contract: local-only}",
            "completion_contract: local-only, fixture_recovery: true}",
        ),
        encoding="utf-8",
    )
    detail = _blocked_recovery_created_card()
    detail["children"] = ["new-review"]
    detail["parents"] = ["parent"]
    review = _factory_detail(
        "new-review",
        "review",
        "replacement-a",
        "reviewer",
        parents=["new-impl"],
        children=["downstream"],
        created_by="builder",
        creator_task_id="seed-recovery",
    )
    _, creator_detail = _creator_recovery(created_cards=["new-impl", "new-review"])
    client = RecoveryCreatedBoardClient(
        [{"id": "new-impl", "created_by": "builder", "status": "blocked"}],
        [detail, review, creator_detail],
    )

    reconcile_board(
        client, load_rig(valid_rig), fixture_recovery=True, recovery_runtime="60s"
    )

    create = next(call for call in client.json_calls if call[3] == "create")
    assert _flag_value(create, "--max-runtime") == "60s"
    body = _flag_value(create, "--body")
    prefix = "Factory fixture recovery package: "
    package_line = next(line for line in body.splitlines() if line.startswith(prefix))
    package = json.loads(package_line[len(prefix) :])
    assert package["implementation"]["parents"] == ["parent"]
    assert package["links"] == [{"parent": "$review_task_id", "child": "downstream"}]
    assert package["completion_metadata"]["superseded_task_ids"] == [
        "new-impl",
        "new-review",
    ]


def test_reconcile_ignores_blocked_card_created_by_a_non_recovery_run(
    valid_rig: Path,
) -> None:
    detail = _blocked_recovery_created_card(creator_task_id="worker-7")
    worker = _factory_detail(
        "worker-7",
        "implementation",
        "replacement-a",
        "builder",
        created_by="builder",
    )
    client = RecoveryCreatedBoardClient(
        [{"id": "new-impl", "created_by": "builder", "status": "blocked"}],
        [detail, worker],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.scanned == 1
    assert result.eligible == 0
    assert result.recovery_task_ids == {}
    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_classifies_blocked_card_created_by_an_interrupted_recovery_run(
    valid_rig: Path,
) -> None:
    detail = _blocked_recovery_created_card()
    _, creator_detail = _interrupted_creator_recovery()
    client = RecoveryCreatedBoardClient(
        [{"id": "new-impl", "created_by": "builder", "status": "blocked"}],
        [detail, creator_detail],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 1
    assert result.recovery_task_ids == {"new-impl": "recovery-2"}
    create = next(call for call in client.json_calls if call[3] == "create")
    assert _flag_value(create, "--idempotency-key") == (
        "factory:demo:reconcile:new-impl:gave-up:200"
    )
    assert _flag_value(create, "--created-by") == "hermes-factory-reconciler"


def test_reconcile_ignores_blocked_card_created_by_a_completed_run_without_a_manifest(
    valid_rig: Path,
) -> None:
    detail = _blocked_recovery_created_card()
    _, creator_detail = _creator_recovery(created_cards=["new-impl", "new-review"])
    # Hermes omits `verified_cards` when a completed run created no cards, so a
    # completed run without that key recorded an empty manifest. Only a run that never
    # completed at all may stand in for a missing manifest.
    for event in creator_detail["events"]:
        if event["kind"] == "completed":
            event["payload"] = {"result_len": 0}
    client = RecoveryCreatedBoardClient(
        [{"id": "new-impl", "created_by": "builder", "status": "blocked"}],
        [detail, creator_detail],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 0
    assert result.recovery_task_ids == {}
    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_ignores_blocked_card_without_a_creator_task_id(
    valid_rig: Path,
) -> None:
    detail = _blocked_recovery_created_card(creator_task_id=None)
    client = RecoveryCreatedBoardClient(
        [{"id": "new-impl", "created_by": "builder", "status": "blocked"}],
        [detail],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 0
    assert result.recovery_task_ids == {}
    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_ignores_blocked_card_missing_from_its_creator_run_manifest(
    valid_rig: Path,
) -> None:
    detail = _blocked_recovery_created_card()
    _, creator_detail = _creator_recovery(created_cards=["new-review"])
    client = RecoveryCreatedBoardClient(
        [{"id": "new-impl", "created_by": "builder", "status": "blocked"}],
        [detail, creator_detail],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 0
    assert result.recovery_task_ids == {}
    assert not any(call[3] == "create" for call in client.json_calls)


@pytest.mark.parametrize(
    "key",
    [
        "factory:other:demo-mission:replacement-a:implementation",
        "factory:demo:demo-mission:replacement-a:review",
    ],
)
def test_reconcile_ignores_blocked_card_without_a_matching_factory_identity(
    valid_rig: Path, key: str
) -> None:
    detail = _blocked_recovery_created_card()
    detail["task"]["idempotency_key"] = key
    _, creator_detail = _creator_recovery(created_cards=["new-impl", "new-review"])
    client = RecoveryCreatedBoardClient(
        [{"id": "new-impl", "created_by": "builder", "status": "blocked"}],
        [detail, creator_detail],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 0
    assert result.recovery_task_ids == {}
    assert not any(call[3] == "create" for call in client.json_calls)


def test_reconcile_supersedes_cards_created_by_a_recovery_run(valid_rig: Path) -> None:
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    _, creator_detail = _creator_recovery(created_cards=["old-impl", "old-review"])
    creator = creator_detail["task"]
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *_recovery_created_pair(gave_up=True)],
        extra_recoveries=[(creator, creator_detail)],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert [
        "kanban",
        "--board",
        "demo-rig",
        "archive",
        "old-impl",
        "old-review",
    ] in client.run_calls
    assert [
        "kanban",
        "--board",
        "demo-rig",
        "archive",
        "--rm",
        "old-review",
        "old-impl",
    ] in client.run_calls
    assert "old-impl" not in client.details
    assert "old-review" not in client.details


def test_reconcile_supersedes_cards_created_by_an_interrupted_recovery_run(
    valid_rig: Path,
) -> None:
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    _, creator_detail = _interrupted_creator_recovery()
    creator = creator_detail["task"]
    client = _CompletedOnlyDoneClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *_recovery_created_pair(gave_up=True)],
        extra_recoveries=[(creator, creator_detail)],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert [
        "kanban",
        "--board",
        "demo-rig",
        "archive",
        "--rm",
        "old-review",
        "old-impl",
    ] in client.run_calls
    assert "old-impl" not in client.details
    assert "old-review" not in client.details


def test_reconcile_rejects_superseded_card_without_a_creator_task_id(
    valid_rig: Path,
) -> None:
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(),
            *_recovery_created_pair(creator_task_id=None, gave_up=True),
        ],
    )

    with pytest.raises(HermesCommandError, match="invalid provenance"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_superseded_card_created_by_a_non_recovery_run(
    valid_rig: Path,
) -> None:
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    worker = _factory_detail(
        "worker-7",
        "implementation",
        "replacement-a",
        "builder",
        created_by="builder",
    )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(),
            *_recovery_created_pair(creator_task_id="worker-7", gave_up=True),
            worker,
        ],
    )

    with pytest.raises(HermesCommandError, match="created inside a worker session"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


def test_reconcile_rejects_superseded_card_missing_from_its_creator_run_manifest(
    valid_rig: Path,
) -> None:
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    _, creator_detail = _creator_recovery(created_cards=["old-impl"])
    creator = creator_detail["task"]
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *_recovery_created_pair(gave_up=True)],
        extra_recoveries=[(creator, creator_detail)],
    )

    with pytest.raises(HermesCommandError, match="created inside a worker session"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


class _RecoveryCreatedUnknownTargetClient(UnknownResumeClient):
    """An unknown-stall recovery whose blocked target a recovery run created."""

    def __init__(self, action: str = "retry") -> None:
        super().__init__(action)
        _, creator_detail = _creator_recovery(created_cards=["unknown-1"])
        self.creator = creator_detail
        target = _factory_detail(
            "unknown-1",
            "implementation",
            "original",
            "builder",
            parents=[],
            created_by="builder",
            creator_task_id="seed-recovery",
        )
        target["task"]["status"] = "blocked"
        target["events"] = [
            {"kind": "created", "payload": {"creator_task_id": "seed-recovery"}},
            {
                "kind": "blocked",
                "created_at": 100,
                "run_id": 9,
                "payload": {"reason": "Need a coordinator decision"},
            },
        ]
        self.target = target

    def run_json(self, args: list[str]) -> object:
        tail = args[3:]
        if tail == ["show", "seed-recovery", "--json"]:
            return self.creator
        if tail == ["list", "--archived", "--json"]:
            return [
                self.recovery["task"],
                self.target["task"],
                self.creator["task"],
            ]
        return super().run_json(args)


def test_unknown_recovery_retries_a_recovery_created_target(valid_rig: Path) -> None:
    client = _RecoveryCreatedUnknownTargetClient("retry")

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("unknown-recovery-1",)
    assert [
        "kanban",
        "--board",
        "demo-rig",
        "unblock",
        "unknown-1",
        "--reason",
        "Run the safe script and continue.",
    ] in client.run_calls
    assert client.target["task"]["status"] == "ready"


def test_reconcile_gives_recovery_cards_the_coordinator_runtime(valid_rig: Path):
    rig_path = valid_rig.parent / "rig-coordinator.yaml"
    rig_path.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "max_runtime: 30m", "max_runtime: 30m, coordinator_runtime: 45m"
        ),
        encoding="utf-8",
    )
    client = FakeReconcileClient(
        [
            [_blocked_task()],
            _timed_out_show(),
            {
                "id": "recovery-1",
                "status": "ready",
                "title": "Decompose blocked task task-1",
            },
            [],
        ]
    )

    result = reconcile_board(client, load_rig(rig_path))

    assert result.recovery_task_ids == {"task-1": "recovery-1"}
    create = client.json_calls[2]
    assert _flag_value(create, "--max-runtime") == "45m"
    assert _flag_value(create, "--max-retries") == "2"


def _creator_recovery_with_interrupted_run(
    *,
    created_cards: list[str],
    card_id: str = "seed-recovery",
    target: str = "seed-target",
) -> tuple[dict[str, object], dict[str, object]]:
    """A Factory recovery card with both a completed run and an interrupted one.

    The completed run vouched only for the cards it declared. An earlier run of the
    same card was killed by the runtime budget after creating cards of its own, so no
    manifest can exist for that run.
    """
    recovery, detail = _creator_recovery(
        card_id=card_id, target=target, created_cards=created_cards
    )
    detail["runs"].append(
        {
            "id": 201,
            "status": "timed_out",
            "outcome": "timed_out",
            "error": "elapsed 1209s > limit 1200s",
        }
    )
    return recovery, detail


def test_reconcile_classifies_blocked_card_left_by_an_interrupted_run_of_its_creator(
    valid_rig: Path,
) -> None:
    detail = _blocked_recovery_created_card()
    _, creator_detail = _creator_recovery_with_interrupted_run(
        created_cards=["new-review"]
    )
    client = RecoveryCreatedBoardClient(
        [{"id": "new-impl", "created_by": "builder", "status": "blocked"}],
        [detail, creator_detail],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.eligible == 1
    assert result.recovery_task_ids == {"new-impl": "recovery-2"}


def test_reconcile_supersedes_a_card_left_by_an_interrupted_run_of_its_creator(
    valid_rig: Path,
) -> None:
    recovery, recovery_detail = _completed_recovery(
        replacements=["new-impl", "new-review"],
        rewires=[],
        superseded=["old-impl", "old-review"],
    )
    _, creator_detail = _creator_recovery_with_interrupted_run(
        created_cards=["old-impl"]
    )
    creator = creator_detail["task"]
    client = _CompletedOnlyDoneClient(
        recovery,
        recovery_detail,
        [*_replacement_pair(), *_recovery_created_pair(gave_up=True)],
        extra_recoveries=[(creator, creator_detail)],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.finalized_recovery_task_ids == ("recovery-1",)
    assert "old-impl" not in client.details
    assert "old-review" not in client.details


class _KeyOmittedShowClient(RecoveryCreatedBoardClient):
    """A board whose ``show --json`` omits ``idempotency_key`` for every card.

    The installed Hermes CLI renders an explicit field tuple that leaves
    ``idempotency_key`` out, while the value sits correctly in the board database, so
    every read that feeds a provenance decision must hydrate it from there.
    """

    def __init__(
        self, blocked: list[dict[str, object]], details: list[dict[str, object]]
    ) -> None:
        super().__init__(blocked, details)
        self.task_record_calls: list[tuple[str, str]] = []

    def run_json(self, args: list[str]) -> object:
        payload = super().run_json(args)
        if args[3:4] == ["show"] and isinstance(payload, dict):
            task = payload.get("task")
            if isinstance(task, dict):
                stripped = dict(task)
                stripped.pop("idempotency_key", None)
                payload = {**payload, "task": stripped}
        return payload

    def task_record(self, board_slug: str, task_id: str) -> dict[str, object]:
        self.task_record_calls.append((board_slug, task_id))
        task = self.details[task_id]["task"]
        return {
            "id": task_id,
            "idempotency_key": task.get("idempotency_key"),
            "created_by": task.get("created_by"),
        }


def test_reconcile_classifies_blocked_recovery_created_card_without_creator_key_in_show(
    valid_rig: Path,
) -> None:
    detail = _blocked_recovery_created_card(terminal="rejected", task_class="review")
    detail["task"]["id"] = "new-review"
    _, creator_detail = _creator_recovery(created_cards=["new-impl", "new-review"])
    client = _KeyOmittedShowClient(
        [{"id": "new-review", "created_by": "builder", "status": "blocked"}],
        [detail, creator_detail],
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.scanned == 1
    assert result.eligible == 1
    assert result.recovery_task_ids == {"new-review": "recovery-2"}
    assert ("demo-rig", "seed-recovery") in client.task_record_calls
