from hermes_factory.health import HealthFinding, audit_tasks


def _done_task(task_id: str, result: object = None, metadata: dict | None = None) -> dict:
    return {
        "id": task_id,
        "title": "Implement demo",
        "status": "done",
        "result": result,
        "metadata": metadata or {},
    }


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


def test_required_command_proof_present_is_clean():
    task = _done_task(
        "t-1",
        result="ok",
        metadata={
            "required_commands": ["uv run pytest -q"],
            "commands": ["uv run pytest -q", "cargo build"],
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


def test_existing_hermes_diagnostics_are_preserved():
    findings = audit_tasks(
        tasks=[],
        runs_by_task={},
        diagnostics=[
            {"severity": "warning", "code": "stale-claim", "task_id": "t-9", "message": "claim is stale"},
        ],
    )

    assert findings == (
        HealthFinding(
            severity="warning",
            code="stale-claim",
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
