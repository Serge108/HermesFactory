from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PureWindowsPath
from types import MappingProxyType
from typing import Any

from hermes_factory.errors import SpecError
from hermes_factory.rig_loader import validate_slug

_ROOT_KEYS = {
    "version",
    "project",
    "mission",
    "source_slice",
    "source_contract_sha256",
    "packets",
    "limitations",
}
_PACKET_KEYS = {
    "id",
    "title",
    "goal",
    "depends_on",
    "acceptance",
    "source_acceptance_indexes",
    "likely_files",
    "symbols",
    "first_artifact",
    "proof",
}
_PROOF_KEYS = {"commands", "artifacts", "changed_files_required"}
_PATH_SEPARATOR = re.compile(r"[\\/]")
_WINDOWS_FORBIDDEN_PATH_CHARS = re.compile(r'[<>:"|?*]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    "CLOCK$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
    "COM¹",
    "COM²",
    "COM³",
    "LPT¹",
    "LPT²",
    "LPT³",
}


@dataclass(frozen=True, slots=True)
class DecompositionProofSpec:
    commands: tuple[str, ...]
    artifacts: tuple[str, ...]
    changed_files_required: bool


@dataclass(frozen=True, slots=True)
class ExecutionPacketSpec:
    id: str
    title: str
    goal: str
    depends_on: tuple[str, ...]
    acceptance: tuple[str, ...]
    source_acceptance_indexes: tuple[int, ...]
    likely_files: tuple[str, ...]
    symbols: tuple[str, ...]
    first_artifact: str
    proof: DecompositionProofSpec


@dataclass(frozen=True, slots=True)
class DecompositionManifest:
    version: int
    project: str
    mission: str
    source_slice: str
    source_contract_sha256: str
    packets: tuple[ExecutionPacketSpec, ...]
    limitations: tuple[str, ...]

    @property
    def packets_by_id(self) -> Mapping[str, ExecutionPacketSpec]:
        return MappingProxyType({packet.id: packet for packet in self.packets})


def canonical_source_contract_sha256(source_contract: Any) -> str:
    """Hash JSON data using the Factory's stable canonical representation."""
    try:
        encoded = json.dumps(
            source_contract,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpecError(f"source contract is not canonical JSON data: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _positive_integer(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise SpecError(f"{field} must be a positive integer")
    return value


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise SpecError(f"{field} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise SpecError(f"{field} keys must be strings")
    return value


def _validate_exact_keys(data: dict[str, Any], keys: set[str], field: str) -> None:
    unknown = sorted(set(data) - keys)
    if unknown:
        raise SpecError(f"{field} has unknown key {unknown[0]!r}")
    missing = sorted(keys - set(data))
    if missing:
        raise SpecError(f"{field} is missing required key {missing[0]!r}")


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{field} must be a string")
    trimmed = value.strip()
    if not trimmed:
        raise SpecError(f"{field} must not be empty")
    return trimmed


def _string_list(
    value: Any,
    field: str,
    *,
    require_nonempty: bool = False,
) -> tuple[str, ...]:
    if type(value) is not list:
        raise SpecError(f"{field} must be a list")
    result = tuple(_string(item, f"{field}[{index}]") for index, item in enumerate(value))
    if require_nonempty and not result:
        raise SpecError(f"{field} must not be empty")
    return result


def _safe_project_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SpecError(f"{field} must be a project-relative safe path")
    path = value
    windows_path = PureWindowsPath(path)
    segments = _PATH_SEPARATOR.split(path)
    if (
        windows_path.is_absolute()
        or bool(windows_path.drive)
        or path.startswith(("/", "\\"))
        or ":" in path
        or any(segment in {"", ".", ".."} for segment in segments)
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or any(_WINDOWS_FORBIDDEN_PATH_CHARS.search(segment) for segment in segments)
        or any(segment.endswith((" ", ".")) for segment in segments)
        or any(
            segment.split(".", 1)[0].rstrip(" .").upper() in _WINDOWS_RESERVED_NAMES
            for segment in segments
        )
    ):
        raise SpecError(f"{field} must be a project-relative safe path")
    return path


def _path_list(value: Any, field: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise SpecError(f"{field} must be a list")
    return tuple(
        _safe_project_path(item, f"{field}[{index}]")
        for index, item in enumerate(value)
    )


def _acceptance_indexes(
    value: Any,
    field: str,
    source_acceptance_count: int,
) -> tuple[int, ...]:
    if type(value) is not list:
        raise SpecError(f"{field} must be a list")
    result: list[int] = []
    seen: set[int] = set()
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if type(item) is not int:
            raise SpecError(f"{item_field} must be an integer in range")
        if item < 0 or item >= source_acceptance_count:
            raise SpecError(
                f"{item_field} is outside source acceptance range "
                f"0..{source_acceptance_count - 1}"
            )
        if item in seen:
            raise SpecError(f"{field} must not contain duplicate indexes")
        seen.add(item)
        result.append(item)
    return tuple(result)


def _parse_proof(value: Any, packet_id: str) -> DecompositionProofSpec:
    field = f"packet {packet_id!r}.proof"
    data = _mapping(value, field)
    _validate_exact_keys(data, _PROOF_KEYS, field)
    commands = _string_list(
        data["commands"], f"{field}.commands", require_nonempty=True
    )
    changed_files_required = data["changed_files_required"]
    if type(changed_files_required) is not bool:
        raise SpecError(f"{field}.changed_files_required must be a boolean")
    return DecompositionProofSpec(
        commands=commands,
        artifacts=_path_list(data["artifacts"], f"{field}.artifacts"),
        changed_files_required=changed_files_required,
    )


def _parse_packets(
    value: Any,
    *,
    source_acceptance_count: int,
    max_packets: int,
    max_packet_acceptance_items: int,
) -> tuple[ExecutionPacketSpec, ...]:
    if type(value) is not list:
        raise SpecError("decomposition.packets must be a list")
    if len(value) > max_packets:
        raise SpecError(f"decomposition.packets exceeds limit {max_packets}")

    parsed_data: list[tuple[str, dict[str, Any]]] = []
    ids_by_folded: dict[str, str] = {}
    for index, raw_packet in enumerate(value):
        field = f"decomposition.packets[{index}]"
        packet = _mapping(raw_packet, field)
        _validate_exact_keys(packet, _PACKET_KEYS, field)
        packet_id = validate_slug(packet["id"], f"{field}.id")
        folded = packet_id.casefold()
        if folded in ids_by_folded:
            raise SpecError(
                f"duplicate packet id after trimming and case folding: {packet_id!r}"
            )
        ids_by_folded[folded] = packet_id
        parsed_data.append((packet_id, packet))

    packets: list[ExecutionPacketSpec] = []
    for packet_id, packet in parsed_data:
        field = f"packet {packet_id!r}"
        raw_dependencies = _string_list(packet["depends_on"], f"{field}.depends_on")
        dependencies: list[str] = []
        seen_dependencies: set[str] = set()
        for dependency in raw_dependencies:
            folded = dependency.casefold()
            canonical = ids_by_folded.get(folded)
            if canonical is None:
                raise SpecError(f"packet {packet_id!r} depends on unknown packet {dependency!r}")
            if folded in seen_dependencies:
                raise SpecError(f"{field}.depends_on must not contain duplicates")
            seen_dependencies.add(folded)
            dependencies.append(canonical)

        acceptance = _string_list(
            packet["acceptance"], f"{field}.acceptance", require_nonempty=True
        )
        if len(acceptance) > max_packet_acceptance_items:
            raise SpecError(
                f"{field}.acceptance exceeds limit {max_packet_acceptance_items}"
            )
        packets.append(
            ExecutionPacketSpec(
                id=packet_id,
                title=_string(packet["title"], f"{field}.title"),
                goal=_string(packet["goal"], f"{field}.goal"),
                depends_on=tuple(dependencies),
                acceptance=acceptance,
                source_acceptance_indexes=_acceptance_indexes(
                    packet["source_acceptance_indexes"],
                    f"{field}.source_acceptance_indexes",
                    source_acceptance_count,
                ),
                likely_files=_path_list(
                    packet["likely_files"], f"{field}.likely_files"
                ),
                symbols=_string_list(packet["symbols"], f"{field}.symbols"),
                first_artifact=_string(
                    packet["first_artifact"], f"{field}.first_artifact"
                ),
                proof=_parse_proof(packet["proof"], packet_id),
            )
        )
    return tuple(packets)


def _validate_packet_graph(packets: tuple[ExecutionPacketSpec, ...]) -> None:
    remaining = {packet.id: set(packet.depends_on) for packet in packets}
    order = [packet.id for packet in packets]
    while order:
        ready = next((packet_id for packet_id in order if not remaining[packet_id]), None)
        if ready is None:
            raise SpecError(f"packet dependency cycle among: {', '.join(order)}")
        order.remove(ready)
        for packet_id in order:
            remaining[packet_id].discard(ready)


def parse_decomposition_manifest(
    value: Any,
    *,
    expected_project: str,
    expected_mission: str,
    expected_source_slice: str,
    expected_source_contract_sha256: str,
    source_contract: Any,
    max_packets: int,
    max_packet_acceptance_items: int,
) -> DecompositionManifest:
    """Validate untrusted version-1 manifest data and return immutable models."""
    _positive_integer(max_packets, "max_packets")
    _positive_integer(max_packet_acceptance_items, "max_packet_acceptance_items")

    if canonical_source_contract_sha256(source_contract) != expected_source_contract_sha256:
        raise SpecError("source contract does not match expected source contract hash")
    source_data = _mapping(source_contract, "source contract")
    source_acceptance = _string_list(
        source_data.get("source_acceptance"),
        "source contract.source_acceptance",
        require_nonempty=True,
    )
    source_acceptance_count = len(source_acceptance)
    source_slice = _mapping(source_data.get("source_slice"), "source contract.source_slice")
    raw_source_proof = source_slice.get("proof")
    source_commands: tuple[str, ...] = ()
    source_artifacts: tuple[str, ...] = ()
    source_changed_files_required = False
    if raw_source_proof is not None:
        source_proof = _mapping(raw_source_proof, "source contract.source_slice.proof")
        _validate_exact_keys(source_proof, _PROOF_KEYS, "source contract.source_slice.proof")
        source_commands = _string_list(
            source_proof["commands"], "source contract.source_slice.proof.commands"
        )
        source_artifacts = _path_list(
            source_proof["artifacts"], "source contract.source_slice.proof.artifacts"
        )
        source_changed_files_required = source_proof["changed_files_required"]
        if type(source_changed_files_required) is not bool:
            raise SpecError(
                "source contract.source_slice.proof.changed_files_required must be a boolean"
            )

    data = _mapping(value, "decomposition manifest")
    _validate_exact_keys(data, _ROOT_KEYS, "decomposition manifest")

    version = data["version"]
    if type(version) is not int or version != 1:
        raise SpecError(f"unsupported decomposition version {version!r}")

    identities = (
        ("project", expected_project),
        ("mission", expected_mission),
        ("source_slice", expected_source_slice),
        ("source_contract_sha256", expected_source_contract_sha256),
    )
    loaded_identities: dict[str, str] = {}
    for field, expected in identities:
        loaded = _string(data[field], f"decomposition.{field}")
        if loaded != expected:
            raise SpecError(
                f"decomposition.{field} does not match expected value {expected!r}"
            )
        loaded_identities[field] = loaded

    limitations = _string_list(data["limitations"], "decomposition.limitations")
    if limitations:
        raise SpecError(
            "decomposition has blocking limitations and cannot be materialized: "
            + "; ".join(limitations)
        )

    packets = _parse_packets(
        data["packets"],
        source_acceptance_count=source_acceptance_count,
        max_packets=max_packets,
        max_packet_acceptance_items=max_packet_acceptance_items,
    )
    _validate_packet_graph(packets)

    trusted_commands = set(source_commands)
    covered_artifacts: set[str] = set()
    for packet in packets:
        for acceptance_index in packet.source_acceptance_indexes:
            required_acceptance = source_acceptance[acceptance_index]
            if required_acceptance not in packet.acceptance:
                raise SpecError(
                    f"packet {packet.id!r}.acceptance must include source acceptance "
                    f"index {acceptance_index} verbatim"
                )
        for command in packet.proof.commands:
            if command not in trusted_commands:
                raise SpecError(
                    f"packet {packet.id!r}.proof contains untrusted proof command {command!r}"
                )
        if source_changed_files_required and not packet.proof.changed_files_required:
            raise SpecError(
                f"packet {packet.id!r}.proof cannot weaken changed_files_required"
            )
        covered_artifacts.update(packet.proof.artifacts)

    missing_commands = sorted(set(source_commands) - {
        command for packet in packets for command in packet.proof.commands
    })
    if missing_commands:
        raise SpecError(
            "decomposition proof omits source commands: " + "; ".join(missing_commands)
        )
    missing_artifacts = sorted(set(source_artifacts) - covered_artifacts)
    if missing_artifacts:
        raise SpecError(
            "decomposition proof omits source artifacts: " + "; ".join(missing_artifacts)
        )

    covered = {
        acceptance_index
        for packet in packets
        for acceptance_index in packet.source_acceptance_indexes
    }
    missing = sorted(set(range(source_acceptance_count)) - covered)
    if missing:
        rendered = ", ".join(str(index) for index in missing)
        raise SpecError(f"missing source acceptance coverage for indexes: {rendered}")

    return DecompositionManifest(
        version=version,
        project=loaded_identities["project"],
        mission=loaded_identities["mission"],
        source_slice=loaded_identities["source_slice"],
        source_contract_sha256=loaded_identities["source_contract_sha256"],
        packets=packets,
        limitations=limitations,
    )


source_contract_sha256 = canonical_source_contract_sha256
compute_source_contract_sha256 = canonical_source_contract_sha256
validate_decomposition_manifest = parse_decomposition_manifest
