from __future__ import annotations

import json
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
