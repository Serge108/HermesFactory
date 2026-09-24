from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from hermes_factory.errors import SpecError
from hermes_factory.models import BoardSpec, PolicySpec, ProjectSpec, RigSpec, SeatSpec, WorkflowSpec

_SLUG = re.compile(r"^(?:[a-z0-9]|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$")
_DURATION = re.compile(r"^[1-9][0-9]*(?:s|m|h|d)$")

_ROOT_KEYS = {"version", "project", "board", "seats", "workflows", "policies"}
_PROJECT_KEYS = {"slug", "name", "cwd"}
_BOARD_KEYS = {"slug", "name"}
_SEAT_KEYS = {"profile", "role", "skills"}
_WORKFLOW_KEYS = {"implementation_seat", "review_seat", "acceptance_seat"}
_POLICY_KEYS = {
    "require_proof",
    "human_acceptance",
    "max_retries",
    "max_runtime",
    "completion_contract",
}


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: _UniqueKeyLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def validate_slug(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{field} must be a string containing a valid slug")
    trimmed = value.strip()
    if not _SLUG.fullmatch(trimmed):
        raise SpecError(f"{field} is not a valid slug")
    return trimmed


def parse_duration(value: str) -> str:
    if not isinstance(value, str):
        raise SpecError("duration must be a string such as '30m'")
    trimmed = value.strip()
    if not _DURATION.fullmatch(trimmed):
        raise SpecError(f"invalid duration {value!r}; expected a positive integer plus s, m, h, or d")
    return trimmed


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SpecError(f"{field} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise SpecError(f"{field} keys must be strings")
    return value


def _validate_keys(data: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SpecError(f"{field} has unknown key {unknown[0]!r}")
    missing = sorted(allowed - set(data))
    if missing:
        raise SpecError(f"{field} is missing required key {missing[0]!r}")


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{field} must be a string")
    trimmed = value.strip()
    if not trimmed:
        raise SpecError(f"{field} must not be empty")
    return trimmed


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise SpecError(f"{field} must be a boolean")
    return value


def _positive_integer(value: Any, field: str) -> int:
    if type(value) is not int or value < 1:
        raise SpecError(f"{field} must be an integer of at least 1")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError(f"cannot load rig {path}: {exc}") from exc
    return _mapping(value, "rig")


def _load_seats(value: Any) -> dict[str, SeatSpec]:
    source = _mapping(value, "seats")
    if not source:
        raise SpecError("seats must not be empty")
    seats: dict[str, SeatSpec] = {}
    for raw_name, raw_seat in source.items():
        name = validate_slug(raw_name, "seat slug")
        seat = _mapping(raw_seat, f"seat {name!r}")
        _validate_keys(seat, _SEAT_KEYS, f"seat {name!r}")
        raw_skills = seat["skills"]
        if not isinstance(raw_skills, list):
            raise SpecError(f"seat {name!r}.skills must be a list")
        skills = tuple(
            _string(skill, f"seat {name!r}.skills[{index}]")
            for index, skill in enumerate(raw_skills)
        )
        seats[name] = SeatSpec(
            profile=_string(seat["profile"], f"seat {name!r}.profile"),
            role=_string(seat["role"], f"seat {name!r}.role"),
            skills=skills,
        )
    return seats


def _load_workflows(value: Any, seats: dict[str, SeatSpec]) -> dict[str, WorkflowSpec]:
    source = _mapping(value, "workflows")
    if not source:
        raise SpecError("workflows must not be empty")
    workflows: dict[str, WorkflowSpec] = {}
    for raw_name, raw_workflow in source.items():
        name = validate_slug(raw_name, "workflow slug")
        workflow = _mapping(raw_workflow, f"workflow {name!r}")
        _validate_keys(workflow, _WORKFLOW_KEYS, f"workflow {name!r}")
        loaded = WorkflowSpec(
            implementation_seat=_string(
                workflow["implementation_seat"],
                f"workflow {name!r}.implementation_seat",
            ),
            review_seat=_string(
                workflow["review_seat"], f"workflow {name!r}.review_seat"
            ),
            acceptance_seat=_string(
                workflow["acceptance_seat"],
                f"workflow {name!r}.acceptance_seat",
            ),
        )
        for seat_name in (
            loaded.implementation_seat,
            loaded.review_seat,
            loaded.acceptance_seat,
        ):
            if seat_name not in seats:
                raise SpecError(f"workflow {name!r} references unknown seat {seat_name!r}")
        workflows[name] = loaded
    return workflows


def load_rig(path: Path) -> RigSpec:
    path = Path(path).resolve()
    data = _load_yaml(path)
    _validate_keys(data, _ROOT_KEYS, "rig")

    version = data["version"]
    if type(version) is not int or version != 1:
        raise SpecError(f"unsupported version {version!r}")

    project = _mapping(data["project"], "project")
    _validate_keys(project, _PROJECT_KEYS, "project")
    raw_cwd = _string(project["cwd"], "project.cwd")
    cwd = Path(raw_cwd)
    if not cwd.is_absolute():
        cwd = path.parent / cwd
    cwd = cwd.resolve()
    if not cwd.exists():
        raise SpecError(f"project.cwd does not exist: {cwd}")
    if not cwd.is_dir():
        raise SpecError(f"project.cwd is not a directory: {cwd}")

    board = _mapping(data["board"], "board")
    _validate_keys(board, _BOARD_KEYS, "board")
    seats = _load_seats(data["seats"])
    workflows = _load_workflows(data["workflows"], seats)

    policies = _mapping(data["policies"], "policies")
    _validate_keys(policies, _POLICY_KEYS, "policies")

    return RigSpec(
        version=version,
        project=ProjectSpec(
            slug=validate_slug(project["slug"], "project.slug"),
            name=_string(project["name"], "project.name"),
            cwd=cwd,
        ),
        board=BoardSpec(
            slug=validate_slug(board["slug"], "board.slug"),
            name=_string(board["name"], "board.name"),
        ),
        seats=dict(seats),
        workflows=dict(workflows),
        policies=PolicySpec(
            require_proof=_boolean(policies["require_proof"], "policies.require_proof"),
            human_acceptance=_boolean(
                policies["human_acceptance"], "policies.human_acceptance"
            ),
            max_retries=_positive_integer(
                policies["max_retries"], "policies.max_retries"
            ),
            max_runtime=parse_duration(policies["max_runtime"]),
            completion_contract=_string(
                policies["completion_contract"], "policies.completion_contract"
            ),
        ),
    )
