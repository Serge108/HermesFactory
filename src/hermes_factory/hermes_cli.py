from __future__ import annotations

import contextlib
import json
import locale
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, IO

from hermes_factory.errors import HermesCommandError
from hermes_factory.operator import (
    CronJob,
    CronState,
    parse_cron_executions,
    parse_cron_incidents,
    parse_cron_jobs,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _read_sink(sink: IO[bytes] | None, *, text: bool, encoding: str | None) -> Any:
    if sink is None:
        return None
    sink.seek(0)
    payload = sink.read()
    if not text:
        return payload
    return payload.decode(encoding or locale.getpreferredencoding(False))


def bounded_run(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    text: bool = False,
    encoding: str | None = None,
    capture_output: bool = False,
    timeout: float | None = None,
    shell: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Run a command under a deadline that its own descendants cannot extend.

    ``subprocess.run`` cannot enforce that deadline for a Hermes command. Every
    Hermes command is a launcher for a longer-lived runtime process, so a
    descendant inherits the output pipes and may outlive the child. On Windows
    ``subprocess.run`` waits by joining the pipe reader threads, and after a
    timeout it calls ``communicate()`` a second time with no deadline at all, so
    such a descendant turns the deadline into an unbounded hang. Capturing into
    temporary files instead of pipes makes the deadline a property of the child
    process rather than of end-of-file on a pipe, and a killed child leaves no
    reader behind. The standard input is not inherited either: an unattended
    command must never block on a terminal it does not have.
    """
    argv = list(argv)
    with contextlib.ExitStack() as stack:
        if capture_output:
            out_sink: IO[bytes] | None = stack.enter_context(tempfile.TemporaryFile("w+b"))
            err_sink: IO[bytes] | None = stack.enter_context(tempfile.TemporaryFile("w+b"))
        else:
            out_sink = err_sink = None
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=out_sink,
            stderr=err_sink,
            shell=shell,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        stdout = _read_sink(out_sink, text=text, encoding=encoding)
        stderr = _read_sink(err_sink, text=text, encoding=encoding)
        if check and process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, argv, stdout, stderr)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


class HermesClient:
    def __init__(
        self,
        executable: str = "hermes",
        runner: Runner = bounded_run,
        timeout_seconds: int = 30,
    ) -> None:
        self.executable = executable
        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def run(self, args: Sequence[str], *, cwd: Path | None = None) -> str:
        try:
            completed = self.runner(
                [self.executable, *args],
                cwd=str(cwd) if cwd else None,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HermesCommandError(
                f"Hermes command timed out after {self.timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise HermesCommandError("failed to run Hermes") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
            raise HermesCommandError(
                f"Hermes exited {completed.returncode}: {detail}",
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        return completed.stdout

    def run_json(self, args: Sequence[str], *, cwd: Path | None = None) -> Any:
        output = self.run(args, cwd=cwd)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise HermesCommandError("Hermes returned invalid JSON") from exc

    def _board_database_path(self, board_slug: str) -> Path:
        boards = self.run_json(["kanban", "boards", "list", "--json", "--all"])
        if not isinstance(boards, list) or any(
            not isinstance(board, dict) for board in boards
        ):
            raise HermesCommandError("Hermes returned malformed board list JSON")
        matches = [
            board
            for board in boards
            if board.get("slug") == board_slug and board.get("archived") is False
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("db_path"), str):
            raise HermesCommandError(
                f"Hermes board {board_slug} has no authoritative database path"
            )
        return Path(matches[0]["db_path"])

    def task_record(self, board_slug: str, task_id: str) -> dict[str, Any]:
        """Read fields omitted by Hermes JSON from the authoritative board database."""
        database_path = self._board_database_path(board_slug)
        try:
            uri = database_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "select id, title, body, assignee, status, created_by, "
                    "workspace_kind, workspace_path, idempotency_key, "
                    "max_runtime_seconds, skills, max_retries, completion_contract "
                    "from tasks where id = ?",
                    (task_id,),
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            raise HermesCommandError("failed to read authoritative Hermes task record") from exc
        if row is None:
            raise HermesCommandError(
                f"Hermes task {task_id} is missing from board {board_slug}"
            )
        record = dict(row)
        raw_skills = record.get("skills")
        if raw_skills is None:
            record["skills"] = []
        else:
            try:
                skills = json.loads(raw_skills)
            except (TypeError, json.JSONDecodeError) as exc:
                raise HermesCommandError("Hermes task has malformed authoritative skills") from exc
            if not isinstance(skills, list) or any(not isinstance(skill, str) for skill in skills):
                raise HermesCommandError("Hermes task has malformed authoritative skills")
            record["skills"] = skills
        return record

    def board_activity(
        self,
        board_slug: str,
        *,
        observed_at: int | None = None,
        excluded_creators: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Read active tasks and the latest non-excluded lifecycle event read-only."""
        database_path = self._board_database_path(board_slug)
        exclusion = ""
        params: tuple[str, ...] = ()
        if excluded_creators:
            placeholders = ", ".join("?" for _ in excluded_creators)
            exclusion = f" and (created_by is null or created_by not in ({placeholders}))"
            params = excluded_creators
        try:
            uri = database_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("begin")
                tasks = connection.execute(
                    "select id, status, created_by from tasks "
                    "where status not in ('done', 'archived') order by id"
                ).fetchall()
                event_row = connection.execute(
                    "select max(e.created_at) as latest "
                    "from task_events e join tasks t on t.id = e.task_id "
                    "where t.status != 'archived'" + exclusion,
                    params,
                ).fetchone()
                latest = event_row["latest"] if event_row is not None else None
                if latest is None:
                    fallback = connection.execute(
                        "select max(max(coalesce(created_at, 0), "
                        "coalesce(started_at, 0), coalesce(completed_at, 0))) as latest "
                        "from tasks where status != 'archived'" + exclusion,
                        params,
                    ).fetchone()
                    latest = fallback["latest"] if fallback is not None else None
                latest_incident_terminal = None
                if excluded_creators:
                    placeholders = ", ".join("?" for _ in excluded_creators)
                    terminal_row = connection.execute(
                        "select id, status, coalesce(completed_at, created_at) as terminal_at "
                        "from tasks where created_by in ("
                        + placeholders
                        + ") and status in ('done', 'archived') "
                        "order by terminal_at desc, id desc limit 1",
                        excluded_creators,
                    ).fetchone()
                    if terminal_row is not None:
                        latest_incident_terminal = dict(terminal_row)
        except (OSError, sqlite3.Error) as exc:
            raise HermesCommandError("failed to read authoritative Hermes board activity") from exc
        result = {
            "observed_at": int(time.time()) if observed_at is None else observed_at,
            "last_activity_at": latest,
            "tasks": [dict(row) for row in tasks],
        }
        if latest_incident_terminal is not None:
            result["latest_incident_terminal"] = latest_incident_terminal
        return result

    def _config_path_args(self, profile: str) -> list[str]:
        """Name the profile explicitly, whatever the ambient environment says.

        ``hermes config path`` without ``-p`` resolves ``HERMES_HOME``, so a session
        launched with ``-p <other profile>`` inherits that profile and reads its cron
        directory: the supervisor then sees no watchdog job for its own project and
        reports ``unregistered`` instead of the job list. ``-p default`` was measured
        to outrank ``HERMES_HOME`` on the installed CLI, so every profile is named.
        """
        return ["-p", profile, "config", "path"]

    def cron_directory(self, profile: str = "default") -> Path:
        """Resolve the profile's cron directory from its own configuration path."""
        output = self.run(self._config_path_args(profile)).strip()
        if not output:
            raise HermesCommandError("Hermes returned no profile configuration path")
        directory = Path(output).parent / "cron"
        if not directory.is_dir():
            raise HermesCommandError(f"Hermes profile {profile} has no cron directory")
        return directory

    def _cron_incident_rows(self, database_path: Path) -> tuple[dict[str, Any], ...]:
        if not database_path.exists():
            return ()
        try:
            uri = database_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "select id, job_id, state, failure_type, first_seen_at, last_seen_at, "
                    "error, output_file from cron_incidents order by last_seen_at desc"
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return ()
            raise HermesCommandError("failed to read Hermes cron incidents") from exc
        except (OSError, sqlite3.Error) as exc:
            raise HermesCommandError("failed to read Hermes cron incidents") from exc
        return tuple(dict(row) for row in rows)

    def _cron_execution_rows(self, database_path: Path) -> tuple[dict[str, Any], ...]:
        if not database_path.exists():
            return ()
        try:
            uri = database_path.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "select id, job_id, status, started_at, finished_at from executions "
                    "order by rowid desc limit 200"
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return ()
            raise HermesCommandError("failed to read Hermes cron executions") from exc
        except (OSError, sqlite3.Error) as exc:
            raise HermesCommandError("failed to read Hermes cron executions") from exc
        return tuple(dict(row) for row in rows)

    def cron_state(self, profile: str = "default") -> CronState:
        """Read the durable cron evidence that survives a broken Factory engine."""
        directory = self.cron_directory(profile)
        jobs_path = directory / "jobs.json"
        jobs: tuple[CronJob, ...] = ()
        if jobs_path.exists():
            try:
                payload = json.loads(jobs_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise HermesCommandError(
                    f"Hermes cron job list {jobs_path} is unreadable"
                ) from exc
            jobs = parse_cron_jobs(payload)
        return CronState(
            jobs=jobs,
            incidents=parse_cron_incidents(
                self._cron_incident_rows(directory / "executions.db")
            ),
            executions=parse_cron_executions(
                self._cron_execution_rows(directory / "executions.db")
            ),
            cron_directory=directory,
        )

    def profile_exists(self, profile: str) -> bool:
        args = self._config_path_args(profile)
        try:
            self.run(args)
        except HermesCommandError as exc:
            missing_message = (
                f"Error: Profile '{profile}' does not exist. "
                f"Create it with: hermes profile create {profile}"
            )
            if exc.returncode == 1 and (exc.stderr or "").strip() == missing_message:
                return False
            raise
        return True
