import json
from pathlib import Path

import pytest

from hermes_factory.cli import main
from hermes_factory.errors import HermesCommandError


class FakeHermesClient:
    def __init__(self, profiles: set[str]) -> None:
        self.profiles = profiles
        self.calls: list[str] = []

    def profile_exists(self, profile: str) -> bool:
        self.calls.append(profile)
        return profile in self.profiles


class ApplyHermesClient(FakeHermesClient):
    def __init__(self, profiles: set[str], boards: object) -> None:
        super().__init__(profiles)
        self.boards = boards
        self.command_calls: list[list[str]] = []
        self.events: list[tuple[str, object]] = []

    def profile_exists(self, profile: str) -> bool:
        self.events.append(("profile", profile))
        return super().profile_exists(profile)

    def run_json(self, args: list[str]) -> object:
        self.events.append(("run_json", args))
        self.command_calls.append(args)
        return self.boards

    def run(self, args: list[str]) -> str:
        self.events.append(("run", args))
        self.command_calls.append(args)
        return ""


def test_version_command(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "hermes-factory 0.1.0"


def test_validate_reports_valid_rig(valid_rig: Path, capsys):
    client = FakeHermesClient({"builder", "reviewer"})

    result = main(
        ["validate", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.strip() == "valid: demo (2 seats)"
    assert captured.err == ""
    assert client.calls == ["builder", "reviewer"]


def test_validate_reports_missing_profiles(valid_rig: Path, capsys):
    client = FakeHermesClient({"reviewer"})

    result = main(
        ["validate", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "error: missing Hermes profiles: builder"


def test_validate_rejects_malformed_rig_without_invoking_hermes(
    valid_rig: Path, capsys
):
    valid_rig.write_text("version: 2\n", encoding="utf-8")

    def fail_if_called(executable: str):
        raise AssertionError("Hermes client must not be constructed")

    result = main(
        ["validate", "--rig", str(valid_rig)],
        client_factory=fail_if_called,
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "Traceback" not in captured.err


def test_validate_warns_when_seats_share_a_profile(valid_rig: Path, capsys):
    rig_text = valid_rig.read_text(encoding="utf-8").replace(
        "reviewer: {profile: reviewer,", "reviewer: {profile: builder,"
    )
    valid_rig.write_text(rig_text, encoding="utf-8")
    client = FakeHermesClient({"builder"})

    result = main(
        ["validate", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.strip() == "valid: demo (2 seats)"
    assert captured.err.strip() == (
        "warning: seats builder, reviewer share Hermes profile 'builder'; "
        "these seats are not isolated"
    )
    assert client.calls == ["builder"]


def test_validate_passes_custom_hermes_executable_to_factory(valid_rig: Path):
    client = FakeHermesClient({"builder", "reviewer"})
    executables: list[str] = []

    def client_factory(executable: str):
        executables.append(executable)
        return client

    assert main(
        ["validate", "--rig", str(valid_rig), "--hermes", "F:/bin/hermes.exe"],
        client_factory=client_factory,
    ) == 0
    assert executables == ["F:/bin/hermes.exe"]


def test_validate_reports_hermes_infrastructure_error_as_error(valid_rig: Path, capsys):
    class FailingClient:
        def profile_exists(self, profile: str) -> bool:
            raise HermesCommandError("failed to run Hermes")

    result = main(
        ["validate", "--rig", str(valid_rig)],
        client_factory=lambda executable: FailingClient(),
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "error: failed to run Hermes"
    assert "missing Hermes profiles" not in captured.err
    assert "Traceback" not in captured.err


def test_apply_creates_missing_board_after_profile_validation(valid_rig: Path, capsys):
    client = ApplyHermesClient({"builder", "reviewer"}, [])

    result = main(
        ["apply", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.strip() == "created board demo-rig"
    assert captured.err == ""
    assert client.calls == ["builder", "reviewer"]
    assert client.events[:3] == [
        ("profile", "builder"),
        ("profile", "reviewer"),
        ("run_json", ["kanban", "boards", "list", "--json", "--all"]),
    ]
    assert client.command_calls[0] == [
        "kanban",
        "boards",
        "list",
        "--json",
        "--all",
    ]


def test_apply_reports_existing_board(valid_rig: Path, capsys):
    client = ApplyHermesClient(
        {"builder", "reviewer"}, [{"slug": "demo-rig", "archived": False}]
    )

    result = main(
        ["apply", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.strip() == "board demo-rig already exists"
    assert captured.err == ""
    assert len(client.command_calls) == 1


def test_apply_does_not_list_or_create_board_when_profile_is_missing(
    valid_rig: Path, capsys
):
    client = ApplyHermesClient({"reviewer"}, [])

    result = main(
        ["apply", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "error: missing Hermes profiles: builder"
    assert client.command_calls == []


def test_apply_rejects_malformed_rig_without_invoking_hermes(valid_rig: Path, capsys):
    valid_rig.write_text("version: 2\n", encoding="utf-8")

    def fail_if_called(executable: str):
        raise AssertionError("Hermes client must not be constructed")

    result = main(
        ["apply", "--rig", str(valid_rig)],
        client_factory=fail_if_called,
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "Traceback" not in captured.err


def test_apply_passes_custom_hermes_executable_to_factory(valid_rig: Path, capsys):
    client = ApplyHermesClient(
        {"builder", "reviewer"}, [{"slug": "demo-rig", "archived": False}]
    )
    executables: list[str] = []

    result = main(
        ["apply", "--rig", str(valid_rig), "--hermes", "F:/bin/hermes.exe"],
        client_factory=lambda executable: executables.append(executable) or client,
    )

    assert result == 0
    assert executables == ["F:/bin/hermes.exe"]
    assert capsys.readouterr().out.strip() == "board demo-rig already exists"


def test_apply_reports_malformed_board_list_as_clean_error(valid_rig: Path, capsys):
    client = ApplyHermesClient({"builder", "reviewer"}, {"boards": []})

    result = main(
        ["apply", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "error: Hermes returned malformed board list JSON"
    assert "Traceback" not in captured.err


_MISSION = """\
version: 1
id: demo-mission
title: Demo mission
intent: Ship one verified behavior without expanding scope.
workflow: delivery
human_acceptance: false
acceptance:
  - The requested behavior exists.
slices:
  - id: implement
    title: Implement the requested behavior
    goal: Make the smallest code change satisfying the mission.
    depends_on: []
    acceptance:
      - A failing test was observed before implementation.
    proof:
      commands:
        - uv run pytest -q
      artifacts: []
      changed_files_required: true
"""


@pytest.fixture
def mission_file(tmp_path: Path) -> Path:
    path = tmp_path / "mission.yaml"
    path.write_text(_MISSION, encoding="utf-8")
    return path


class MissionHermesClient:
    def __init__(
        self,
        profiles: set[str],
        boards: object,
        task_responses: list[object],
    ) -> None:
        self.profiles = profiles
        self.boards = boards
        self.task_responses = task_responses
        self.command_calls: list[list[str]] = []

    def profile_exists(self, profile: str) -> bool:
        return profile in self.profiles

    def run_json(self, args: list[str]) -> object:
        self.command_calls.append(args)
        if len(args) > 1 and args[1] == "boards":
            return self.boards
        return self.task_responses.pop(0)

    def run(self, args: list[str]) -> str:
        self.command_calls.append(args)
        return ""


_TASK_RESPONSES = [
    {"id": "task-1", "status": "ready", "title": "Implement the requested behavior"},
    {"id": "task-2", "status": "ready", "title": "Review the requested behavior"},
    {"id": "task-3", "status": "ready", "title": "Accept Demo mission"},
]


def test_mission_plan_prints_json_plan_without_invoking_hermes(
    valid_rig: Path, mission_file: Path, capsys
):
    def fail_if_called(executable: str):
        raise AssertionError("mission plan must never invoke Hermes")

    result = main(
        ["mission", "plan", str(mission_file), "--rig", str(valid_rig), "--json"],
        client_factory=fail_if_called,
    )

    captured = capsys.readouterr()
    assert result == 0
    payload = json.loads(captured.out)
    assert payload["board_slug"] == "demo-rig"
    assert [task["key"] for task in payload["tasks"]] == [
        "implement:implementation",
        "implement:review",
        "acceptance",
    ]
    assert [task["profile"] for task in payload["tasks"]] == [
        "builder",
        "reviewer",
        "builder",
    ]
    assert payload["tasks"][1]["parents"] == ["implement:implementation"]
    assert payload["tasks"][2]["parents"] == ["implement:review"]
    assert payload["tasks"][0]["idempotency_key"] == (
        "factory:demo:demo-mission:implement:implementation"
    )


def test_mission_plan_rejects_malformed_mission_as_clean_error(
    valid_rig: Path, mission_file: Path, capsys
):
    mission_file.write_text("version: 2\n", encoding="utf-8")

    result = main(
        ["mission", "plan", str(mission_file), "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: (_ for _ in ()).throw(
            AssertionError("Hermes must not be invoked")
        ),
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert "Traceback" not in captured.err


def test_mission_apply_creates_tasks_and_prints_task_id_map(
    valid_rig: Path, mission_file: Path, capsys
):
    client = MissionHermesClient({"builder", "reviewer"}, [], list(_TASK_RESPONSES))

    result = main(
        ["mission", "apply", str(mission_file), "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == {
        "task_ids": {
            "implement:implementation": "task-1",
            "implement:review": "task-2",
            "acceptance": "task-3",
        }
    }
    create_calls = [args for args in client.command_calls if args[3] == "create"]
    assert len(create_calls) == 3
    assert create_calls[0][:4] == ["kanban", "--board", "demo-rig", "create"]
    for call in create_calls:
        assert "--idempotency-key" in call
        assert "--json" in call


def test_mission_apply_fails_when_profiles_are_missing(
    valid_rig: Path, mission_file: Path, capsys
):
    client = MissionHermesClient({"reviewer"}, [], list(_TASK_RESPONSES))

    result = main(
        ["mission", "apply", str(mission_file), "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err.strip() == "error: missing Hermes profiles: builder"
    assert client.command_calls == []


def test_mission_apply_without_json_prints_key_to_id_lines(
    valid_rig: Path, mission_file: Path, capsys
):
    client = MissionHermesClient(
        {"builder", "reviewer"},
        [{"slug": "demo-rig", "archived": False}],
        list(_TASK_RESPONSES),
    )

    result = main(
        ["mission", "apply", str(mission_file), "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.strip() == (
        "implement:implementation -> task-1\n"
        "implement:review -> task-2\n"
        "acceptance -> task-3"
    )
