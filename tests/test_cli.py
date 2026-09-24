from pathlib import Path

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
