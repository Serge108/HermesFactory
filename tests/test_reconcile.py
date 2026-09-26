from pathlib import Path

from hermes_factory.reconcile import reconcile_board
from hermes_factory.rig_loader import load_rig


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


def _blocked_task(task_id: str = "task-1") -> dict[str, object]:
    return {
        "id": task_id,
        "title": "Implement large behavior",
        "body": "Original generated task body",
        "assignee": "builder",
        "status": "blocked",
        "created_by": "hermes-factory",
        "workspace_kind": "dir",
        "workspace_path": "F:\\Project With Space",
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
    assert "remove the superseded implementation and review cards" in body


def test_reconcile_ignores_manual_blocks_and_reconciler_tasks(valid_rig: Path):
    manual = _blocked_task("manual-1")
    manual["last_failure_error"] = None
    recovery = _blocked_task("recovery-1")
    recovery["created_by"] = "hermes-factory-reconciler"
    client = FakeReconcileClient(
        [
            [manual, recovery],
            {
                "task": manual,
                "parents": [],
                "children": [],
                "comments": [],
                "events": [{"kind": "blocked", "created_at": 100, "run_id": None}],
                "runs": [],
            },
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.scanned == 2
    assert result.eligible == 0
    assert result.recovery_task_ids == {}
    assert len(client.json_calls) == 2


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
