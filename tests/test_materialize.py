import json
from pathlib import Path

import pytest

from hermes_factory.compiler import compile_decomposition_phase
from hermes_factory.materialize import materialize_completed_decompositions
from hermes_factory.mission_loader import load_mission
from hermes_factory.rig_loader import load_rig
from hermes_factory.task_templates import decomposition_source_contract
from hermes_factory.decomposition import canonical_source_contract_sha256


_MISSION = """\
version: 1
id: demo-mission
title: Demo mission
intent: Deliver a decomposed graph.
workflow: delivery
human_acceptance: false
acceptance:
  - Mission acceptance is preserved.
slices:
  - id: first
    title: First slice
    goal: Deliver the first outcome.
    depends_on: []
    acceptance:
      - First slice acceptance is preserved.
    proof:
      commands: [uv run pytest -q]
      artifacts: []
      changed_files_required: true
  - id: second
    title: Second slice
    goal: Deliver the second outcome.
    depends_on: [first]
    acceptance:
      - Second slice acceptance is preserved.
    proof:
      commands: [uv run pytest -q]
      artifacts: []
      changed_files_required: true
"""


def _rig_and_mission(valid_rig: Path, tmp_path: Path, *, mission_id: str = "demo-mission"):
    rig_path = tmp_path / "rig-v2.yaml"
    text = valid_rig.read_text(encoding="utf-8")
    text = text.replace("version: 1", "version: 2", 1)
    text = text.replace(
        "  reviewer: {profile: reviewer, role: independent_review, skills: []}",
        "  reviewer: {profile: reviewer, role: independent_review, skills: []}\n"
        "  decomposer: {profile: decomposer, role: decomposition, skills: []}",
    )
    text = text.replace(
        "delivery: {implementation_seat: builder, review_seat: reviewer, acceptance_seat: builder}",
        "delivery: {decomposition_seat: decomposer, implementation_seat: builder, "
        "review_seat: reviewer, acceptance_seat: builder}",
    )
    text = text.replace(
        "max_runtime: 30m, completion_contract",
        "max_runtime: 30m, decomposer_runtime: 60m, max_packets_per_slice: 8, "
        "max_packet_acceptance_items: 5, completion_contract",
    )
    rig_path.write_text(text, encoding="utf-8")
    mission_path = tmp_path / f"{mission_id}.yaml"
    mission_path.write_text(_MISSION.replace("id: demo-mission", f"id: {mission_id}"), encoding="utf-8")
    rig = load_rig(rig_path)
    return rig, load_mission(mission_path, rig)


def _manifest(rig, mission, slice_spec, packet_id: str) -> dict[str, object]:
    contract = decomposition_source_contract(rig, mission, slice_spec)
    acceptance = [*mission.acceptance, *slice_spec.acceptance]
    return {
        "version": 1,
        "project": rig.project.slug,
        "mission": mission.id,
        "source_slice": slice_spec.id,
        "source_contract_sha256": canonical_source_contract_sha256(contract),
        "packets": [
            {
                "id": packet_id,
                "title": f"Packet {packet_id}",
                "goal": f"Deliver {packet_id}.",
                "depends_on": [],
                "acceptance": acceptance,
                "source_acceptance_indexes": list(range(len(acceptance))),
                "likely_files": [f"src/{packet_id}.py"],
                "symbols": [f"{packet_id}.run"],
                "first_artifact": f"Add the failing {packet_id} test.",
                "proof": {
                    "commands": ["uv run pytest -q"],
                    "artifacts": [],
                    "changed_files_required": True,
                },
            }
        ],
        "limitations": [],
    }


class MaterializeClient:
    def __init__(
        self,
        tasks: list[dict[str, object]],
        details: dict[str, dict[str, object]],
        *,
        created_ids: list[str] | None = None,
        sort_graph_edges: bool = False,
    ):
        self.tasks = tasks
        self.details = details
        self.json_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []
        self.task_record_calls: list[tuple[str, str]] = []
        self.created_ids = list(created_ids or [])
        self.sort_graph_edges = sort_graph_edges
        self.ids_by_key: dict[str, str] = {
            task["idempotency_key"]: task["id"]
            for task in tasks
            if isinstance(task.get("idempotency_key"), str)
        }
        self.records = {
            task["id"]: {
                "id": task["id"],
                "title": task["title"],
                "body": task["body"],
                "assignee": task["assignee"],
                "status": task["status"],
                "created_by": task["created_by"],
                "workspace_kind": "dir",
                "workspace_path": str(task.get("workspace_path") or ""),
                "idempotency_key": task.pop("idempotency_key"),
                "max_runtime_seconds": task["max_runtime_seconds"],
                "skills": list(task.get("skills", [])),
                "max_retries": task.get("max_retries", 3),
                "completion_contract": task.get("completion_contract", "pr"),
            }
            for task in tasks
        }

    @staticmethod
    def _flag(args: list[str], name: str) -> str | None:
        return args[args.index(name) + 1] if name in args else None

    def task_record(self, board_slug: str, task_id: str) -> dict[str, object]:
        self.task_record_calls.append((board_slug, task_id))
        return self.records[task_id]

    def run_json(self, args: list[str]) -> object:
        self.json_calls.append(args)
        tail = args[3:]
        if tail == ["list", "--json"]:
            return self.tasks
        if tail[:1] == ["show"]:
            return self.details[tail[1]]
        if tail[:1] == ["create"]:
            key = self._flag(tail, "--idempotency-key")
            assert key is not None
            task_id = self.ids_by_key.get(key)
            if task_id is None:
                task_id = (
                    self.created_ids.pop(0)
                    if self.created_ids
                    else f"execution-{len(self.ids_by_key) + 1}"
                )
                self.ids_by_key[key] = task_id
                parents = [tail[index + 1] for index, value in enumerate(tail) if value == "--parent"]
                if self.sort_graph_edges:
                    parents.sort()
                runtime = self._flag(tail, "--max-runtime")
                runtime_seconds = {"30m": 1800, "60m": 3600}[runtime]
                task = {
                    "id": task_id,
                    "title": tail[1],
                    "body": self._flag(tail, "--body"),
                    "status": self._flag(tail, "--initial-status") or ("todo" if parents else "ready"),
                    "assignee": self._flag(tail, "--assignee"),
                    "created_by": self._flag(tail, "--created-by"),
                    "idempotency_key": key,
                    "max_runtime_seconds": runtime_seconds,
                }
                skills = [tail[index + 1] for index, value in enumerate(tail) if value == "--skill"]
                workspace = self._flag(tail, "--workspace") or ""
                self.records[task_id] = {
                    **task,
                    "workspace_kind": workspace.split(":", 1)[0],
                    "workspace_path": workspace.split(":", 1)[1] if ":" in workspace else None,
                    "skills": skills,
                    "max_retries": int(self._flag(tail, "--max-retries") or 0),
                    "completion_contract": self._flag(tail, "--completion-contract"),
                }
                self.tasks.append(task)
                self.details[task_id] = {
                    "task": task,
                    "parents": parents,
                    "children": [],
                    "comments": [],
                    "events": [
                        {
                            "kind": "created",
                            "payload": {"creator_task_id": None},
                            "run_id": None,
                        }
                    ],
                    "runs": [],
                }
                for parent in parents:
                    self.details[parent]["children"].append(task_id)
                    if self.sort_graph_edges:
                        self.details[parent]["children"].sort()
            task = self.details[task_id]["task"]
            return {"id": task_id, "status": task["status"], "title": task["title"]}
        raise AssertionError(args)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        tail = args[3:]
        if tail[:1] == ["comment"]:
            self.details[tail[1]]["comments"].append(
                {"body": tail[2], "author": self._flag(tail, "--author")}
            )
        elif tail[:1] == ["notify-subscribe"]:
            pass
        else:
            raise AssertionError(args)
        return ""


def _decomposition_board(rig, mission, statuses: tuple[str, str], *, prefix: str = "decomp"):
    plan = compile_decomposition_phase(rig, mission)
    tasks = []
    details = {}
    for index, (planned, slice_spec, status) in enumerate(
        zip(plan.tasks, mission.slices, statuses, strict=True), start=1
    ):
        task_id = f"{prefix}-{index}"
        task = {
            "id": task_id,
            "title": planned.title,
            "body": planned.body,
            "status": status,
            "assignee": planned.profile,
            "created_by": "hermes-factory",
            "idempotency_key": planned.idempotency_key,
            "max_runtime_seconds": 3600,
            "skills": list(planned.skills),
            "max_retries": rig.policies.max_retries,
            "completion_contract": rig.policies.completion_contract,
            "workspace_path": str(rig.project.cwd),
        }
        tasks.append(task)
        details[task_id] = {
            "task": task,
            "parents": [],
            "children": [],
            "comments": [],
            "events": [
                {
                    "kind": "created",
                    "payload": {"creator_task_id": None},
                    "run_id": None,
                },
                {
                    "kind": "completed",
                    "payload": {},
                    "run_id": index,
                },
            ],
            "runs": [
                {
                    "id": index,
                    "status": "done",
                    "outcome": "completed",
                    "metadata": _manifest(rig, mission, slice_spec, f"packet-{index}"),
                }
            ] if status == "done" else [],
        }
    return tasks, details


def test_materialization_waits_until_every_decomposition_task_is_done(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "running"))
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert result.materialized == {}
    assert result.rejected == {}
    assert not any(call[3] == "create" for call in client.json_calls)
    assert client.run_calls == []


def test_completed_decomposer_replacement_materializes_from_original_contract(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    tasks[0]["idempotency_key"] += ":retry:1"
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert result.rejected == {}
    assert "demo-mission" in result.materialized


def test_missing_decomposition_card_cannot_materialize_a_partial_mission(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    client = MaterializeClient(tasks[:1], {"decomp-1": details["decomp-1"]})

    result = materialize_completed_decompositions(client, rig)

    assert result.materialized == {}
    assert "demo-mission" in result.rejected
    assert "missing decomposition slices" in result.rejected["demo-mission"]
    assert not any(call[3] == "create" for call in client.json_calls)


def test_decomposition_contract_uses_authoritative_record_when_show_omits_fields(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    for detail in details.values():
        detail["task"] = {
            "id": detail["task"]["id"],
            "status": detail["task"]["status"],
        }
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert result.rejected == {}
    assert "demo-mission" in result.materialized


@pytest.mark.parametrize(
    "events",
    [
        [
            {"kind": "created", "payload": {"creator_task_id": "worker-1"}, "run_id": None},
            {"kind": "completed", "payload": {}, "run_id": 1},
        ],
        [
            {"kind": "created", "payload": {"creator_task_id": None}, "run_id": None},
            {"kind": "edited", "payload": {"fields": ["body"]}, "run_id": None},
            {"kind": "completed", "payload": {}, "run_id": 1},
        ],
        [
            {"kind": "created", "payload": {"creator_task_id": None}, "run_id": None},
            {"kind": "edited", "payload": {"fields": ["title"]}, "run_id": None},
            {"kind": "completed", "payload": {}, "run_id": 1},
        ],
    ],
)
def test_decomposition_card_requires_external_creation_and_immutable_contract_fields(
    valid_rig: Path, tmp_path: Path, events: list[dict[str, object]]
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    details["decomp-1"]["events"] = events
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    reason = result.rejected["demo-mission"]
    assert "created" in reason or "edited" in reason
    assert not any(call[3] == "create" for call in client.json_calls)


def test_decomposition_card_rejects_an_edit_event_that_can_hide_a_body_edit(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    details["decomp-1"]["events"].insert(
        1,
        {
            "kind": "edited",
            "payload": {"fields": ["result", "summary"]},
            "run_id": 1,
        },
    )
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert "edited Factory contract" in result.rejected["demo-mission"]
    assert not any(call[3] == "create" for call in client.json_calls)


def test_malformed_decomposition_events_are_rejected_per_mission(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    details["decomp-1"]["events"] = None
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert "malformed task detail" in result.rejected["demo-mission"]


def test_completed_valid_decomposition_is_applied_verified_and_marked_once(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert result.rejected == {}
    task_ids = result.materialized["demo-mission"]
    assert set(task_ids) == {
        "first:packet-1:implementation",
        "first:packet-1:review",
        "second:packet-2:implementation",
        "second:packet-2:review",
        "acceptance",
    }
    assert details[task_ids["second:packet-2:implementation"]]["parents"] == [
        task_ids["first:packet-1:review"]
    ]
    assert details[task_ids["acceptance"]]["parents"] == [
        task_ids["second:packet-2:review"]
    ]
    for key, task_id in task_ids.items():
        task = details[task_id]["task"]
        assert task["body"]
        assert task["max_runtime_seconds"] == (1800 if key != "acceptance" else 1800)
        assert task["assignee"] == ("reviewer" if key.endswith(":review") else "builder")
    marker_comments = [
        comment
        for detail in details.values()
        for comment in detail["comments"]
        if "hermes-factory:materialized:" in comment["body"]
    ]
    assert len(marker_comments) == 1
    assert marker_comments[0]["author"] == "hermes-factory"
    creates = [call for call in client.json_calls if call[3] == "create"]
    assert len(creates) == 5
    execution_shows = [
        call
        for call in client.json_calls
        if call[3] == "show" and call[4].startswith("execution-")
    ]
    assert len(execution_shows) == 5


def test_repeated_materialization_reuses_the_complete_verified_execution_graph(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    client = MaterializeClient(tasks, details)

    first = materialize_completed_decompositions(client, rig)
    create_count = len([call for call in client.json_calls if call[3] == "create"])
    second = materialize_completed_decompositions(client, rig)

    assert second.rejected == {}
    assert second.materialized == first.materialized
    assert len([call for call in client.json_calls if call[3] == "create"]) == create_count


def test_execution_graph_verification_ignores_hermes_edge_sort_order(
    valid_rig: Path, tmp_path: Path
):
    rig, _ = _rig_and_mission(valid_rig, tmp_path)
    independent_path = tmp_path / "independent.yaml"
    independent_path.write_text(_MISSION.replace("depends_on: [first]", "depends_on: []"), encoding="utf-8")
    mission = load_mission(independent_path, rig)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    client = MaterializeClient(
        tasks,
        details,
        created_ids=["z-impl", "z-review", "a-impl", "a-review", "accept"],
        sort_graph_edges=True,
    )

    result = materialize_completed_decompositions(client, rig)

    assert result.rejected == {}
    assert "demo-mission" in result.materialized


def test_malformed_manifest_rejects_the_whole_mission_before_any_execution_card(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    details["decomp-2"]["runs"][0]["metadata"]["source_contract_sha256"] = "0" * 64
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert result.materialized == {}
    assert "demo-mission" in result.rejected
    assert "source_contract_sha256" in result.rejected["demo-mission"]
    assert not any(call[3] == "create" for call in client.json_calls)
    assert not any("hermes-factory:materialized:" in " ".join(call) for call in client.run_calls)


def test_active_execution_key_collision_is_rejected_before_any_create(
    valid_rig: Path, tmp_path: Path
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    rogue = {
        "id": "rogue-active",
        "title": "Lookalike",
        "body": "not Factory work",
        "status": "ready",
        "assignee": "builder",
        "created_by": "hermes-factory",
        "idempotency_key": "factory:demo:demo-mission:first:packet:packet-1:implementation",
        "max_runtime_seconds": 1800,
        "skills": [],
        "max_retries": rig.policies.max_retries,
        "completion_contract": rig.policies.completion_contract,
        "workspace_path": str(rig.project.cwd),
    }
    tasks.append(rogue)
    details["rogue-active"] = {
        "task": rogue,
        "parents": [],
        "children": [],
        "comments": [],
        "events": [{"kind": "created", "payload": {"creator_task_id": None}, "run_id": None}],
        "runs": [],
    }
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert "partial task set" in result.rejected["demo-mission"]
    assert not any(call[3] == "create" for call in client.json_calls)


@pytest.mark.parametrize("defect", ["authoritative_body", "worker_created"])
def test_created_execution_cards_require_exact_authoritative_contract_and_external_origin(
    valid_rig: Path, tmp_path: Path, defect: str
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))

    class DriftingClient(MaterializeClient):
        def run_json(self, args: list[str]) -> object:
            result = super().run_json(args)
            if args[3:4] == ["create"] and isinstance(result, dict):
                task_id = result["id"]
                if defect == "authoritative_body":
                    self.records[task_id]["body"] = "tampered authoritative body"
                else:
                    self.details[task_id]["events"][0]["payload"]["creator_task_id"] = "worker-1"
            return result

    client = DriftingClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    reason = result.rejected["demo-mission"]
    assert "contract" in reason or "created" in reason


def test_latest_successful_run_is_authoritative(valid_rig: Path, tmp_path: Path):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    stale = json.loads(json.dumps(details["decomp-1"]["runs"][0]))
    stale["id"] = 99
    stale["status"] = "failed"
    stale["outcome"] = "failed"
    stale["metadata"]["source_contract_sha256"] = "0" * 64
    details["decomp-1"]["runs"].append(stale)
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert "demo-mission" in result.materialized
    assert result.rejected == {}


@pytest.mark.parametrize("defect", ["non_integer_run", "missing_completion", "duplicate_completion"])
def test_manifest_requires_one_completion_event_for_integer_successful_run(
    valid_rig: Path, tmp_path: Path, defect: str
):
    rig, mission = _rig_and_mission(valid_rig, tmp_path)
    tasks, details = _decomposition_board(rig, mission, ("done", "done"))
    if defect == "non_integer_run":
        details["decomp-1"]["runs"][0]["id"] = "1"
    elif defect == "missing_completion":
        details["decomp-1"]["events"] = details["decomp-1"]["events"][:1]
    else:
        details["decomp-1"]["events"].append(
            {"kind": "completed", "payload": {}, "run_id": 1}
        )
    client = MaterializeClient(tasks, details)

    result = materialize_completed_decompositions(client, rig)

    assert "run" in result.rejected["demo-mission"] or "completion" in result.rejected["demo-mission"]
    assert not any(call[3] == "create" for call in client.json_calls)


def test_rejected_mission_does_not_stop_independent_materialization(
    valid_rig: Path, tmp_path: Path
):
    rig, bad_mission = _rig_and_mission(valid_rig, tmp_path)
    _, good_mission = _rig_and_mission(valid_rig, tmp_path, mission_id="other-mission")
    bad_tasks, bad_details = _decomposition_board(
        rig, bad_mission, ("done", "done"), prefix="bad"
    )
    good_tasks, good_details = _decomposition_board(
        rig, good_mission, ("done", "done"), prefix="good"
    )
    bad_details["bad-1"]["runs"][0]["metadata"]["packets"] = "malformed"
    client = MaterializeClient(
        bad_tasks + good_tasks,
        {**bad_details, **good_details},
    )

    result = materialize_completed_decompositions(client, rig)

    assert "demo-mission" in result.rejected
    assert "other-mission" in result.materialized
