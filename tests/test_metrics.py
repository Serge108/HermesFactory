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
        packets_per_architectural_slice={},
        hard_timeout_rate_by_task_class={},
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


def test_proof_rate_accepts_real_completed_run_shape():
    tasks = [_task("t-1", result=None)]
    runs = {"t-1": [_run(status="done", outcome="completed")]}

    metrics = compute_metrics(tasks, runs, {"now": 100})

    assert metrics.proof_rate == 1.0


def test_proof_rate_rejects_contradictory_failed_completed_run():
    tasks = [_task("t-1", result=None)]
    runs = {"t-1": [_run(status="failed", outcome="completed")]}

    metrics = compute_metrics(tasks, runs, {"now": 100})

    assert metrics.proof_rate == 0.0


def test_proof_rate_uses_authoritative_override():
    tasks = [_task("t-1", result="claimed")]

    metrics = compute_metrics(
        tasks,
        {},
        {"now": 100},
        proof_by_task={"t-1": False},
    )

    assert metrics.proof_rate == 0.0


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


def test_material_progress_metrics_use_factory_task_identity_and_run_timestamps():
    tasks = [
        _task(
            "d-1",
            created_at=0,
            completed_at=600,
            metadata={"task_class": "decomposition"},
        )
        | {"idempotency_key": "factory:demo:mission:core:decomposition"},
        _task(
            "i-1",
            created_at=600,
            completed_at=1_200,
            metadata={"task_class": "implementation"},
        )
        | {"idempotency_key": "factory:demo:mission:core:packet-a:implementation"},
        _task(
            "r-1",
            created_at=600,
            completed_at=1_500,
            metadata={"task_class": "review"},
        )
        | {"idempotency_key": "factory:demo:mission:core:packet-a:review"},
        _task(
            "i-2",
            created_at=600,
            completed_at=1_800,
            metadata={"task_class": "implementation"},
        )
        | {"idempotency_key": "factory:demo:mission:core:packet-b:implementation"},
        _task(
            "r-2",
            created_at=600,
            completed_at=2_100,
            metadata={"task_class": "review"},
        )
        | {"idempotency_key": "factory:demo:mission:core:packet-b:review"},
    ]
    runs = {
        "d-1": [_run(status="done", outcome="completed", started_at=0, ended_at=600)],
        "i-1": [_run(status="done", outcome="completed", started_at=610, ended_at=1_200)],
        "r-1": [_run(status="done", outcome="completed", started_at=1_210, ended_at=1_500)],
        "i-2": [
            _run(
                status="blocked",
                outcome="blocked",
                started_at=610,
                ended_at=900,
                error='Factory progress checkpoint: {"version": 1}',
            ),
            _run(status="done", outcome="completed", started_at=1_510, ended_at=1_800),
        ],
        "r-2": [
            _run(status="timed_out", outcome="timed_out", started_at=1_810, ended_at=2_100)
        ],
    }

    metrics = compute_metrics(tasks, runs, {"now": 2_100})

    assert metrics.decomposition_lead_time_minutes == 10.0
    assert metrics.packets_per_architectural_slice == {"core": 2}
    assert metrics.builder_first_attempt_completion_rate == 0.5
    assert metrics.progress_checkpoint_rate == 1.0
    assert metrics.hard_timeout_rate_by_task_class == {
        "decomposition": 0.0,
        "implementation": 0.0,
        "review": 0.5,
    }
    assert metrics.median_packet_lead_time_minutes == 20.0


def test_rework_after_review_requires_a_later_builder_attempt():
    tasks = [
        _task("i-1", metadata={"task_class": "implementation"})
        | {"idempotency_key": "factory:demo:mission:core:packet-a:implementation"},
        _task("r-1", metadata={"task_class": "review"})
        | {"idempotency_key": "factory:demo:mission:core:packet-a:review"},
        _task("i-2", metadata={"task_class": "implementation"})
        | {"idempotency_key": "factory:demo:mission:core:packet-b:implementation"},
        _task("r-2", metadata={"task_class": "review"})
        | {"idempotency_key": "factory:demo:mission:core:packet-b:review"},
    ]
    runs = {
        "i-1": [
            _run(status="done", outcome="completed", started_at=10, ended_at=20),
            _run(status="done", outcome="completed", started_at=40, ended_at=50),
        ],
        "r-1": [_run(status="blocked", outcome="changes_requested", started_at=25, ended_at=30)],
        "i-2": [_run(status="done", outcome="completed", started_at=10, ended_at=20)],
        "r-2": [_run(status="done", outcome="completed", started_at=25, ended_at=30)],
    }

    metrics = compute_metrics(tasks, runs, {"now": 100})

    assert metrics.rework_rate_after_review == 0.5


def test_action_latency_is_explicitly_unavailable_without_authoritative_events():
    task = _task("i-1", metadata={"task_class": "implementation"}) | {
        "idempotency_key": "factory:demo:mission:core:packet-a:implementation"
    }

    metrics = compute_metrics(
        [task],
        {"i-1": [_run(status="done", outcome="completed", started_at=10, ended_at=20)]},
        {"now": 100},
    )

    assert metrics.time_to_first_command_minutes is None
    assert metrics.time_to_first_file_mutation_minutes is None
    assert metrics.action_latency_observation == "unavailable"


def test_v2_packet_identity_uses_the_compiler_key_format():
    tasks = [
        _task("i-1", created_at=600, completed_at=1_200)
        | {"idempotency_key": "factory:demo:demo-mission:core:packet:p1:implementation"},
        _task("r-1", created_at=600, completed_at=1_500)
        | {"idempotency_key": "factory:demo:demo-mission:core:packet:p1:review"},
        _task("reconcile-1", "ready")
        | {"idempotency_key": "factory:demo:reconcile:t_ab12cd:gave-up:1790540080"},
        _task("a-1")
        | {"idempotency_key": "factory:demo:demo-mission:acceptance"},
    ]
    runs = {
        "i-1": [_run(status="done", outcome="completed", id=1, started_at=610, ended_at=1_200)],
        "r-1": [_run(status="done", outcome="completed", id=2, started_at=1_210, ended_at=1_500)],
        "a-1": [_run(status="done", outcome="completed", id=3, started_at=1_510, ended_at=1_600)],
    }

    metrics = compute_metrics(tasks, runs, {"now": 1_500})

    assert metrics.packets_per_architectural_slice == {"core": 1}
    assert metrics.builder_first_attempt_completion_rate == 1.0
    assert metrics.median_packet_lead_time_minutes == 15.0
    assert metrics.hard_timeout_rate_by_task_class == {
        "acceptance": 0.0,
        "implementation": 0.0,
        "review": 0.0,
    }


def test_progress_checkpoint_rate_counts_unsuccessful_terminal_attempts():
    task = _task("i-1") | {
        "idempotency_key": "factory:demo:mission:core:packet-a:implementation"
    }
    runs = {
        "i-1": [
            _run(
                status="blocked",
                outcome="blocked",
                id=1,
                started_at=10,
                ended_at=20,
                summary='Factory progress checkpoint: {"version": 1}',
            ),
            _run(
                status="timed_out",
                outcome="timed_out",
                id=2,
                started_at=30,
                ended_at=40,
                summary="",
            ),
        ]
    }

    metrics = compute_metrics([task], runs, {"now": 100})

    assert metrics.progress_checkpoint_rate == 0.5


def test_rework_after_review_is_null_without_a_completed_review_observation():
    tasks = [
        _task("i-1")
        | {"idempotency_key": "factory:demo:mission:core:packet-a:implementation"},
        _task("r-1", "running")
        | {"idempotency_key": "factory:demo:mission:core:packet-a:review"},
    ]
    runs = {
        "i-1": [_run(status="done", outcome="completed", id=1, started_at=10, ended_at=20)],
        "r-1": [_run(status="running", outcome=None, id=2, started_at=25)],
    }

    metrics = compute_metrics(tasks, runs, {"now": 100})

    assert metrics.rework_rate_after_review is None


def test_first_builder_attempt_follows_run_order_not_input_order():
    task = _task("i-1") | {
        "idempotency_key": "factory:demo:mission:core:packet-a:implementation"
    }
    runs = {
        "i-1": [
            _run(status="done", outcome="completed", id=8, started_at=40, ended_at=50),
            _run(status="timed_out", outcome="timed_out", id=2, started_at=10, ended_at=20),
        ]
    }

    metrics = compute_metrics([task], runs, {"now": 100})

    assert metrics.builder_first_attempt_completion_rate == 0.0


def test_progress_checkpoint_metric_rejects_a_whitespace_prefixed_lookalike():
    task = _task("i-1", metadata={"task_class": "implementation"}) | {
        "idempotency_key": "factory:demo:mission:core:packet-a:implementation"
    }

    metrics = compute_metrics(
        [task],
        {
            "i-1": [
                _run(
                    status="blocked",
                    outcome="blocked",
                    started_at=10,
                    ended_at=20,
                    error=' Factory progress checkpoint: {"version": 1}',
                )
            ]
        },
        {"now": 100},
    )

    assert metrics.progress_checkpoint_rate == 0.0
