from hermes_factory.metrics import BoardMetrics, compute_metrics


def _task(
    task_id: str,
    status: str = "done",
    assignee: str = "default",
    created_at: int | None = None,
    completed_at: int | None = None,
    result: object = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": task_id,
        "status": status,
        "assignee": assignee,
        "created_at": created_at,
        "completed_at": completed_at,
        "result": result,
        "metadata": metadata or {},
    }


def _run(status: str = "succeeded", outcome: str | None = None, **fields: object) -> dict:
    run = {"status": status, "outcome": outcome}
    run.update(fields)
    return run


def test_empty_board_metrics_are_zero_or_none():
    metrics = compute_metrics([], {}, {"now": 100})

    assert metrics == BoardMetrics(
        totals={},
        done=0,
        throughput_per_day=None,
        proof_rate=None,
        rework_rate=None,
        changes_requested=0,
        failed_runs=0,
        total_runs=0,
        median_lead_time_minutes=None,
        max_lead_time_minutes=None,
        open_work_by_seat={},
        oldest_ready_age_seconds=None,
    )


def test_totals_count_every_task_status():
    tasks = [
        _task("t-1", "done"),
        _task("t-2", "ready"),
        _task("t-3", "blocked"),
        _task("t-4", "todo"),
        _task("t-5", "running"),
    ]

    metrics = compute_metrics(tasks, {}, {"now": 100})

    assert metrics.totals == {
        "done": 1,
        "ready": 1,
        "blocked": 1,
        "todo": 1,
        "running": 1,
    }
    assert metrics.done == 1
    assert metrics.open_work_by_seat == {"default": 3}


def test_throughput_divides_done_by_active_window_in_days():
    tasks = [
        _task("t-1", created_at=20_000, completed_at=90_000, result="ok"),
        _task("t-2", created_at=21_000, completed_at=91_000, result="ok"),
    ]
    stats = {"now": 2_093_600}  # 24 days after the oldest created_at (20000 + 24*86400)

    metrics = compute_metrics(tasks, {}, stats)

    assert metrics.done == 2
    assert metrics.throughput_per_day == round(2 / 24, 4)


def test_throughput_is_none_without_a_time_window():
    tasks = [_task("t-1", created_at=50_000, completed_at=51_000, result="ok")]
    stats = {"now": 50_000}

    metrics = compute_metrics(tasks, {}, stats)

    assert metrics.throughput_per_day is None


def test_proof_rate_uses_result_or_successful_run():
    tasks = [
        _task("t-1", result="done"),
        _task("t-2", result=None),
        _task("t-3", result=None),
    ]
    runs = {"t-3": [_run(outcome="success")]}

    metrics = compute_metrics(tasks, runs, {"now": 100})

    assert metrics.proof_rate == 2 / 3


def test_rework_rate_counts_tasks_with_more_than_one_run():
    tasks = [
        _task("t-1", result="a"),
        _task("t-2", result="b"),
        _task("t-3", result="c"),
    ]
    runs = {
        "t-1": [_run()],
        "t-2": [_run(), _run(outcome="changes_requested")],
        "t-3": [_run(outcome="changes_requested"), _run(), _run()],
    }

    metrics = compute_metrics(tasks, runs, {"now": 100})

    assert metrics.rework_rate == 2 / 3
    assert metrics.changes_requested == 2
    assert metrics.total_runs == 6


def test_failed_runs_count_failed_outcomes_only():
    runs = {
        "t-1": [
            _run(status="spawn_failed"),
            _run(status="timed_out"),
            _run(status="crashed"),
            _run(),
        ]
    }

    metrics = compute_metrics([], runs, {"now": 100})

    assert metrics.failed_runs == 3
    assert metrics.total_runs == 4


def test_lead_time_uses_created_to_completed():
    tasks = [
        _task("t-1", created_at=0, completed_at=3_600),
        _task("t-2", created_at=0, completed_at=7_200),
        _task("t-3", created_at=0, completed_at=36_000),
    ]

    metrics = compute_metrics(tasks, {}, {"now": 40_000})

    assert metrics.median_lead_time_minutes == 120.0
    assert metrics.max_lead_time_minutes == 600.0


def test_lead_time_is_none_without_completed_tasks():
    tasks = [_task("t-1", "todo", created_at=0)]

    metrics = compute_metrics(tasks, {}, {"now": 100})

    assert metrics.median_lead_time_minutes is None
    assert metrics.max_lead_time_minutes is None


def test_open_work_groups_by_assignee_and_ignores_done():
    tasks = [
        _task("t-1", "ready", assignee="builder"),
        _task("t-2", "running", assignee="builder"),
        _task("t-3", "blocked", assignee="reviewer"),
        _task("t-4", "done", assignee="builder"),
        _task("t-5", "todo", assignee="reviewer"),
    ]

    metrics = compute_metrics(tasks, {}, {"now": 100, "oldest_ready_age_seconds": 42})

    assert metrics.open_work_by_seat == {"builder": 2, "reviewer": 1}
    assert metrics.oldest_ready_age_seconds == 42
