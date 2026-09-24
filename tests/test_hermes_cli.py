from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest

from hermes_factory.errors import HermesCommandError
from hermes_factory.hermes_cli import HermesClient


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

    with pytest.raises(HermesCommandError, match=rf"Hermes exited 2: {detail}"):
        HermesClient(runner=fake_run).run(["kanban", "stats", "--json"])


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


def test_profile_exists_returns_false_when_config_path_fails():
    def fake_run(argv, **kwargs):
        return CompletedProcess(argv, 2, "", "profile missing")

    assert HermesClient(runner=fake_run).profile_exists("missing") is False
