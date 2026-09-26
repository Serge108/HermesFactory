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


def _replacement_policy_body() -> str:
    policy = {
        "project": "demo",
        "mission": "demo-mission",
        "source_slice": "original",
        "workflow": "delivery",
        "incoming_parent_ids": ["parent"],
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
            "task_class": "implementation",
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
            "status": "todo",
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
) -> tuple[dict[str, object], dict[str, object]]:
    recovery = {
        "id": "recovery-1",
        "title": "Decompose blocked task old-impl",
        "body": "Superseded task: old-impl\n\n" + _replacement_policy_body(),
        "status": "done",
        "created_by": "hermes-factory-reconciler",
        "idempotency_key": "factory:demo:reconcile:old-impl:gave-up:200",
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
            return [detail["task"] for detail in self.recoveries.values()]
        if tail[:1] == ["show"] and tail[1] in self.recoveries:
            return self.recoveries[tail[1]]
        if tail[:1] == ["show"]:
            return self.details[tail[1]]
        if tail == ["list", "--archived", "--json"]:
            return [detail["task"] for detail in self.details.values()]
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
            [],
        ]
    )

    result = reconcile_board(client, load_rig(valid_rig))

    assert result.scanned == 2
    assert result.eligible == 0
    assert result.recovery_task_ids == {}
    assert len(client.json_calls) == 3
    assert client.json_calls[-1] == [
        "kanban", "--board", "demo-rig", "list", "--status", "done", "--json"
    ]


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


@pytest.mark.parametrize("proof", ["none", "stale"])
def test_reconcile_rejects_unproven_missing_external_edge(
    valid_rig: Path, proof: str
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
    events: list[dict[str, object]] = []
    if proof == "stale":
        events.append(
            {
                "kind": "unlinked",
                "created_at": 199,
                "payload": {"parent": "old-review", "child": "downstream"},
            }
        )
    client = StatefulRecoveryClient(
        recovery,
        recovery_detail,
        [
            *_replacement_pair(review_children=["downstream"]),
            *_superseded_pair(),
            _downstream_detail(["new-review"], events),
        ],
    )

    with pytest.raises(HermesCommandError, match="old-review -> downstream"):
        reconcile_board(client, load_rig(valid_rig))

    assert client.run_calls == []


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

    with pytest.raises(HermesCommandError, match="simulated rig-level rejection"):
        reconcile_board(client, load_rig(valid_rig))

    assert any(call[3] == "archive" for call in client.run_calls)
    assert "old-impl" not in client.details
