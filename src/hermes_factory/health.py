from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from hermes_factory.task_templates import (
    contains_audit_contract_markers,
    looks_like_factory_task_body,
    parse_audit_contract,
)

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


def run_succeeded(run: object) -> bool:
    if not isinstance(run, dict):
        return False
    status = run.get("status")
    outcome = run.get("outcome")
    if status not in {None, "done", "succeeded", "completed"}:
        return False
    if outcome not in {None, "success", "completed"}:
        return False
    return status in {"succeeded", "completed"} or outcome in {"success", "completed"}


def _has_successful_run(runs: list[dict[str, Any]]) -> bool:
    return any(run_succeeded(run) for run in runs)


def _latest_successful_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in reversed(runs):
        if run_succeeded(run):
            return run
    return None


def _has_reviewer_summary(
    task_metadata: dict[str, Any],
    runs: list[dict[str, Any]],
    *,
    allow_task_fallback: bool,
) -> bool:
    run = _latest_successful_run(runs)
    if run is not None:
        return bool(run.get("summary"))
    return allow_task_fallback and bool(task_metadata.get("reviewer_summary"))


def _recorded_command_succeeded(recorded: object, required: str) -> bool:
    return (
        isinstance(recorded, dict)
        and recorded.get("command") == required
        and type(recorded.get("exit_code")) is int
        and recorded.get("exit_code") == 0
    )


def _is_command_record_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(record, dict)
        and isinstance(record.get("command"), str)
        and bool(record.get("command"))
        and type(record.get("exit_code")) is int
        for record in value
    )


def _valid_command_records(value: object) -> list[dict[str, Any]]:
    if not _is_command_record_list(value):
        return []
    return value


def _valid_string_list(value: object) -> list[str]:
    if not _is_string_list(value):
        return []
    return value


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and bool(item) for item in value
    )


def _valid_attachment_record(attachment: object) -> bool:
    if not isinstance(attachment, dict):
        return False
    required_keys = {
        "id",
        "filename",
        "content_type",
        "size",
        "uploaded_by",
        "stored_path",
        "created_at",
    }
    if not required_keys.issubset(attachment):
        return False
    filename = attachment.get("filename")
    stored_path = attachment.get("stored_path")
    return (
        type(attachment.get("id")) is int
        and attachment["id"] > 0
        and isinstance(filename, str)
        and bool(filename)
        and "/" not in filename
        and "\\" not in filename
        and not filename.startswith(".")
        and isinstance(stored_path, str)
        and bool(stored_path)
        and (
            PurePosixPath(stored_path.replace("\\", "/")).is_absolute()
            or PureWindowsPath(stored_path).is_absolute()
        )
        and type(attachment.get("size")) is int
        and attachment["size"] >= 0
        and (
            attachment.get("content_type") is None
            or isinstance(attachment.get("content_type"), str)
        )
        and (
            attachment.get("uploaded_by") is None
            or isinstance(attachment.get("uploaded_by"), str)
        )
        and type(attachment.get("created_at")) is int
        and attachment["created_at"] > 0
    )


def _attachment_paths(attachments: object) -> list[str]:
    if not isinstance(attachments, list) or not all(
        _valid_attachment_record(attachment) for attachment in attachments
    ):
        return []
    paths: list[str] = []
    for attachment in attachments:
        paths.append(attachment["filename"])
        stored_path = attachment.get("stored_path")
        if isinstance(stored_path, str) and stored_path:
            paths.append(stored_path)
    return paths


def _recorded_artifact_matches(recorded: object, required: object) -> bool:
    if recorded == required:
        return True
    if not isinstance(recorded, str) or not isinstance(required, str):
        return False

    required_path = PurePosixPath(required.replace("\\", "/"))
    if (
        required_path.is_absolute()
        or PureWindowsPath(required).is_absolute()
        or ".." in required_path.parts
    ):
        return False

    recorded_path = PurePosixPath(recorded.replace("\\", "/"))
    if not (
        recorded_path.is_absolute() or PureWindowsPath(recorded).is_absolute()
    ):
        return False
    return recorded_path.parts[-len(required_path.parts) :] == required_path.parts


def _task_findings(
    task: dict[str, Any],
    runs: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
) -> list[HealthFinding]:
    if task.get("status") != "done":
        return []

    findings: list[HealthFinding] = []
    task_id = task.get("id")
    result = task.get("result")
    task_metadata = _metadata(task)
    body = task.get("body")
    contract = parse_audit_contract(body)
    latest_successful_run = _latest_successful_run(runs)
    evidence = (
        _metadata(latest_successful_run)
        if latest_successful_run is not None
        else {} if contract else dict(task_metadata)
    )

    factory_key = task.get("idempotency_key")
    is_factory_task = (
        task.get("created_by") == "hermes-factory"
        or isinstance(factory_key, str)
        and factory_key.startswith("factory:")
    )
    if (
        is_factory_task
        or contains_audit_contract_markers(body)
        or looks_like_factory_task_body(body)
    ) and not contract:
        findings.append(
            HealthFinding(
                severity="error",
                code="invalid-audit-contract",
                message=(
                    f"task {task_id} has a malformed or schema-invalid "
                    "factory audit contract"
                ),
                task_id=task_id,
            )
        )

    has_successful_run = _has_successful_run(runs)
    if (contract and not has_successful_run) or (
        not contract and not result and not has_successful_run
    ):
        findings.append(
            HealthFinding(
                severity="critical",
                code="done-without-proof",
                message=f"task {task_id} is done without a result or successful run summary",
                task_id=task_id,
            )
        )

    legacy_requirements_invalid = not contract and any(
        (
            key in task_metadata
            and (
                not _is_string_list(task_metadata[key])
                if key in {"required_commands", "required_artifacts"}
                else type(task_metadata[key]) is not bool
            )
        )
        for key in (
            "required_commands",
            "required_artifacts",
            "changed_files_required",
        )
    )
    if legacy_requirements_invalid:
        findings.append(
            HealthFinding(
                severity="error",
                code="invalid-proof-requirements",
                message=f"task {task_id} has malformed legacy proof requirements",
                task_id=task_id,
            )
        )

    if contract:
        changed_files_required = contract["changed_files_required"]
        required_commands = contract["required_commands"]
        required_artifacts = contract["required_artifacts"]
    elif legacy_requirements_invalid:
        changed_files_required = False
        required_commands = []
        required_artifacts = []
    else:
        changed_files_required = task_metadata.get("changed_files_required", False)
        required_commands = task_metadata.get("required_commands", [])
        required_artifacts = task_metadata.get("required_artifacts", [])

    if contract:
        required_metadata = contract.get("required_metadata", [])
        invalid_metadata = [
            field
            for field in required_metadata
            if (
                field == "commands"
                and not _is_command_record_list(evidence.get(field))
            )
            or (
                field in {"changed_files", "limitations"}
                and not _is_string_list(evidence.get(field))
            )
        ]
        if invalid_metadata:
            fields = ", ".join(invalid_metadata)
            findings.append(
                HealthFinding(
                    severity="error",
                    code="invalid-completion-metadata",
                    message=(
                        f"task {task_id} has missing or malformed completion "
                        f"metadata: {fields}"
                    ),
                    task_id=task_id,
                )
            )

    changed_files = _valid_string_list(evidence.get("changed_files"))
    if changed_files_required is True and not changed_files:
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

    recorded_commands = _valid_command_records(evidence.get("commands"))
    for command in required_commands:
        if not any(
            _recorded_command_succeeded(recorded, command)
            for recorded in recorded_commands
        ):
            findings.append(
                HealthFinding(
                    severity="error",
                    code="missing-proof-command",
                    message=f"task {task_id} lacks required proof command {command!r}",
                    task_id=task_id,
                )
            )

    recorded_artifacts = _attachment_paths(attachments)
    for artifact in required_artifacts:
        if not any(
            _recorded_artifact_matches(recorded, artifact)
            for recorded in recorded_artifacts
        ):
            findings.append(
                HealthFinding(
                    severity="error",
                    code="missing-proof-artifact",
                    message=f"task {task_id} lacks required proof artifact {artifact!r}",
                    task_id=task_id,
                )
            )

    task_class = contract.get("task_class", task_metadata.get("task_class"))
    if task_class == "review" and not _has_reviewer_summary(
        task_metadata, runs, allow_task_fallback=not bool(contract)
    ):
        findings.append(
            HealthFinding(
                severity="error" if contract else "warning",
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
    for envelope in diagnostics:
        if not isinstance(envelope, dict):
            continue
        nested = envelope.get("diagnostics")
        if isinstance(nested, list):
            task_id = envelope.get("task_id")
            for diagnostic in nested:
                if not isinstance(diagnostic, dict):
                    continue
                findings.append(
                    HealthFinding(
                        severity=diagnostic.get("severity", "warning"),
                        code=diagnostic.get("kind", "hermes-diagnostic"),
                        message=(
                            diagnostic.get("detail")
                            or diagnostic.get("title")
                            or "Hermes reported a diagnostic"
                        ),
                        task_id=task_id,
                    )
                )
            continue
        findings.append(
            HealthFinding(
                severity=envelope.get("severity", "warning"),
                code=envelope.get("code", "hermes-diagnostic"),
                message=envelope.get("message") or "Hermes reported a diagnostic",
                task_id=envelope.get("task_id"),
            )
        )
    return findings


def build_proof_map(
    tasks: list[dict[str, Any]],
    runs_by_task: dict[str, list[dict[str, Any]]],
    findings: tuple[HealthFinding, ...],
) -> dict[str, bool]:
    failed_task_ids = {
        finding.task_id
        for finding in findings
        if finding.task_id and finding.severity in {"critical", "error"}
    }
    return {
        task_id: (
            bool(task.get("result"))
            or any(run_succeeded(run) for run in runs_by_task.get(task_id, []))
        )
        and task_id not in failed_task_ids
        for task in tasks
        if isinstance(task, dict)
        and task.get("status") == "done"
        and isinstance((task_id := task.get("id")), str)
    }


def audit_tasks(
    tasks: list[dict[str, Any]],
    runs_by_task: dict[str, list[dict[str, Any]]],
    diagnostics: list[dict[str, Any]],
    attachments_by_task: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[HealthFinding, ...]:
    findings: list[HealthFinding] = []
    attachments_by_task = attachments_by_task or {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = task.get("id")
        findings.extend(
            _task_findings(
                task,
                runs_by_task.get(task_id, []),
                attachments_by_task.get(task_id, []),
            )
        )
    findings.extend(_diagnostic_findings(diagnostics))

    def sort_key(finding: HealthFinding) -> tuple[int, str, str]:
        return (
            _SEVERITY_RANK.get(finding.severity, 99),
            finding.task_id or "",
            finding.code,
        )

    return tuple(sorted(findings, key=sort_key))
