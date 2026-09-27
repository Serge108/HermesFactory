from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import shutil

import pytest

from hermes_factory.errors import HermesCommandError
from hermes_factory.hermes_cli import HermesClient


def test_real_hermes_executable_reports_version():
    hermes = shutil.which("hermes")
    if hermes is None:
        pytest.skip("hermes executable not found on PATH")

    output = HermesClient(executable=hermes, timeout_seconds=60).run(["--version"])

    assert "Hermes Agent v" in output


def test_run_json_preserves_argument_boundaries_for_windows_path():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return CompletedProcess(argv, 0, '{"ok": true}', "")

    client = HermesClient(executable="hermes", runner=fake_run)
    result = client.run_json(
        ["kanban", "create", "Task", "--workspace", "dir:F:/Project With Spaces"]
    )

    assert result == {"ok": True}
    assert calls[0][0] == [
        "hermes",
        "kanban",
        "create",
        "Task",
        "--workspace",
        "dir:F:/Project With Spaces",
    ]


def test_task_record_reads_authoritative_fields_from_board_database(tmp_path: Path):
    import sqlite3

    db_path = tmp_path / "Board With Spaces" / "kanban.db"
    db_path.parent.mkdir()
    connection = sqlite3.connect(db_path)
    connection.execute(
        "create table tasks (id text primary key, idempotency_key text, created_by text)"
    )
    connection.execute(
        "insert into tasks (id, idempotency_key, created_by) values (?, ?, ?)",
        (
            "task-1",
            "factory:demo:mission:slice:implementation",
            "hermes-factory",
        ),
    )
    connection.commit()
    connection.close()

    def fake_run(argv, **kwargs):
        payload = [
            {
                "slug": "demo-rig",
                "archived": False,
                "db_path": str(db_path),
            }
        ]
        return CompletedProcess(argv, 0, __import__("json").dumps(payload), "")

    record = HermesClient(runner=fake_run).task_record("demo-rig", "task-1")

    assert record == {
        "id": "task-1",
        "idempotency_key": "factory:demo:mission:slice:implementation",
        "created_by": "hermes-factory",
    }


def test_run_uses_safe_utf8_subprocess_options():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return CompletedProcess(argv, 0, "done", "")

    client = HermesClient(runner=fake_run, timeout_seconds=17)
    result = client.run(["config", "path"], cwd=Path("F:/Project With Spaces"))

    assert result == "done"
    assert calls[0][1] == {
        "cwd": "F:\\Project With Spaces",
        "text": True,
        "encoding": "utf-8",
        "capture_output": True,
        "timeout": 17,
        "shell": False,
        "check": False,
    }


@pytest.mark.parametrize(
    ("stdout", "stderr", "detail"),
    [
        ("", "profile missing", "profile missing"),
        ("profile missing", "", "profile missing"),
        ("", "", "no output"),
    ],
)
def test_run_reports_available_output_on_nonzero_exit(stdout, stderr, detail):
    def fake_run(argv, **kwargs):
        return CompletedProcess(argv, 2, stdout, stderr)

    with pytest.raises(HermesCommandError, match=rf"Hermes exited 2: {detail}") as exc_info:
        HermesClient(runner=fake_run).run(["kanban", "stats", "--json"])

    assert exc_info.value.returncode == 2
    assert exc_info.value.stdout == stdout
    assert exc_info.value.stderr == stderr


def test_run_json_wraps_malformed_json_without_echoing_output():
    secret_output = "not-json secret-token"

    def fake_run(argv, **kwargs):
        return CompletedProcess(argv, 0, secret_output, "")

    with pytest.raises(HermesCommandError, match="Hermes returned invalid JSON") as exc_info:
        HermesClient(runner=fake_run).run_json(["kanban", "stats", "--json"])

    assert secret_output not in str(exc_info.value)


def test_run_wraps_timeout_without_echoing_arguments():
    secret = "secret-token"

    def fake_run(argv, **kwargs):
        raise TimeoutExpired([*argv, "--token", secret], kwargs["timeout"])

    with pytest.raises(HermesCommandError, match="timed out after 17 seconds") as exc_info:
        HermesClient(runner=fake_run, timeout_seconds=17).run(["config", "path"])

    assert secret not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, TimeoutExpired)


def test_run_wraps_os_error():
    def fake_run(argv, **kwargs):
        raise OSError("executable not found")

    with pytest.raises(HermesCommandError, match="failed to run Hermes") as exc_info:
        HermesClient(runner=fake_run).run(["config", "path"])

    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.parametrize(
    ("profile", "expected_argv"),
    [
        ("default", ["hermes", "config", "path"]),
        ("Builder Profile", ["hermes", "-p", "Builder Profile", "config", "path"]),
    ],
)
def test_profile_exists_uses_profile_specific_config_path(profile, expected_argv):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, "F:/config.yaml", "")

    assert HermesClient(runner=fake_run).profile_exists(profile) is True
    assert calls == [expected_argv]


def test_profile_exists_returns_false_for_confirmed_missing_profile():
    def fake_run(argv, **kwargs):
        return CompletedProcess(
            argv,
            1,
            "",
            "Error: Profile 'missing' does not exist. "
            "Create it with: hermes profile create missing\n",
        )

    assert HermesClient(runner=fake_run).profile_exists("missing") is False


@pytest.mark.parametrize(
    ("returncode", "stderr"),
    [
        (2, "Error: Profile 'bad name' is not a valid profile name"),
        (1, "database unavailable"),
    ],
)
def test_profile_exists_propagates_non_missing_command_errors(returncode, stderr):
    def fake_run(argv, **kwargs):
        return CompletedProcess(argv, returncode, "", stderr)

    with pytest.raises(HermesCommandError):
        HermesClient(runner=fake_run).profile_exists("bad name")


def test_profile_exists_propagates_timeout_as_hermes_command_error():
    def fake_run(argv, **kwargs):
        raise TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(HermesCommandError, match="timed out") as exc_info:
        HermesClient(runner=fake_run).profile_exists("builder")

    assert isinstance(exc_info.value.__cause__, TimeoutExpired)


def test_profile_exists_propagates_os_error_as_hermes_command_error():
    def fake_run(argv, **kwargs):
        raise OSError("executable not found")

    with pytest.raises(HermesCommandError, match="failed to run Hermes") as exc_info:
        HermesClient(runner=fake_run).profile_exists("builder")

    assert isinstance(exc_info.value.__cause__, OSError)
