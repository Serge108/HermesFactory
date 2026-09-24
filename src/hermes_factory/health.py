from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_SEVERITY_RANK = {"critical": 0, "error": 1, "warning": 2}


@dataclass(frozen=True, slots=True)
class HealthFinding:
    severity: str
    code: str
    message: str
    task_id: str | None = None


def _metadata(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("metadata")
    return value if isinstance(value, dict) else {}


def _has_successful_run(runs: list[dict[str, Any]]) -> bool:
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("status") == "succeeded" or run.get("outcome") == "success":
            return True
    return False


def _task_findings(
    task: dict[str, Any], runs: list[dict[str, Any]]
) -> list[HealthFinding]:
    if task.get("status") != "done":
        return []

    findings: list[HealthFinding] = []
    task_id = task.get("id")
    result = task.get("result")
    metadata = _metadata(task)

    if not result and not _has_successful_run(runs):
        findings.append(
            HealthFinding(
                severity="critical",
                code="done-without-proof",
                message=f"task {task_id} is done without a result or successful run summary",
                task_id=task_id,
            )
        )

    if metadata.get("changed_files_required") is True and not metadata.get("changed_files"):
        findings.append(
            HealthFinding(
                severity="error",
                code="missing-changed-files",
                message=(
                    f"task {task_id} requires changed files but "
                    "metadata.changed_files is absent"
                ),
                task_id=task_id,
            )
        )

    recorded = metadata.get("commands") or []
    for command in metadata.get("required_commands") or []:
        if command not in recorded:
            findings.append(
                HealthFinding(
                    severity="error",
                    code="missing-proof-command",
                    message=f"task {task_id} lacks required proof command {command!r}",
                    task_id=task_id,
                )
            )

    if (
        metadata.get("task_class") == "review"
        and not metadata.get("reviewer_summary")
    ):
        findings.append(
            HealthFinding(
                severity="warning",
                code="review-without-summary",
                message=f"review task {task_id} has no independent reviewer summary",
                task_id=task_id,
            )
        )

    return findings


def _diagnostic_findings(
    diagnostics: list[dict[str, Any]],
) -> list[HealthFinding]:
    findings: list[HealthFinding] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        findings.append(
            HealthFinding(
                severity=diagnostic.get("severity", "warning"),
                code=diagnostic.get("code", "hermes-diagnostic"),
                message=diagnostic.get("message") or "Hermes reported a diagnostic",
                task_id=diagnostic.get("task_id"),
            )
        )
    return findings


def audit_tasks(
    tasks: list[dict[str, Any]],
    runs_by_task: dict[str, list[dict[str, Any]]],
    diagnostics: list[dict[str, Any]],
) -> tuple[HealthFinding, ...]:
    findings: list[HealthFinding] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        findings.extend(_task_findings(task, runs_by_task.get(task.get("id"), [])))
    findings.extend(_diagnostic_findings(diagnostics))

    def sort_key(finding: HealthFinding) -> tuple[int, str, str]:
        return (
            _SEVERITY_RANK.get(finding.severity, 99),
            finding.task_id or "",
            finding.code,
        )

    return tuple(sorted(findings, key=sort_key))
