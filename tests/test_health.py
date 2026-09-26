import subprocess

import pytest

from hermes_factory.health import HealthFinding, audit_tasks, verify_git_commit


def _done_task(task_id: str, result: object = None, metadata: dict | None = None) -> dict:
    return {
        "id": task_id,
        "title": "Implement demo",
        "status": "done",
        "result": result,
        "metadata": metadata or {},
    }


def _contract_body(payload: str) -> str:
    return (
        "<!-- hermes-factory:audit-contract -->\n"
        f"Factory audit contract: {payload}\n"
        "<!-- /hermes-factory:audit-contract -->"
    )


def test_done_task_without_result_or_successful_run_is_critical():
    findings = audit_tasks(
        tasks=[_done_task("t-1", result=None)],
        runs_by_task={},
        diagnostics=[],
    )

    assert findings == (
        HealthFinding(
            severity="critical",
            code="done-without-proof",
            message="task t-1 is done without a result or successful run summary",
            task_id="t-1",
        ),
    )


def test_done_task_with_result_is_clean():
    findings = audit_tasks(
        tasks=[_done_task("t-1", result="tests pass")],
        runs_by_task={},
        diagnostics=[],
    )

    assert findings == ()


def test_done_task_with_successful_run_is_clean():
    task = _done_task("t-1", result=None)
    findings = audit_tasks(
        tasks=[task],
        runs_by_task={"t-1": [{"status": "succeeded", "outcome": "success"}]},
        diagnostics=[],
    )

    assert findings == ()


def test_done_task_with_real_completed_run_is_clean():
    findings = audit_tasks(
        tasks=[_done_task("t-1", result=None)],
        runs_by_task={
            "t-1": [
                {
                    "status": "done",
                    "outcome": "completed",
                    "summary": "verified",
                    "metadata": {},
                }
            ]
        },
        diagnostics=[],
    )

    assert findings == ()


def test_done_task_with_manual_completed_run_is_clean():
    findings = audit_tasks(
        tasks=[_done_task("t-1", result=None)],
        runs_by_task={
            "t-1": [
                {
                    "status": "completed",
                    "outcome": "completed",
                    "summary": "verified",
                    "metadata": {},
                }
            ]
        },
        diagnostics=[],
    )

    assert findings == ()


def test_factory_contract_is_checked_against_completed_run_metadata():
    task = _done_task("t-1", result=None)
    task["body"] = _contract_body(
        '{"task_class":"implementation",'
        '"required_commands":["uv run pytest -q"],'
        '"required_artifacts":["report.txt"],'
        '"changed_files_required":true}'
    )
    run = {
        "status": "done",
        "outcome": "completed",
        "summary": "implemented and verified",
        "metadata": {
            "changed_files": ["a.py"],
            "commands": [{"command": "uv run pytest -q", "exit_code": 0}],
            "artifacts": ["report.txt"],
        },
    }

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={"t-1": [run]},
        diagnostics=[],
        attachments_by_task={
            "t-1": [
                {
                    "id": 1,
                    "filename": "report.txt",
                    "content_type": "text/plain",
                    "size": 6,
                    "uploaded_by": "builder",
                    "stored_path": "F:/Hermes/attachments/t-1/report.txt",
                    "created_at": 1,
                }
            ]
        },
    )

    assert findings == ()


def test_factory_contract_without_successful_run_rejects_task_level_proof():
    task = _done_task(
        "t-1",
        result="worker claims success",
        metadata={
            "changed_files": ["src/demo.py"],
            "commands": [{"command": "uv run pytest -q", "exit_code": 0}],
        },
    )
    task["created_by"] = "hermes-factory"
    task["body"] = _contract_body(
        '{"task_class":"implementation",'
        '"required_commands":["uv run pytest -q"],'
        '"required_artifacts":[],"changed_files_required":true}'
    )

    findings = audit_tasks(tasks=[task], runs_by_task={"t-1": []}, diagnostics=[])

    assert [(finding.code, finding.task_id) for finding in findings] == [
        ("done-without-proof", "t-1"),
        ("missing-changed-files", "t-1"),
        ("missing-proof-command", "t-1"),
    ]


def test_factory_completion_metadata_requires_limitations_field():
    task = _done_task("t-1", result="implemented")
    task["created_by"] = "hermes-factory"
    task["body"] = _contract_body(
        '{"task_class":"implementation","required_commands":[],'
        '"required_artifacts":[],"changed_files_required":false,'
        '"required_metadata":["changed_files","commands","limitations"]}'
    )
    run = {
        "status": "done",
        "outcome": "completed",
        "summary": "implemented",
        "metadata": {"changed_files": [], "commands": []},
    }

    findings = audit_tasks(
        tasks=[task], runs_by_task={"t-1": [run]}, diagnostics=[]
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="invalid-completion-metadata",
            message="task t-1 has missing or malformed completion metadata: limitations",
            task_id="t-1",
        ),
    )


def test_commit_contract_requires_git_hash_and_decision_log():
    task = _done_task("t-commit", result="committed")
    task["created_by"] = "hermes-factory"
    task["body"] = _contract_body(
        '{"task_class":"commit","required_commands":[],'
        '"required_artifacts":[],"changed_files_required":false,'
        '"required_metadata":["commit","decision_log","limitations"]}'
    )
    run = {
        "status": "done",
        "outcome": "completed",
        "summary": "local commit created",
        "metadata": {
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "decision_log": [],
            "limitations": [],
        },
    }

    assert audit_tasks(
        tasks=[task],
        runs_by_task={"t-commit": [run]},
        diagnostics=[],
        commit_verifier=lambda object_id: object_id == run["metadata"]["commit"],
    ) == ()


def test_commit_contract_rejects_unverified_hex_object_id():
    task = _done_task("t-commit", result="committed")
    task["created_by"] = "hermes-factory"
    task["body"] = _contract_body(
        '{"task_class":"commit","required_commands":[],'
        '"required_artifacts":[],"changed_files_required":false,'
        '"required_metadata":["commit","decision_log","limitations"]}'
    )
    run = {
        "status": "done",
        "outcome": "completed",
        "summary": "local commit created",
        "metadata": {
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "decision_log": [],
            "limitations": [],
        },
    }

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={"t-commit": [run]},
        diagnostics=[],
        commit_verifier=lambda object_id: False,
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="invalid-completion-metadata",
            message="task t-commit has missing or malformed completion metadata: commit",
            task_id="t-commit",
        ),
    )


def test_git_commit_verifier_accepts_commit_and_rejects_blob_and_unknown(tmp_path):
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "factory@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Factory Test"], cwd=tmp_path, check=True
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("proof\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "proof"], cwd=tmp_path, check=True)
    commit_id = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    blob_id = subprocess.run(
        ["git", "hash-object", "tracked.txt"],
        cwd=tmp_path,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()

    assert verify_git_commit(tmp_path, commit_id)
    assert not verify_git_commit(tmp_path, blob_id)
    assert not verify_git_commit(tmp_path, "0" * len(commit_id))


def test_factory_contract_reports_missing_artifact():
    task = _done_task("t-1", result="ok")
    task["body"] = _contract_body(
        '{"task_class":"implementation",'
        '"required_commands":[],"required_artifacts":["report.txt"],'
        '"changed_files_required":false}'
    )

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={
            "t-1": [
                {
                    "status": "done",
                    "outcome": "completed",
                    "summary": "verified",
                    "metadata": {},
                }
            ]
        },
        diagnostics=[],
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-artifact",
            message="task t-1 lacks required proof artifact 'report.txt'",
            task_id="t-1",
        ),
    )


def test_malformed_generated_contract_fails_closed():
    task = _done_task("t-1", result="ok")
    task["body"] = _contract_body("{not-json}")

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="invalid-audit-contract",
            message="task t-1 has a malformed or schema-invalid factory audit contract",
            task_id="t-1",
        ),
    )


def test_removed_factory_contract_fails_closed():
    task = _done_task("t-1", result="ok")
    task["body"] = """\
# Project
Demo

# Mission
Ship

# Proof required
Commands that must be recorded:
- pytest

# Scope boundaries
Stay in scope.

# Handoff
Complete in Kanban.
"""

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="invalid-audit-contract",
            message="task t-1 has a malformed or schema-invalid factory audit contract",
            task_id="t-1",
        ),
    )


def test_factory_idempotency_key_requires_contract_even_when_body_is_empty():
    task = _done_task("t-1", result="ok")
    task["idempotency_key"] = "factory:demo:mission:slice:implementation"
    task["body"] = ""

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="invalid-audit-contract",
            message="task t-1 has a malformed or schema-invalid factory audit contract",
            task_id="t-1",
        ),
    )


def test_factory_created_by_requires_contract_even_when_body_is_empty():
    task = _done_task("t-1", result="ok")
    task["created_by"] = "hermes-factory"
    task["body"] = ""

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="invalid-audit-contract",
            message="task t-1 has a malformed or schema-invalid factory audit contract",
            task_id="t-1",
        ),
    )


@pytest.mark.parametrize(
    "status",
    ["failed", "cancelled", "crashed", "timed_out", "spawn_failed", "changes_requested"],
)
def test_terminal_failure_status_never_accepts_completed_outcome(status):
    findings = audit_tasks(
        tasks=[_done_task("t-1", result=None)],
        runs_by_task={
            "t-1": [{"status": status, "outcome": "completed", "summary": "bad"}]
        },
        diagnostics=[],
    )

    assert findings == (
        HealthFinding(
            severity="critical",
            code="done-without-proof",
            message="task t-1 is done without a result or successful run summary",
            task_id="t-1",
        ),
    )


def test_contradictory_failed_completed_run_is_not_successful():
    findings = audit_tasks(
        tasks=[_done_task("t-1", result=None)],
        runs_by_task={
            "t-1": [{"status": "failed", "outcome": "completed", "summary": "bad"}]
        },
        diagnostics=[],
    )

    assert findings == (
        HealthFinding(
            severity="critical",
            code="done-without-proof",
            message="task t-1 is done without a result or successful run summary",
            task_id="t-1",
        ),
    )


def test_required_artifact_accepts_authoritative_attachment_record():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={"required_artifacts": ["report.txt"]},
    )

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={},
        diagnostics=[],
        attachments_by_task={
            "t-1": [
                {
                    "id": 1,
                    "filename": "report.txt",
                    "content_type": "text/plain",
                    "size": 6,
                    "uploaded_by": "builder",
                    "stored_path": "F:/HermesFactory/attachments/t-1/report.txt",
                    "created_at": 1,
                }
            ]
        },
    )

    assert findings == ()


def test_filename_only_attachment_record_cannot_satisfy_required_artifact():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={"required_artifacts": ["report.txt"]},
    )

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={},
        diagnostics=[],
        attachments_by_task={"t-1": [{"filename": "report.txt"}]},
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-artifact",
            message="task t-1 lacks required proof artifact 'report.txt'",
            task_id="t-1",
        ),
    )


def test_attachment_missing_native_optional_fields_cannot_satisfy_required_artifact():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={"required_artifacts": ["report.txt"]},
    )
    incomplete = {
        "id": 1,
        "filename": "report.txt",
        "size": 6,
        "stored_path": "F:/HermesFactory/attachments/t-1/report.txt",
        "created_at": 1,
    }

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={},
        diagnostics=[],
        attachments_by_task={"t-1": [incomplete]},
    )

    assert [finding.code for finding in findings] == ["missing-proof-artifact"]


def test_self_reported_artifact_path_is_not_authoritative():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_artifacts": ["report.txt"],
            "artifacts": ["C:/definitely/nonexistent/report.txt"],
        },
    )

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={},
        diagnostics=[],
        attachments_by_task={"t-1": []},
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-artifact",
            message="task t-1 lacks required proof artifact 'report.txt'",
            task_id="t-1",
        ),
    )


def test_artifact_mapping_cannot_satisfy_required_artifact():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_artifacts": ["report.txt"],
            "artifacts": {"report.txt": False},
        },
    )

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-artifact",
            message="task t-1 lacks required proof artifact 'report.txt'",
            task_id="t-1",
        ),
    )


def test_relative_artifact_requirement_does_not_match_filename_suffix():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_artifacts": ["report.txt"],
            "artifacts": ["F:/HermesFactory/.hermes/artifacts/other-report.txt"],
        },
    )

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-artifact",
            message="task t-1 lacks required proof artifact 'report.txt'",
            task_id="t-1",
        ),
    )


def test_proof_uses_latest_successful_run_metadata_not_later_failed_run():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={"required_commands": ["uv run pytest -q"]},
    )
    runs = [
        {
            "status": "succeeded",
            "metadata": {
                "commands": [{"command": "uv run pytest -q", "exit_code": 0}]
            },
        },
        {"status": "failed", "outcome": "failure", "metadata": {}},
    ]

    findings = audit_tasks(
        tasks=[task], runs_by_task={"t-1": runs}, diagnostics=[]
    )

    assert findings == ()


def test_newer_successful_run_without_metadata_does_not_reuse_old_proof():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={"required_commands": ["uv run pytest -q"]},
    )
    runs = [
        {
            "status": "succeeded",
            "metadata": {
                "commands": [{"command": "uv run pytest -q", "exit_code": 0}]
            },
        },
        {"status": "done", "outcome": "completed"},
    ]

    findings = audit_tasks(
        tasks=[task], runs_by_task={"t-1": runs}, diagnostics=[]
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-command",
            message="task t-1 lacks required proof command 'uv run pytest -q'",
            task_id="t-1",
        ),
    )


def test_latest_successful_run_does_not_reuse_task_level_proof():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_commands": ["uv run pytest -q"],
            "commands": [{"command": "uv run pytest -q", "exit_code": 0}],
        },
    )

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={"t-1": [{"status": "done", "outcome": "completed"}]},
        diagnostics=[],
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-command",
            message="task t-1 lacks required proof command 'uv run pytest -q'",
            task_id="t-1",
        ),
    )


def test_failed_run_metadata_cannot_satisfy_proof_requirement():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={"required_commands": ["uv run pytest -q"]},
    )
    failed_run = {
        "status": "failed",
        "outcome": "failure",
        "metadata": {
            "commands": [{"command": "uv run pytest -q", "exit_code": 0}]
        },
    }

    findings = audit_tasks(
        tasks=[task], runs_by_task={"t-1": [failed_run]}, diagnostics=[]
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-command",
            message="task t-1 lacks required proof command 'uv run pytest -q'",
            task_id="t-1",
        ),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("required_commands", {"uv run pytest -q": False}),
        ("required_artifacts", {"report.txt": False}),
    ],
)
def test_malformed_legacy_requirement_metadata_fails_closed(field, value):
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            field: value,
            "commands": [{"command": "uv run pytest -q", "exit_code": 0}],
        },
    )

    findings = audit_tasks(
        tasks=[task],
        runs_by_task={},
        diagnostics=[],
        attachments_by_task={"t-1": [{"filename": "report.txt"}]},
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="invalid-proof-requirements",
            message="task t-1 has malformed legacy proof requirements",
            task_id="t-1",
        ),
    )


def test_changed_files_required_without_metadata_is_error():
    task = _done_task("t-1", result="ok", metadata={"changed_files_required": True})
    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-changed-files",
            message="task t-1 requires changed files but metadata.changed_files is absent",
            task_id="t-1",
        ),
    )


def test_changed_files_required_rejects_scalar_metadata():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={"changed_files_required": True, "changed_files": "a.py"},
    )

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-changed-files",
            message="task t-1 requires changed files but metadata.changed_files is absent",
            task_id="t-1",
        ),
    )


def test_changed_files_recorded_is_clean():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={"changed_files_required": True, "changed_files": ["a.py"]},
    )
    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == ()


def test_required_command_proof_absent_from_metadata_is_error():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_commands": ["uv run pytest -q"],
            "commands": ["cargo build"],
        },
    )
    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-command",
            message="task t-1 lacks required proof command 'uv run pytest -q'",
            task_id="t-1",
        ),
    )


def test_required_command_with_nonzero_exit_is_error():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_commands": ["uv run pytest -q"],
            "commands": [{"command": "uv run pytest -q", "exit_code": 1}],
        },
    )
    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-command",
            message="task t-1 lacks required proof command 'uv run pytest -q'",
            task_id="t-1",
        ),
    )


def test_required_command_bare_string_is_error():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_commands": ["uv run pytest -q"],
            "commands": ["uv run pytest -q", "cargo build"],
        },
    )
    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-command",
            message="task t-1 lacks required proof command 'uv run pytest -q'",
            task_id="t-1",
        ),
    )


def test_required_command_false_exit_code_is_error():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_commands": ["uv run pytest -q"],
            "commands": [{"command": "uv run pytest -q", "exit_code": False}],
        },
    )

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-command",
            message="task t-1 lacks required proof command 'uv run pytest -q'",
            task_id="t-1",
        ),
    )


def test_malformed_command_list_cannot_satisfy_required_command():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_commands": ["uv run pytest -q"],
            "commands": [
                {"command": "uv run pytest -q", "exit_code": 0},
                "malformed",
            ],
        },
    )

    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="error",
            code="missing-proof-command",
            message="task t-1 lacks required proof command 'uv run pytest -q'",
            task_id="t-1",
        ),
    )


def test_required_structured_command_proof_present_is_clean():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_commands": ["uv run pytest -q"],
            "commands": [{"command": "uv run pytest -q", "exit_code": 0}],
        },
    )
    findings = audit_tasks(tasks=[task], runs_by_task={}, diagnostics=[])

    assert findings == ()


def test_review_task_without_reviewer_summary_is_warning():
    review = {
        "id": "t-2",
        "title": "Review demo",
        "status": "done",
        "result": "looks fine",
        "metadata": {"task_class": "review"},
    }
    findings = audit_tasks(tasks=[review], runs_by_task={}, diagnostics=[])

    assert findings == (
        HealthFinding(
            severity="warning",
            code="review-without-summary",
            message="review task t-2 has no independent reviewer summary",
            task_id="t-2",
        ),
    )


def test_factory_review_without_reviewer_summary_is_error():
    review = {
        "id": "t-2",
        "title": "Review demo",
        "status": "done",
        "result": "looks fine",
        "created_by": "hermes-factory",
        "body": _contract_body(
            '{"task_class":"review","required_commands":[],'
            '"required_artifacts":[],"changed_files_required":false}'
        ),
        "metadata": {},
    }
    findings = audit_tasks(
        tasks=[review],
        runs_by_task={
            "t-2": [
                {
                    "status": "done",
                    "outcome": "completed",
                    "metadata": {},
                }
            ]
        },
        diagnostics=[],
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="review-without-summary",
            message="review task t-2 has no independent reviewer summary",
            task_id="t-2",
        ),
    )


def test_review_task_with_reviewer_summary_is_clean():
    review = {
        "id": "t-2",
        "title": "Review demo",
        "status": "done",
        "result": "approved",
        "metadata": {"task_class": "review", "reviewer_summary": "verified tests pass"},
    }
    findings = audit_tasks(tasks=[review], runs_by_task={}, diagnostics=[])

    assert findings == ()


def test_review_task_with_successful_run_summary_is_clean():
    review = {
        "id": "t-2",
        "title": "Review demo",
        "status": "done",
        "result": "approved",
        "metadata": {"task_class": "review"},
    }

    findings = audit_tasks(
        tasks=[review],
        runs_by_task={"t-2": [{"status": "succeeded", "summary": "verified"}]},
        diagnostics=[],
    )

    assert findings == ()


def test_review_task_does_not_reuse_older_successful_run_summary():
    review = {
        "id": "t-2",
        "title": "Review demo",
        "status": "done",
        "result": "approved",
        "metadata": {"task_class": "review"},
    }

    findings = audit_tasks(
        tasks=[review],
        runs_by_task={
            "t-2": [
                {"status": "succeeded", "summary": "old verification"},
                {"status": "done", "outcome": "completed"},
            ]
        },
        diagnostics=[],
    )

    assert findings == (
        HealthFinding(
            severity="warning",
            code="review-without-summary",
            message="review task t-2 has no independent reviewer summary",
            task_id="t-2",
        ),
    )


def test_review_task_does_not_reuse_task_level_summary():
    review = {
        "id": "t-2",
        "title": "Review demo",
        "status": "done",
        "result": "approved",
        "metadata": {"task_class": "review", "reviewer_summary": "old verification"},
    }

    findings = audit_tasks(
        tasks=[review],
        runs_by_task={"t-2": [{"status": "done", "outcome": "completed"}]},
        diagnostics=[],
    )

    assert findings == (
        HealthFinding(
            severity="warning",
            code="review-without-summary",
            message="review task t-2 has no independent reviewer summary",
            task_id="t-2",
        ),
    )


def test_native_hermes_diagnostics_are_flattened():
    findings = audit_tasks(
        tasks=[],
        runs_by_task={},
        diagnostics=[
            {
                "task_id": "t-9",
                "title": "Demo task",
                "status": "blocked",
                "assignee": "builder",
                "diagnostics": [
                    {
                        "kind": "repeated_failures",
                        "severity": "error",
                        "title": "Agent failed repeatedly",
                        "detail": "claim is stale",
                    }
                ],
            },
            {"task_id": None, "dispatch_profiles": "any", "diagnostics": []},
        ],
    )

    assert findings == (
        HealthFinding(
            severity="error",
            code="repeated_failures",
            message="claim is stale",
            task_id="t-9",
        ),
    )


def test_findings_sort_by_severity_then_task_id_then_code():
    tasks = [
        _done_task("t-b", result=None, metadata={"changed_files_required": True}),
        _done_task("t-a", result=None, metadata={"changed_files_required": True}),
    ]
    diagnostics = [{"severity": "warning", "code": "stale-claim", "task_id": "t-0"}]

    findings = audit_tasks(tasks=tasks, runs_by_task={}, diagnostics=diagnostics)

    assert [(finding.severity, finding.code, finding.task_id) for finding in findings] == [
        ("critical", "done-without-proof", "t-a"),
        ("critical", "done-without-proof", "t-b"),
        ("error", "missing-changed-files", "t-a"),
        ("error", "missing-changed-files", "t-b"),
        ("warning", "stale-claim", "t-0"),
    ]
