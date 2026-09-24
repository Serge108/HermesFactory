from pathlib import Path

import pytest

from hermes_factory.board import BoardApplyResult, ensure_board
from hermes_factory.errors import HermesCommandError
from hermes_factory.rig_loader import load_rig


class FakeHermesClient:
    def __init__(self, board_lists: list[object]) -> None:
        self.board_lists = board_lists
        self.json_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []

    def run_json(self, args: list[str]) -> object:
        self.json_calls.append(args)
        return self.board_lists.pop(0)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        return ""


def test_existing_active_board_is_reused(valid_rig: Path):
    client = FakeHermesClient(
        [[{"slug": "other", "archived": False}, {"slug": "demo-rig", "archived": False}]]
    )

    result = ensure_board(client, load_rig(valid_rig))

    assert result == BoardApplyResult(slug="demo-rig", created=False)
    assert client.json_calls == [["kanban", "boards", "list", "--json", "--all"]]
    assert client.run_calls == []


def test_missing_board_is_created_with_exact_arguments(valid_rig: Path):
    client = FakeHermesClient([[]])
    rig = load_rig(valid_rig)

    result = ensure_board(client, rig)

    assert result == BoardApplyResult(slug="demo-rig", created=True)
    assert client.json_calls == [["kanban", "boards", "list", "--json", "--all"]]
    assert client.run_calls == [
        [
            "kanban",
            "boards",
            "create",
            "demo-rig",
            "--name",
            "Demo Rig",
            "--description",
            "Managed by Hermes Factory",
            "--default-workdir",
            str(rig.project.cwd),
        ]
    ]


def test_second_ensure_reuses_board_from_updated_listing(valid_rig: Path):
    client = FakeHermesClient(
        [[], [{"slug": "demo-rig", "archived": False}]]
    )
    rig = load_rig(valid_rig)

    first = ensure_board(client, rig)
    second = ensure_board(client, rig)

    assert first.created is True
    assert second.created is False
    assert len(client.run_calls) == 1
    assert all("switch" not in call for call in client.json_calls + client.run_calls)


def test_archived_same_slug_does_not_count_as_existing(valid_rig: Path):
    client = FakeHermesClient([[{"slug": "demo-rig", "archived": True}]])

    result = ensure_board(client, load_rig(valid_rig))

    assert result.created is True
    assert len(client.run_calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        None,
        {"boards": []},
        ["demo-rig"],
        [{}],
        [{"slug": "demo-rig", "archived": "false"}],
    ],
)
def test_malformed_board_list_fails_with_clear_error(valid_rig: Path, response: object):
    client = FakeHermesClient([response])

    with pytest.raises(
        HermesCommandError, match="Hermes returned malformed board list JSON"
    ):
        ensure_board(client, load_rig(valid_rig))

    assert client.run_calls == []
