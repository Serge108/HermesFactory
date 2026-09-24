from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_factory.errors import HermesCommandError
from hermes_factory.hermes_cli import HermesClient
from hermes_factory.models import RigSpec


@dataclass(frozen=True, slots=True)
class BoardApplyResult:
    slug: str
    created: bool


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
