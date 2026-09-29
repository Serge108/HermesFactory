from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from hermes_factory.board import _task_runtime, apply_mission
from hermes_factory.compiler import compile_decomposition_phase, compile_materialized_execution_phase
from hermes_factory.decomposition import DecompositionManifest, canonical_source_contract_sha256, parse_decomposition_manifest
from hermes_factory.errors import HermesCommandError, SpecError
from hermes_factory.health import run_succeeded
from hermes_factory.hermes_cli import HermesClient
from hermes_factory.models import MissionSpec, ProofSpec, RigSpec, SliceSpec
from hermes_factory.task_templates import decomposition_source_contract, parse_audit_contract

_MATERIALIZED_MARKER_PREFIX = "<!-- hermes-factory:materialized:"
_SOURCE_CONTRACT_HEADING = "# Immutable source contract"
_DURATION = re.compile(r"^(\d+)([smhd])$")


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    materialized: dict[str, dict[str, str]]
    rejected: dict[str, str]


def _task_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise HermesCommandError("Hermes returned malformed task list JSON")
    return value


def _task_show(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HermesCommandError("Hermes returned malformed task detail JSON")
    task = value.get("task")
    parents = value.get("parents")
    children = value.get("children")
    comments = value.get("comments")
    events = value.get("events")
    runs = value.get("runs")
    if (
        not isinstance(task, dict)
        or not isinstance(parents, list)
        or not isinstance(children, list)
        or not isinstance(comments, list)
        or not isinstance(events, list)
        or not isinstance(runs, list)
        or any(not isinstance(item, str) for item in parents + children)
        or any(not isinstance(item, dict) for item in comments + events + runs)
    ):
        raise HermesCommandError("Hermes returned malformed task detail JSON")
    return value


def _decomposition_identity(task: dict[str, Any], project: str) -> tuple[str, str] | None:
    key = task.get("idempotency_key")
    if task.get("created_by") != "hermes-factory" or not isinstance(key, str):
        return None
    prefix = f"factory:{project}:"
    if not key.startswith(prefix):
        return None
    parts = key[len(prefix) :].split(":")
    if (
        len(parts) not in {3, 5}
        or not all(parts)
        or parts[2] != "decomposition"
        or (len(parts) == 5 and parts[3:] != ["retry", "1"])
    ):
        return None
    return parts[0], parts[1]


def _authoritative_decomposition_task(
    client: HermesClient,
    rig: RigSpec,
    listed: dict[str, Any],
    record: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    task_id = listed.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise HermesCommandError("Hermes returned malformed task list JSON")
    if record is None:
        record = client.task_record(rig.board.slug, task_id)
    body = record.get("body")
    if (
        record.get("created_by") != "hermes-factory"
        or not isinstance(body, str)
        or parse_audit_contract(body).get("task_class") != "decomposition"
    ):
        return None
    if record.get("id") != task_id:
        raise HermesCommandError("Hermes decomposition task disagrees with authoritative record")
    hydrated = dict(record)
    if _decomposition_identity(hydrated, rig.project.slug) is None:
        raise HermesCommandError("Hermes decomposition task has malformed Factory key")
    return hydrated


def _source_contract(body: object) -> dict[str, Any]:
    if not isinstance(body, str):
        raise HermesCommandError("decomposition task has no authoritative body")
    heading = body.find(_SOURCE_CONTRACT_HEADING)
    fence = body.find("```json", heading)
    if heading < 0 or fence < 0:
        raise HermesCommandError("decomposition task has no immutable source contract")
    start = fence + len("```json")
    end = body.find("```", start)
    if end < 0:
        raise HermesCommandError("decomposition task has malformed immutable source contract")
    try:
        value = json.loads(body[start:end])
    except json.JSONDecodeError as exc:
        raise HermesCommandError("decomposition task has malformed immutable source contract") from exc
    if not isinstance(value, dict):
        raise HermesCommandError("decomposition source contract must be a mapping")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise HermesCommandError(f"decomposition source contract has malformed {field}")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise HermesCommandError(f"decomposition source contract has malformed {field}")
    return tuple(value)


def _mission_from_contracts(rig: RigSpec, mission_id: str, contracts: list[dict[str, Any]]) -> MissionSpec:
    if not contracts:
        raise HermesCommandError("decomposition mission has no source contracts")
    first_mission = contracts[0].get("mission")
    if not isinstance(first_mission, dict):
        raise HermesCommandError("decomposition source contract has malformed mission")
    signature = json.dumps(first_mission, sort_keys=True, separators=(",", ":"))
    slices: list[SliceSpec] = []
    seen: set[str] = set()
    for contract in contracts:
        project = contract.get("project")
        mission = contract.get("mission")
        source_slice = contract.get("source_slice")
        if (
            not isinstance(project, dict)
            or project.get("slug") != rig.project.slug
            or project.get("name") != rig.project.name
            or project.get("cwd") != str(rig.project.cwd)
            or not isinstance(mission, dict)
            or json.dumps(mission, sort_keys=True, separators=(",", ":")) != signature
            or not isinstance(source_slice, dict)
        ):
            raise HermesCommandError("decomposition source contracts disagree")
        slice_id = _string(source_slice.get("id"), "source_slice.id")
        if slice_id in seen:
            raise HermesCommandError(f"duplicate decomposition slice {slice_id!r}")
        seen.add(slice_id)
        proof_data = source_slice.get("proof")
        proof = None
        if proof_data is not None:
            if not isinstance(proof_data, dict) or type(proof_data.get("changed_files_required")) is not bool:
                raise HermesCommandError("decomposition source contract has malformed proof")
            proof = ProofSpec(
                commands=_strings(proof_data.get("commands"), "proof.commands"),
                artifacts=_strings(proof_data.get("artifacts"), "proof.artifacts"),
                changed_files_required=proof_data["changed_files_required"],
            )
        slices.append(SliceSpec(
            id=slice_id,
            title=_string(source_slice.get("title"), "source_slice.title"),
            goal=_string(source_slice.get("goal"), "source_slice.goal"),
            depends_on=_strings(source_slice.get("depends_on"), "source_slice.depends_on"),
            acceptance=_strings(source_slice.get("acceptance"), "source_slice.acceptance"),
            proof=proof,
        ))
    expected_slice_ids = _strings(first_mission.get("slice_ids"), "mission.slice_ids")
    missing = sorted(set(expected_slice_ids) - seen)
    unknown = sorted(seen - set(expected_slice_ids))
    if missing:
        raise HermesCommandError("missing decomposition slices: " + ", ".join(missing))
    if unknown or len(expected_slice_ids) != len(set(expected_slice_ids)):
        raise HermesCommandError("decomposition source contract has inconsistent slice_ids")
    slices_by_id = {item.id: item for item in slices}
    slices = [slices_by_id[slice_id] for slice_id in expected_slice_ids]
    loaded_id = _string(first_mission.get("id"), "mission.id")
    if loaded_id != mission_id:
        raise HermesCommandError("decomposition mission identity disagrees with Factory key")
    workflow = _string(first_mission.get("workflow"), "mission.workflow")
    if workflow not in rig.workflows:
        raise HermesCommandError(f"decomposition mission uses unknown workflow {workflow!r}")
    version = first_mission.get("version")
    human_acceptance = first_mission.get("human_acceptance")
    if type(version) is not int or type(human_acceptance) is not bool:
        raise HermesCommandError("decomposition source contract has malformed mission policy")
    return MissionSpec(
        version=version,
        id=loaded_id,
        title=_string(first_mission.get("title"), "mission.title"),
        intent=_string(first_mission.get("intent"), "mission.intent"),
        workflow=workflow,
        human_acceptance=human_acceptance,
        acceptance=_strings(first_mission.get("acceptance"), "mission.acceptance"),
        slices=tuple(slices),
    )


def _duration_seconds(value: str) -> int:
    if value.isdigit():
        return int(value)
    match = _DURATION.fullmatch(value)
    if match is None:
        raise HermesCommandError(f"unsupported runtime {value!r}")
    return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]


def _latest_manifest(
    detail: dict[str, Any], *, rig: RigSpec, mission: MissionSpec,
    source_slice: SliceSpec, source_contract: dict[str, Any]
) -> DecompositionManifest:
    successful = next((run for run in reversed(detail["runs"]) if run_succeeded(run)), None)
    if successful is None or type(successful.get("id")) is not int:
        raise HermesCommandError("completed decomposition task has no authoritative successful run")
    run_id = successful["id"]
    completed = [
        event
        for event in detail["events"]
        if event.get("kind") == "completed" and event.get("run_id") == run_id
    ]
    if len(completed) != 1:
        raise HermesCommandError(
            "completed decomposition task has no single authoritative completion event"
        )
    metadata = successful.get("metadata")
    if isinstance(metadata, dict):
        # Hermes stamps its own run envelope onto the metadata of the completion a worker
        # records: `kanban_complete` merges `worker_session_id`, and `_merge_artifacts` merges
        # the attachments the worker declared. A real manifest always arrives inside that
        # envelope, which belongs to the runtime and never to the decomposer, so it is removed
        # here. Every other key stays validated exactly: an unknown key is decomposer drift and
        # still rejects the whole mission, and a malformed envelope is refused rather than
        # silently dropped, even though an authored key of the same name cannot be told apart
        # from the runtime's - the runtime wins, and its value is never interpreted.
        manifest = dict(metadata)
        worker_session_id = manifest.pop("worker_session_id", None)
        if worker_session_id is not None and (
            not isinstance(worker_session_id, str) or not worker_session_id
        ):
            raise HermesCommandError("completed decomposition task has malformed run envelope")
        artifacts = manifest.pop("artifacts", None)
        if artifacts is not None and (
            not isinstance(artifacts, list)
            or any(not isinstance(name, str) or not name for name in artifacts)
        ):
            raise HermesCommandError("completed decomposition task has malformed run envelope")
    else:
        manifest = metadata
    return parse_decomposition_manifest(
        manifest,
        expected_project=rig.project.slug,
        expected_mission=mission.id,
        expected_source_slice=source_slice.id,
        expected_source_contract_sha256=canonical_source_contract_sha256(source_contract),
        source_contract=source_contract,
        max_packets=rig.policies.max_packets_per_slice or 0,
        max_packet_acceptance_items=rig.policies.max_packet_acceptance_items or 0,
    )


def _verify_decomposition_cards(
    rig: RigSpec, mission: MissionSpec, tasks_by_slice: dict[str, dict[str, Any]],
    details_by_slice: dict[str, dict[str, Any]]
) -> None:
    expected = {task.key.split(":", 1)[0]: task for task in compile_decomposition_phase(rig, mission).tasks}
    if set(expected) != set(tasks_by_slice):
        raise HermesCommandError("decomposition cards do not match reconstructed mission slices")
    expected_runtime = _duration_seconds(rig.policies.decomposer_runtime or "")
    for slice_id, planned in expected.items():
        listed = tasks_by_slice[slice_id]
        detail = details_by_slice[slice_id]
        task = detail["task"]
        if (
            task.get("id") != listed.get("id")
            or task.get("status") != listed.get("status")
            or listed.get("status") != "done"
        ):
            raise HermesCommandError("decomposition task detail disagrees with authoritative record")
        created = [event for event in detail["events"] if event.get("kind") == "created"]
        created_payload = created[0].get("payload") if len(created) == 1 else None
        if (
            len(created) != 1
            or not isinstance(created_payload, dict)
            or created_payload.get("creator_task_id") is not None
        ):
            raise HermesCommandError(
                f"decomposition task {task.get('id')} must have exactly one externally created event"
            )
        for event in detail["events"]:
            if event.get("kind") == "edited":
                raise HermesCommandError(
                    f"decomposition task {task.get('id')} has edited Factory contract fields"
                )
        expected_record = {
            "title": planned.title,
            "body": planned.body,
            "assignee": planned.profile,
            "created_by": "hermes-factory",
            "workspace_kind": "dir",
            "workspace_path": str(rig.project.cwd),
            "max_runtime_seconds": expected_runtime,
            "skills": list(planned.skills),
            "max_retries": rig.policies.max_retries,
            "completion_contract": rig.policies.completion_contract,
        }
        if (
            any(listed.get(field) != value for field, value in expected_record.items())
            or listed.get("idempotency_key")
            not in {planned.idempotency_key, planned.idempotency_key + ":retry:1"}
            or detail["parents"]
        ):
            raise HermesCommandError(
                f"decomposition task {task.get('id')} drifted from Factory plan"
            )


def _verify_execution_graph(client: HermesClient, rig: RigSpec, plan: Any, task_ids: dict[str, str]) -> None:
    board = ["kanban", "--board", rig.board.slug]
    expected_children: dict[str, set[str]] = {task_id: set() for task_id in task_ids.values()}
    for planned in plan.tasks:
        child_id = task_ids[planned.key]
        for parent_key in planned.parents:
            expected_children[task_ids[parent_key]].add(child_id)
    for planned in plan.tasks:
        task_id = task_ids[planned.key]
        detail = _task_show(client.run_json(board + ["show", task_id, "--json"]))
        task = detail["task"]
        record = client.task_record(rig.board.slug, task_id)
        created = [event for event in detail["events"] if event.get("kind") == "created"]
        created_payload = created[0].get("payload") if len(created) == 1 else None
        expected_record = {
            "id": task_id,
            "title": planned.title,
            "body": planned.body,
            "assignee": planned.profile,
            "created_by": "hermes-factory",
            "workspace_kind": "dir",
            "workspace_path": str(rig.project.cwd),
            "idempotency_key": planned.idempotency_key,
            "max_runtime_seconds": _duration_seconds(_task_runtime(rig, planned)),
            "skills": list(planned.skills),
            "max_retries": rig.policies.max_retries,
            "completion_contract": rig.policies.completion_contract,
        }
        if (
            any(record.get(field) != value for field, value in expected_record.items())
            or task.get("id") != task_id
            or task.get("status") != record.get("status")
            or len(created) != 1
            or not isinstance(created_payload, dict)
            or created_payload.get("creator_task_id") is not None
            or set(detail["parents"]) != {task_ids[parent] for parent in planned.parents}
            or set(detail["children"]) != expected_children[task_id]
        ):
            raise HermesCommandError(
                f"materialized task {task_id} failed authoritative contract read-back"
            )


def _marker(task_ids: dict[str, str]) -> str:
    encoded = json.dumps(task_ids, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{_MATERIALIZED_MARKER_PREFIX}{hashlib.sha256(encoded).hexdigest()} -->"


def _materialize_group(
    client: HermesClient,
    rig: RigSpec,
    mission_id: str,
    listed_tasks: list[dict[str, Any]],
    board_records: list[dict[str, Any]],
) -> dict[str, str]:
    board = ["kanban", "--board", rig.board.slug]
    tasks_by_slice: dict[str, dict[str, Any]] = {}
    details_by_slice: dict[str, dict[str, Any]] = {}
    contracts_by_slice: dict[str, dict[str, Any]] = {}
    for listed in listed_tasks:
        identity = _decomposition_identity(listed, rig.project.slug)
        if identity is None or identity[0] != mission_id:
            raise HermesCommandError("invalid decomposition task identity")
        source_slice = identity[1]
        task_id = listed.get("id")
        if not isinstance(task_id, str) or not task_id or source_slice in tasks_by_slice:
            raise HermesCommandError("malformed or duplicate decomposition task")
        detail = _task_show(client.run_json(board + ["show", task_id, "--json"]))
        tasks_by_slice[source_slice] = listed
        details_by_slice[source_slice] = detail
        contracts_by_slice[source_slice] = _source_contract(listed.get("body"))

    mission = _mission_from_contracts(rig, mission_id, list(contracts_by_slice.values()))
    _verify_decomposition_cards(rig, mission, tasks_by_slice, details_by_slice)
    manifests: dict[str, DecompositionManifest] = {}
    slices_by_id = {slice_spec.id: slice_spec for slice_spec in mission.slices}
    for source_slice, source_contract in contracts_by_slice.items():
        expected_contract = decomposition_source_contract(rig, mission, slices_by_id[source_slice])
        if source_contract != expected_contract:
            raise HermesCommandError("decomposition task source contract is not current")
        manifests[source_slice] = _latest_manifest(
            details_by_slice[source_slice], rig=rig, mission=mission,
            source_slice=slices_by_id[source_slice], source_contract=expected_contract,
        )

    plan = compile_materialized_execution_phase(rig, mission, manifests)
    planned_by_key = {task.idempotency_key: task for task in plan.tasks}
    planned_keys = set(planned_by_key)
    existing_by_key = {
        record.get("idempotency_key"): record
        for record in board_records
        if record.get("idempotency_key") in planned_keys
        and record.get("status") != "archived"
    }
    if existing_by_key:
        # A card that carries a planned key but not the planned body is not this plan's
        # work, so nothing is created around it. A card that matches its planned body is
        # this plan's work: an apply interrupted before it finished leaves a strict subset
        # of those cards, and Hermes' create is idempotent on the idempotency key, so
        # re-applying the plan reuses them and creates only the missing cards instead of
        # wedging every later pass on a partial set.
        drifted = sorted(
            str(record.get("id"))
            for key, record in existing_by_key.items()
            if record.get("body") != planned_by_key[key].body
        )
        if drifted:
            raise HermesCommandError(
                "partial task set already uses planned execution idempotency keys: "
                + ", ".join(drifted)
            )
    if existing_by_key and set(existing_by_key) == planned_keys:
        task_ids = {
            task.key: _string(existing_by_key[task.idempotency_key].get("id"), "task.id")
            for task in plan.tasks
        }
        _verify_execution_graph(client, rig, plan, task_ids)
    else:
        task_ids = apply_mission(client, rig, plan).task_ids
        _verify_execution_graph(client, rig, plan, task_ids)
    marker = _marker(task_ids)
    anchor_id = _string(tasks_by_slice[mission.slices[0].id].get("id"), "task.id")
    comments = details_by_slice[mission.slices[0].id]["comments"]
    if not any(
        comment.get("body") == marker and comment.get("author") == "hermes-factory"
        for comment in comments
    ):
        client.run(board + ["comment", anchor_id, marker, "--author", "hermes-factory"])
    return task_ids


def materialize_completed_decompositions(client: HermesClient, rig: RigSpec) -> MaterializationResult:
    board = ["kanban", "--board", rig.board.slug]
    groups: dict[str, list[dict[str, Any]]] = {}
    board_records: list[dict[str, Any]] = []
    for listed in _task_list(client.run_json(board + ["list", "--json"])):
        task_id = listed.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise HermesCommandError("Hermes returned malformed task list JSON")
        record = client.task_record(rig.board.slug, task_id)
        board_records.append(record)
        task = _authoritative_decomposition_task(client, rig, listed, record)
        if task is None:
            continue
        identity = _decomposition_identity(task, rig.project.slug)
        assert identity is not None
        groups.setdefault(identity[0], []).append(task)
    materialized: dict[str, dict[str, str]] = {}
    rejected: dict[str, str] = {}
    for mission_id, tasks in groups.items():
        if not tasks or not all(task.get("status") == "done" for task in tasks):
            continue
        try:
            materialized[mission_id] = _materialize_group(
                client, rig, mission_id, tasks, board_records
            )
        except (HermesCommandError, SpecError, ValueError) as exc:
            rejected[mission_id] = str(exc)
    return MaterializationResult(materialized=materialized, rejected=rejected)
