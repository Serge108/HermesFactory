import json
import subprocess
from pathlib import Path

import pytest

from hermes_factory import cli as cli_module
from hermes_factory.cli import main
from hermes_factory.errors import HermesCommandError
from hermes_factory.metrics import compute_metrics
from hermes_factory.reconcile import ReconcileResult


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


_MULTI_SLICE_MISSION = """\
version: 1
id: demo-mission
title: Demo mission
intent: Ship one verified behavior without expanding scope.
workflow: delivery
human_acceptance: true
acceptance:
  - The requested behavior exists.
slices:
  - id: core
    title: Implement the core behavior
    goal: Make the smallest code change satisfying the mission.
    depends_on: []
    acceptance:
      - A failing test was observed before implementation.
    proof:
      commands:
        - uv run pytest -q
      artifacts: []
      changed_files_required: true
  - id: polish
    title: Polish the behavior
    goal: Remove rough edges revealed by review.
    depends_on:
      - core
    acceptance:
      - Review findings are resolved.
    proof:
      commands:
        - uv run pytest -q
      artifacts: []
      changed_files_required: true
"""


def test_mission_plan_compiles_dependent_slices_end_to_end(
    valid_rig: Path, tmp_path: Path, capsys
):
    valid_rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "completion_contract: local-only}",
            "completion_contract: local-only, decision_authority: human}",
        ),
        encoding="utf-8",
    )
    mission_path = tmp_path / "mission.yaml"
    mission_path.write_text(_MULTI_SLICE_MISSION, encoding="utf-8")
    hermes_calls: list[list[str]] = []

    class RecordingClient:
        def run_json(self, args: list[str]) -> object:
            hermes_calls.append(args)
            return {}

        def run(self, args: list[str]) -> str:
            hermes_calls.append(args)
            return ""

    def record_only(executable: str):
        return RecordingClient()

    result = main(
        [
            "mission",
            "plan",
            str(mission_path),
            "--rig",
            str(valid_rig),
            "--json",
        ],
        client_factory=record_only,
    )

    captured = capsys.readouterr()
    assert result == 0
    payload = json.loads(captured.out)
    assert [task["key"] for task in payload["tasks"]] == [
        "core:implementation",
        "core:review",
        "polish:implementation",
        "polish:review",
        "acceptance",
    ]
    assert payload["tasks"][2]["parents"] == ["core:review"]
    assert payload["tasks"][4]["parents"] == ["core:review", "polish:review"]
    assert payload["tasks"][4]["initial_status"] == "blocked"
    assert payload["tasks"][0]["initial_status"] is None
    assert hermes_calls == []


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


_STATUS_TASKS = [
    {
        "id": "t-1",
        "title": "Implement demo",
        "status": "done",
        "result": "tests pass",
        "metadata": {"task_class": "implementation"},
    },
    {
        "id": "t-2",
        "title": "Review demo",
        "status": "done",
        "result": "approved",
        "metadata": {"task_class": "review", "reviewer_summary": "verified"},
    },
    {
        "id": "t-3",
        "title": "Accept demo",
        "status": "blocked",
        "result": None,
        "metadata": {},
    },
]
_STATUS_DIAGNOSTICS = [
    {
        "task_id": "t-3",
        "title": "Accept demo",
        "status": "blocked",
        "assignee": None,
        "diagnostics": [
            {
                "severity": "warning",
                "kind": "stale-claim",
                "title": "Claim is stale",
                "detail": "claim is stale",
            }
        ],
    },
    {"task_id": None, "dispatch_profiles": "any", "diagnostics": []},
]


class StatusHermesClient:
    def __init__(
        self,
        profiles: set[str],
        stats: object,
        tasks: object,
        diagnostics: object,
        runs: dict[str, object],
        attachments: dict[str, object] | None = None,
    ) -> None:
        self.profiles = profiles
        self.stats = stats
        self.tasks = tasks
        self.diagnostics = diagnostics
        self.runs = runs
        self.attachments = attachments or {}
        self.command_calls: list[list[str]] = []

    def profile_exists(self, profile: str) -> bool:
        return profile in self.profiles

    def run_json(self, args: list[str]) -> object:
        self.command_calls.append(args)
        subcommand = args[3] if len(args) > 3 else None
        if subcommand == "stats":
            return self.stats
        if subcommand == "list":
            return self.tasks
        if subcommand == "diagnostics":
            return self.diagnostics
        if subcommand == "runs":
            return self.runs[args[4]]
        if subcommand == "attachments":
            return self.attachments.get(args[4], [])
        raise AssertionError(f"unexpected command: {args}")

    def run(self, args: list[str]) -> str:
        self.command_calls.append(args)
        return ""


def test_status_clean_board_exits_zero_and_prints_counts(
    valid_rig: Path, capsys
):
    client = StatusHermesClient(
        {"builder", "reviewer"},
        {"by_status": {"done": 2, "blocked": 1}},
        list(_STATUS_TASKS),
        list(_STATUS_DIAGNOSTICS),
        {"t-1": [{"status": "succeeded"}], "t-2": []},
    )

    result = main(
        ["status", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out.splitlines()[0] == "findings: 0 critical, 0 error, 1 warning"
    assert captured.out.splitlines()[1] == (
        "warning: stale-claim: claim is stale (t-3)"
    )
    assert captured.err == ""
    assert [call[3] for call in client.command_calls] == [
        "stats",
        "list",
        "diagnostics",
        "runs",
        "attachments",
        "runs",
        "attachments",
    ]
    assert all(call[:3] == ["kanban", "--board", "demo-rig"] for call in client.command_calls)


def test_status_json_contains_stats_and_findings(valid_rig: Path, capsys):
    client = StatusHermesClient(
        {"builder", "reviewer"},
        {"by_status": {"done": 1}, "by_assignee": {}},
        [
            {
                "id": "t-1",
                "title": "Implement demo",
                "status": "done",
                "result": None,
                "metadata": {"changed_files_required": True},
            }
        ],
        [],
        {"t-1": []},
    )

    result = main(
        ["status", "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 1
    payload = json.loads(captured.out)
    assert payload["stats"]["by_status"] == {"done": 1}
    assert payload["findings"] == [
        {
            "severity": "critical",
            "code": "done-without-proof",
            "message": "task t-1 is done without a result or successful run summary",
            "task_id": "t-1",
        },
        {
            "severity": "error",
            "code": "missing-changed-files",
            "message": "task t-1 requires changed files but metadata.changed_files is absent",
            "task_id": "t-1",
        },
    ]


def test_status_fetches_runs_only_for_done_or_review_tasks(
    valid_rig: Path, capsys
):
    client = StatusHermesClient(
        {"builder", "reviewer"},
        {"by_status": {}},
        list(_STATUS_TASKS),
        [],
        {"t-1": [], "t-2": [], "t-3": []},
    )

    result = main(
        ["status", "--rig", str(valid_rig)],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    run_calls = [call for call in client.command_calls if call[3] == "runs"]
    attachment_calls = [
        call for call in client.command_calls if call[3] == "attachments"
    ]
    assert [call[4] for call in run_calls] == ["t-1", "t-2"]
    assert [call[4] for call in attachment_calls] == ["t-1", "t-2"]


def test_status_accepts_commit_only_when_git_confirms_project_commit(
    valid_rig: Path, capsys
):
    project = valid_rig.parent / "project"
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    subprocess.run(
        ["git", "config", "user.email", "factory@example.invalid"],
        cwd=project,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Factory Test"], cwd=project, check=True
    )
    tracked = project / "tracked.txt"
    tracked.write_text("verified\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=project, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "verified"], cwd=project, check=True)
    commit_id = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    task = {
        "id": "t-commit",
        "title": "Commit demo",
        "status": "done",
        "result": "committed",
        "created_by": "hermes-factory",
        "body": (
            "<!-- hermes-factory:audit-contract -->\n"
            'Factory audit contract: {"task_class":"commit",'
            '"required_commands":[],"required_artifacts":[],'
            '"changed_files_required":false,'
            '"required_metadata":["commit","decision_log","limitations"]}\n'
            "<!-- /hermes-factory:audit-contract -->"
        ),
        "metadata": {},
    }
    run = {
        "status": "done",
        "outcome": "completed",
        "summary": "commit verified",
        "metadata": {"commit": commit_id, "decision_log": [], "limitations": []},
    }
    client = StatusHermesClient(
        {"builder", "reviewer"},
        {"by_status": {"done": 1}},
        [task],
        [],
        {"t-commit": [run]},
    )

    result = main(
        ["status", "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: client,
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["findings"] == []


_REPORT_TASKS = [
    {
        "id": "t-1",
        "status": "done",
        "assignee": "builder",
        "created_at": 0,
        "completed_at": 3_600,
        "result": "tests pass",
        "metadata": {},
    },
    {
        "id": "t-2",
        "status": "ready",
        "assignee": "builder",
        "created_at": 100,
        "completed_at": None,
        "result": None,
        "metadata": {},
    },
]
_REPORT_RUNS = {
    "t-1": [
        {
            "status": "review",
            "outcome": "review_requested",
        },
        {
            "status": "done",
            "outcome": "changes_requested",
        },
    ]
}
_REPORT_STATS = {"by_status": {"done": 1, "ready": 1}, "now": 86_400}


class ReportHermesClient:
    def __init__(
        self,
        stats: object,
        tasks: object,
        runs: dict[str, object],
        attachments: dict[str, object] | None = None,
        diagnostics: object | None = None,
    ) -> None:
        self.stats = stats
        self.tasks = tasks
        self.runs = runs
        self.attachments = attachments or {}
        self.diagnostics = diagnostics if diagnostics is not None else []
        self.command_calls: list[list[str]] = []

    def run_json(self, args: list[str]) -> object:
        self.command_calls.append(args)
        subcommand = args[3] if len(args) > 3 else None
        if subcommand == "stats":
            return self.stats
        if subcommand == "list":
            return self.tasks
        if subcommand == "diagnostics":
            return self.diagnostics
        if subcommand == "runs":
            return self.runs.get(args[4], [])
        if subcommand == "attachments":
            return self.attachments.get(args[4], [])
        raise AssertionError(f"unexpected command: {args}")


def test_report_json_returns_metrics_and_never_fails(
    valid_rig: Path, capsys
):
    client = ReportHermesClient(_REPORT_STATS, list(_REPORT_TASKS), dict(_REPORT_RUNS))

    result = main(
        ["report", "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: client,
    )

    captured = capsys.readouterr()
    assert result == 0
    payload = json.loads(captured.out)
    expected = compute_metrics(list(_REPORT_TASKS), dict(_REPORT_RUNS), _REPORT_STATS)
    from dataclasses import asdict

    assert payload["metrics"] == asdict(expected)
    assert payload["metrics"]["rework_rate"] == 1.0
    assert payload["metrics"]["changes_requested"] == 1
    assert payload["metrics"]["open_work_by_seat"] == {"builder": 1}
    assert payload["metrics"]["median_lead_time_minutes"] == 60.0
    attachment_calls = [
        call for call in client.command_calls if call[3] == "attachments"
    ]
    assert [call[4] for call in attachment_calls] == ["t-1"]


def test_report_proof_rate_requires_authoritative_artifact(valid_rig: Path, capsys):
    body = (
        "<!-- hermes-factory:audit-contract -->\n"
        'Factory audit contract: {"task_class":"implementation",'
        '"required_commands":[],"required_artifacts":["report.txt"],'
        '"changed_files_required":false}\n'
        "<!-- /hermes-factory:audit-contract -->"
    )
    tasks = [
        {
            "id": "t-1",
            "status": "done",
            "result": None,
            "created_by": "hermes-factory",
            "body": body,
            "metadata": {},
        }
    ]
    runs = {"t-1": [{"status": "done", "outcome": "completed"}]}
    client = ReportHermesClient(
        {"by_status": {"done": 1}, "now": 100},
        tasks,
        runs,
        attachments={"t-1": []},
    )

    result = main(
        ["report", "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: client,
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["proof_rate"] == 0.0


def test_report_proof_rate_includes_hermes_diagnostics(valid_rig: Path, capsys):
    tasks = [
        {
            "id": "t-1",
            "status": "done",
            "result": "tests pass",
            "metadata": {},
        }
    ]
    client = ReportHermesClient(
        {"by_status": {"done": 1}, "now": 100},
        tasks,
        {"t-1": []},
        diagnostics=[
            {
                "task_id": "t-1",
                "title": "Implement demo",
                "status": "done",
                "assignee": "builder",
                "diagnostics": [
                    {
                        "severity": "error",
                        "kind": "broken-dependency",
                        "title": "Dependency is inconsistent",
                        "detail": "dependency state is inconsistent",
                    }
                ],
            },
            {"task_id": None, "dispatch_profiles": "any", "diagnostics": []},
        ],
    )

    result = main(
        ["report", "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: client,
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["proof_rate"] == 0.0
    assert any(call[3] == "diagnostics" for call in client.command_calls)


def test_report_text_prints_operational_lines(valid_rig: Path, capsys):
    client = ReportHermesClient(_REPORT_STATS, list(_REPORT_TASKS), dict(_REPORT_RUNS))

    result = main(["report", "--rig", str(valid_rig)], client_factory=lambda executable: client)

    captured = capsys.readouterr()
    assert result == 0
    lines = captured.out.splitlines()
    assert lines[0].startswith("done: 1")
    assert any("rework_rate=1.0" in line for line in lines)
    assert any("open work" in line and "builder: 1" in line for line in lines)


def test_custom_dashboard_command_is_not_part_of_factory_cli():
    from hermes_factory.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["dashboard", "--rig", str(Path("rig.yaml")), "--port", "8400"]
        )


def test_stall_taxonomy_json_exposes_stable_reason_codes(capsys):
    result = main(["stall-taxonomy", "--json"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    classes = {item["reason_code"]: item for item in payload["stall_classes"]}
    assert "normal_dependency_wait" in classes
    assert "awaiting_dispatch" in classes
    assert "stale_runner" in classes
    assert "stale_dependency_edge" in classes
    assert "human_gate" in classes
    assert "corrupt_state" in classes
    assert classes[
        "recovery_coordinator_insufficient_kanban_authority"
    ]["recovery_owner"] == "factory"


def test_report_fetches_runs_for_all_tasks(valid_rig: Path, capsys):
    runs = dict(_REPORT_RUNS)
    runs["t-2"] = [{"status": "failed", "outcome": "crashed"}]
    client = ReportHermesClient(_REPORT_STATS, list(_REPORT_TASKS), runs)

    result = main(
        ["report", "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: client,
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    run_calls = [call for call in client.command_calls if call[3] == "runs"]
    assert [call[4] for call in run_calls] == ["t-1", "t-2"]
    assert payload["metrics"]["total_runs"] == 3
    assert payload["metrics"]["failed_runs"] == 1


def test_reconcile_json_reports_created_recovery_task(valid_rig: Path, capsys):
    workspace_path = (valid_rig.parent / "project").as_posix()
    blocked = {
        "id": "task-1",
        "title": "Implement large behavior",
        "body": (
            "# Project\n"
            f"Demo (slug: demo)\nWorking directory: {workspace_path}\n\n"
            "# Mission\nDemo mission.\n\n"
            "# Slice\nSlice id: implement\nImplement large behavior.\n\n"
            "# Acceptance\n- behavior works\n\n"
            "# Proof required\nCommands that must be recorded:\n- uv run pytest\n\n"
            "# Scope boundaries\n"
            "Project demo; mission demo-mission; workflow delivery.\n"
            "Assigned seat: builder; role: implementation.\n"
            "Failure limit: 2 consecutive non-successful runs; bounded runtime per task: 30m.\n"
            "Completion contract: local-only.\n"
            "<!-- hermes-factory:audit-contract -->\n"
            'Factory audit contract: {"task_class":"implementation",'
            '"required_commands":[],"required_artifacts":[],'
            '"changed_files_required":false,'
            '"required_metadata":["changed_files","commands","limitations"]}\n'
            "<!-- /hermes-factory:audit-contract -->\n\n"
            "# Handoff\nNext target: review."
        ),
        "status": "blocked",
        "assignee": "builder",
        "created_by": "hermes-factory",
        "idempotency_key": "factory:demo:demo-mission:implement:implementation",
        "workspace_kind": "dir",
        "workspace_path": workspace_path,
        "max_retries": 2,
        "max_runtime_seconds": 1800,
        "completion_contract": "local-only",
        "skills": [],
        "last_failure_error": "elapsed 11s > limit 10s",
    }
    responses = [
        [blocked],
        {
            "task": blocked,
            "parents": [],
            "children": ["review-1"],
            "events": [
                {
                    "kind": "gave_up",
                    "created_at": 102,
                    "payload": {
                        "trigger_outcome": "timed_out",
                        "error": "elapsed 11s > limit 10s",
                    },
                }
            ],
            "runs": [],
        },
        {
            "id": "recovery-1",
            "status": "ready",
            "title": "Decompose blocked task task-1",
        },
        [],
    ]

    class ReconcileClient:
        def run_json(self, args: list[str]) -> object:
            return responses.pop(0)

        def run(self, args: list[str]) -> str:
            return ""

    result = main(
        ["reconcile", "--rig", str(valid_rig), "--json"],
        client_factory=lambda executable: ReconcileClient(),
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "scanned": 1,
        "eligible": 1,
        "recovery_task_ids": {"task-1": "recovery-1"},
        "provider_reopened_task_ids": [],
        "finalized_recovery_task_ids": [],
        "board_incident_task_id": None,
        "dispatched": 0,
    }


def test_reconcile_forwards_explicit_fixture_recovery_options(
    valid_rig: Path, monkeypatch, capsys
):
    captured = {}

    def fake_reconcile(client, rig, **kwargs):
        captured.update(kwargs)
        return ReconcileResult(0, 0, {}, (), 0)

    monkeypatch.setattr(cli_module, "reconcile_board", fake_reconcile)

    result = main(
        [
            "reconcile",
            "--rig",
            str(valid_rig),
            "--fixture-recovery",
            "--recovery-runtime",
            "60s",
            "--json",
        ],
        client_factory=lambda executable: object(),
    )

    assert result == 0
    assert captured == {
        "dispatch": False,
        "fixture_recovery": True,
        "recovery_runtime": "60s",
    }
    assert json.loads(capsys.readouterr().out)["eligible"] == 0
