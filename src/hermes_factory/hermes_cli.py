from __future__ import annotations

import json
import sqlite3
import subprocess
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

    def task_record(self, board_slug: str, task_id: str) -> dict[str, Any]:
        """Read fields omitted by Hermes JSON from the authoritative board database."""
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
        database_path = Path(matches[0]["db_path"])
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
