from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hermes_factory.errors import SpecError
from hermes_factory.models import MissionSpec, ProofSpec, RigSpec, SliceSpec
from hermes_factory.rig_loader import _UniqueKeyLoader, _string, _boolean, _mapping, _validate_keys, validate_slug

_MISSION_KEYS = {
    "version",
    "id",
    "title",
    "intent",
    "workflow",
    "human_acceptance",
    "acceptance",
    "slices",
}
_MISSION_REQUIRED_KEYS = {
    "version",
    "id",
    "title",
    "intent",
    "workflow",
    "acceptance",
    "slices",
}
_SLICE_KEYS = {
    "id",
    "title",
    "goal",
    "depends_on",
    "acceptance",
    "proof",
}
_SLICE_REQUIRED_KEYS = {"id", "title", "goal", "depends_on", "acceptance"}
_PROOF_KEYS = {"commands", "artifacts", "changed_files_required"}


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError(f"cannot load mission {path}: {exc}") from exc
    return _mapping(value, "mission")


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SpecError(f"{field} must be a list")
    return tuple(_string(item, f"{field}[{index}]") for index, item in enumerate(value))


def _artifact_names(value: Any, field: str) -> tuple[str, ...]:
    artifacts = _string_list(value, field)
    for index, artifact in enumerate(artifacts):
        if (
            "/" in artifact
            or "\\" in artifact
            or artifact in {".", ".."}
            or artifact.startswith(".")
            or len(artifact) > 200
        ):
            raise SpecError(f"{field}[{index}] artifact must be a safe filename")
    normalized = [artifact.casefold() for artifact in artifacts]
    if len(set(normalized)) != len(normalized):
        raise SpecError(f"{field} must not contain duplicate filenames")
    return artifacts


def _load_proof(value: Any, slice_id: str, rig: RigSpec) -> ProofSpec | None:
    if value is None:
        if rig.policies.require_proof:
            raise SpecError(f"slice {slice_id!r} must define proof")
        return None
    proof = _mapping(value, f"slice {slice_id!r}.proof")
    _validate_keys(proof, _PROOF_KEYS, f"slice {slice_id!r}.proof")
    commands = _string_list(proof["commands"], f"slice {slice_id!r}.proof.commands")
    if not commands:
        # A packet must carry a proof command anchored in the source contract, so an empty
        # command list leaves the trust set empty and makes the mission unsolvable: refuse it
        # here rather than at materialization.
        raise SpecError(
            f"slice {slice_id!r}.proof.commands must declare at least one command"
        )
    return ProofSpec(
        commands=commands,
        artifacts=_artifact_names(
            proof["artifacts"], f"slice {slice_id!r}.proof.artifacts"
        ),
        changed_files_required=_boolean(
            proof["changed_files_required"],
            f"slice {slice_id!r}.proof.changed_files_required",
        ),
    )


def _load_slices(value: Any, rig: RigSpec) -> tuple[SliceSpec, ...]:
    source = value
    if not isinstance(source, list) or not source:
        raise SpecError("mission.slices must be a non-empty list")
    parsed: list[tuple[str, dict[str, Any]]] = []
    for raw_slice in source:
        data = _mapping(raw_slice, "slice")
        unknown = sorted(set(data) - _SLICE_KEYS)
        if unknown:
            raise SpecError(f"slice has unknown key {unknown[0]!r}")
        missing = sorted(_SLICE_REQUIRED_KEYS - set(data))
        if missing:
            raise SpecError(f"slice is missing required key {missing[0]!r}")
        slice_id = validate_slug(data["id"], "slice.id")
        if any(existing == slice_id for existing, _ in parsed):
            raise SpecError(f"duplicate slice id {slice_id!r}")
        parsed.append((slice_id, data))

    known_ids = {slice_id for slice_id, _ in parsed}
    slices: list[SliceSpec] = []
    for slice_id, data in parsed:
        dependencies = _string_list(data["depends_on"], f"slice {slice_id!r}.depends_on")
        for dependency in dependencies:
            if dependency not in known_ids:
                raise SpecError(f"slice {slice_id!r} depends on unknown slice {dependency!r}")
        slices.append(
            SliceSpec(
                id=slice_id,
                title=_string(data["title"], f"slice {slice_id!r}.title"),
                goal=_string(data["goal"], f"slice {slice_id!r}.goal"),
                depends_on=dependencies,
                acceptance=_string_list(
                    data["acceptance"], f"slice {slice_id!r}.acceptance"
                ),
                proof=_load_proof(data.get("proof"), slice_id, rig),
            )
        )
    return tuple(slices)


def topological_slices(mission: MissionSpec) -> tuple[SliceSpec, ...]:
    by_id = {slice_spec.id: slice_spec for slice_spec in mission.slices}
    remaining: dict[str, set[str]] = {
        slice_spec.id: set(slice_spec.depends_on) for slice_spec in mission.slices
    }
    order = [slice_spec.id for slice_spec in mission.slices]
    result: list[SliceSpec] = []
    while order:
        chosen = next((candidate for candidate in order if not remaining[candidate]), None)
        if chosen is None:
            raise SpecError(
                f"slice dependency cycle among: {', '.join(order)}"
            )
        order.remove(chosen)
        result.append(by_id[chosen])
        for other in order:
            remaining[other].discard(chosen)
    return tuple(result)


def load_mission(path: Path, rig: RigSpec) -> MissionSpec:
    data = _load_yaml(Path(path).resolve())
    unknown = sorted(set(data) - _MISSION_KEYS)
    if unknown:
        raise SpecError(f"mission has unknown key {unknown[0]!r}")
    missing = sorted(_MISSION_REQUIRED_KEYS - set(data))
    if missing:
        raise SpecError(f"mission is missing required key {missing[0]!r}")

    version = data["version"]
    if type(version) is not int or version != 1:
        raise SpecError(f"unsupported version {version!r}")

    mission_id = validate_slug(data["id"], "mission.id")
    title = _string(data["title"], "mission.title")
    intent = _string(data["intent"], "mission.intent")
    workflow = _string(data["workflow"], "mission.workflow")
    if workflow not in rig.workflows:
        raise SpecError(f"unknown workflow {workflow!r}")

    human_acceptance = rig.policies.human_acceptance
    if "human_acceptance" in data:
        value = _boolean(data["human_acceptance"], "mission.human_acceptance")
        if not value and rig.policies.human_acceptance:
            raise SpecError("mission may not weaken human_acceptance from the rig policy")
        human_acceptance = value
    if human_acceptance and not rig.policies.decision_authority_declared:
        # A mission that requests the human stop must say which authority the rig
        # grants; an omitted authority must not silently decide that boundary.
        raise SpecError(
            "mission human_acceptance: true requires an explicit policies.decision_authority in the rig"
        )

    acceptance = _string_list(data["acceptance"], "mission.acceptance")
    if not acceptance:
        raise SpecError("mission acceptance must not be empty")

    slices = _load_slices(data["slices"], rig)
    mission = MissionSpec(
        version=version,
        id=mission_id,
        title=title,
        intent=intent,
        workflow=workflow,
        human_acceptance=human_acceptance,
        acceptance=acceptance,
        slices=slices,
    )
    topological_slices(mission)
    return mission
