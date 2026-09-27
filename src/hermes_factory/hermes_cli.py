from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from hermes_factory.errors import HermesCommandError

Runner = Callable[..., subprocess.CompletedProcess[str]]


class HermesClient:
    def __init__(
        self,
        executable: str = "hermes",
        runner: Runner = subprocess.run,
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
                    "select id, idempotency_key, created_by from tasks where id = ?",
                    (task_id,),
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            raise HermesCommandError("failed to read authoritative Hermes task record") from exc
        if row is None:
            raise HermesCommandError(
                f"Hermes task {task_id} is missing from board {board_slug}"
            )
        return dict(row)

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

    def profile_exists(self, profile: str) -> bool:
        args = (
            ["config", "path"]
            if profile == "default"
            else ["-p", profile, "config", "path"]
        )
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
