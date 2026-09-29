from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from hermes_factory import operator
from hermes_factory.cli import main
from hermes_factory.errors import FactoryError
from hermes_factory.hermes_cli import HermesClient
from hermes_factory.operator import (
    CronExecution,
    CronIncident,
    CronJob,
    CronState,
    OperatorPlan,
    assess_engine,
    bound_jobs,
    build_session_argv,
    lock_is_live,
    parse_cron_executions,
    parse_cron_incidents,
    parse_cron_jobs,
    read_lock,
    supervise,
)

NOW = 1_790_730_000.0


def iso(seconds_ago: float) -> str:
    return datetime.fromtimestamp(NOW - seconds_ago, tz=timezone.utc).isoformat()


RECENT = iso(60)
STALE = iso(3600)


def job_payload(
    job_id: str = "job-1",
    name: str = "Hermes Factory reconcile: demo",
    *,
    enabled: bool = True,
    state: str = "scheduled",
    last_status: str | None = "failed",
    failure_streak: int = 0,
    last_run_at: str | None = RECENT,
    workdir: str = r"F:\demo",
    script: str | None = "demo-watchdog.py",
) -> dict:
    return {
        "id": job_id,
        "name": name,
        "script": script,
        "enabled": enabled,
        "state": state,
        "last_status": last_status,
        "last_error": None,
        "failure_streak": failure_streak,
        "workdir": workdir,
        "last_run_at": last_run_at,
    }


def incident_payload(
    incident_id: str = "inc-1",
    job_id: str = "job-1",
    *,
    state: str = "alerted",
    first_seen_at: str = STALE,
    last_seen_at: str = RECENT,
    error: str = "Script exited with code 2",
) -> dict:
    return {
        "id": incident_id,
        "job_id": job_id,
        "state": state,
        "failure_type": "script",
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
        "error": error,
        "output_file": None,
    }


def execution_payload(
    execution_id: str = "exec-1",
    job_id: str = "job-1",
    *,
    status: str = "completed",
    started_at: str = RECENT,
    finished_at: str | None = RECENT,
) -> dict:
    return {
        "id": execution_id,
        "job_id": job_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
    }


def cron_state(jobs: list[dict], incidents: list[dict] | None = None, executions: list[dict] | None = None) -> CronState:
    return CronState(
        jobs=parse_cron_jobs({"jobs": jobs}),
        incidents=parse_cron_incidents(incidents or []),
        executions=parse_cron_executions(executions or []),
    )


# --- parsing ---------------------------------------------------------------


def test_parse_cron_jobs_reads_the_documented_fields():
    jobs = parse_cron_jobs({"jobs": [job_payload(failure_streak=2)]})

    assert jobs == (
        CronJob(
            id="job-1",
            name="Hermes Factory reconcile: demo",
            script="demo-watchdog.py",
            enabled=True,
            state="scheduled",
            last_status="failed",
            last_error=None,
            failure_streak=2,
            workdir=r"F:\demo",
            last_run_at=RECENT,
        ),
    )


def test_parse_cron_jobs_rejects_a_job_without_an_id():
    with pytest.raises(FactoryError):
        parse_cron_jobs({"jobs": [{"name": "broken"}]})


def test_parse_cron_jobs_rejects_a_malformed_payload():
    with pytest.raises(FactoryError):
        parse_cron_jobs({"jobs": "not-a-list"})


def test_parse_cron_jobs_rejects_a_non_integer_failure_streak():
    payload = job_payload()
    payload["failure_streak"] = "zero"

    with pytest.raises(FactoryError):
        parse_cron_jobs({"jobs": [payload]})


def test_parse_cron_incidents_reads_the_documented_fields():
    incidents = parse_cron_incidents([incident_payload()])

    assert incidents == (
        CronIncident(
            id="inc-1",
            job_id="job-1",
            state="alerted",
            failure_type="script",
            first_seen_at=STALE,
            last_seen_at=RECENT,
            error="Script exited with code 2",
            output_file=None,
        ),
    )


# --- binding ---------------------------------------------------------------


def test_binds_jobs_by_project_name_suffix_and_workdir():
    state = cron_state(
        [
            job_payload("job-1"),
            job_payload("job-2", "Hermes Factory reconcile: other", workdir=r"F:\other"),
        ]
    )

    assert [item.id for item in bound_jobs(state, "demo")] == ["job-1"]


def test_excludes_operator_supervision_jobs_from_binding():
    state = cron_state(
        [job_payload("job-1", "Hermes Factory reconcile: demo"), job_payload("job-2", "Hermes Factory operator: demo")]
    )

    assert [item.id for item in bound_jobs(state, "demo")] == ["job-1"]


def test_explicit_job_ids_bind_regardless_of_name_and_workdir():
    state = cron_state(
        [
            job_payload("job-1", "unrelated name", workdir=r"F:\unrelated"),
            job_payload("job-2", "Hermes Factory reconcile: demo"),
        ]
    )

    assert [item.id for item in bound_jobs(state, "demo", job_ids=("job-1",))] == ["job-1"]


def test_an_unknown_explicit_job_id_is_reported():
    state = cron_state([job_payload("job-1")])

    with pytest.raises(FactoryError):
        bound_jobs(state, "demo", job_ids=("job-9",))


# --- assessment ------------------------------------------------------------


def test_a_project_without_a_watchdog_job_is_unregistered():
    verdict = assess_engine(cron_state([]), "demo", now=NOW)

    assert verdict.verdict == "unregistered"
    assert verdict.reasons == ("no_watchdog_job_for_project",)
    assert not verdict.launches()


def test_a_live_watchdog_without_failures_is_healthy():
    state = cron_state([job_payload(last_status="ok", failure_streak=0, last_run_at=RECENT)])

    verdict = assess_engine(state, "demo", now=NOW)

    assert verdict.verdict == "healthy"
    assert not verdict.launches()


def test_a_single_failure_is_degraded_and_does_not_launch():
    state = cron_state(
        [job_payload(failure_streak=1, last_status="failed")],
        [incident_payload(state="alerted")],
    )

    verdict = assess_engine(state, "demo", now=NOW)

    assert verdict.verdict == "degraded"
    assert verdict.reasons == ("failure_streak_below_threshold",)
    assert not verdict.launches()


def test_a_persistent_failure_streak_stops_the_engine():
    state = cron_state(
        [job_payload(failure_streak=3, last_status="failed")],
        [incident_payload(state="alerted")],
    )

    verdict = assess_engine(state, "demo", now=NOW)

    assert verdict.verdict == "stopped"
    assert verdict.launches()
    assert verdict.reasons == ("failure_streak_reached_threshold",)


def test_an_unacknowledged_incident_that_self_healed_is_healthy():
    state = cron_state(
        [job_payload(last_status="ok", failure_streak=0)],
        [incident_payload(state="alerted")],
    )

    verdict = assess_engine(state, "demo", now=NOW)

    assert verdict.verdict == "healthy"
    assert verdict.jobs[0].open_incident_ids == ("inc-1",)


def test_a_paused_watchdog_is_reported_paused_and_never_launches():
    state = cron_state([job_payload(enabled=False, state="paused", failure_streak=5)])

    verdict = assess_engine(state, "demo", now=NOW)

    assert verdict.verdict == "paused"
    assert verdict.reasons == ("watchdog_paused_by_operator",)
    assert not verdict.launches()


def test_an_enabled_watchdog_that_stopped_ticking_is_stopped():
    state = cron_state([job_payload(last_status="ok", failure_streak=0, last_run_at=STALE)])

    verdict = assess_engine(state, "demo", now=NOW)

    assert verdict.verdict == "stopped"
    assert verdict.reasons == ("watchdog_not_ticking",)
    assert verdict.launches()


def test_a_paused_job_outranks_a_stale_last_run():
    state = cron_state(
        [job_payload(enabled=False, state="paused", failure_streak=0, last_run_at=STALE)]
    )

    assert assess_engine(state, "demo", now=NOW).verdict == "paused"


def test_a_running_execution_keeps_the_loop_alive_despite_a_stale_last_run():
    state = cron_state(
        [job_payload(last_status="ok", failure_streak=0, last_run_at=STALE)],
        executions=[execution_payload(status="running", started_at=iso(120), finished_at=None)],
    )

    verdict = assess_engine(state, "demo", now=NOW)

    assert verdict.verdict == "healthy"


def test_a_recent_completed_execution_keeps_the_loop_alive():
    state = cron_state(
        [job_payload(last_status="ok", failure_streak=0, last_run_at=STALE)],
        executions=[execution_payload(started_at=iso(300), finished_at=iso(280))],
    )

    assert assess_engine(state, "demo", now=NOW).verdict == "healthy"


def test_an_old_execution_does_not_keep_the_loop_alive():
    state = cron_state(
        [job_payload(last_status="ok", failure_streak=0, last_run_at=STALE)],
        executions=[execution_payload(started_at=iso(3000), finished_at=iso(2990))],
    )

    assert assess_engine(state, "demo", now=NOW).verdict == "stopped"


def test_parse_cron_executions_reads_the_documented_fields():
    executions = parse_cron_executions([execution_payload()])

    assert executions == (
        CronExecution(
            id="exec-1",
            job_id="job-1",
            status="completed",
            started_at=RECENT,
            finished_at=RECENT,
        ),
    )


def test_parse_cron_executions_rejects_a_row_without_a_job_id():
    with pytest.raises(FactoryError):
        parse_cron_executions([{"id": "exec-1", "status": "completed", "started_at": RECENT}])


def test_a_claimed_run_without_a_start_time_is_accepted():
    state = cron_state(
        [job_payload(last_status="ok", failure_streak=0, last_run_at=RECENT)],
        executions=[
            {
                "id": "exec-1",
                "job_id": "job-1",
                "status": "running",
                "started_at": None,
                "finished_at": None,
            }
        ],
    )

    assert state.executions[0].started_at is None
    assert assess_engine(state, "demo", now=NOW).verdict == "healthy"


def test_the_verdict_carries_per_job_evidence():
    state = cron_state(
        [job_payload(failure_streak=3, last_status="failed")],
        [incident_payload(state="alerted")],
    )

    verdict = assess_engine(state, "demo", now=NOW)
    evidence = verdict.jobs[0]

    assert evidence.job_id == "job-1"
    assert evidence.failure_streak == 3
    assert evidence.open_incident_ids == ("inc-1",)
    assert evidence.healthy is False


# --- session plan ----------------------------------------------------------


def plan(tmp_path: Path, **overrides) -> OperatorPlan:
    prompt = tmp_path / "OPERATOR-SESSION-PROMPT.md"
    prompt.write_text("repair the factory", encoding="utf-8")
    values = {
        "project": "demo",
        "profile": "factory-operator",
        "prompt": prompt,
        "repo": tmp_path / "HermesFactory",
        "budget": 60,
        "log_directory": tmp_path / "logs",
        "lock_directory": tmp_path / "locks",
    }
    values.update(overrides)
    return OperatorPlan(**values)


def test_session_argv_matches_the_documented_invocation(tmp_path: Path):
    assert build_session_argv(plan(tmp_path), hermes="hermes") == (
        "hermes",
        "-p",
        "factory-operator",
        "chat",
        "--query-file",
        str(tmp_path / "OPERATOR-SESSION-PROMPT.md"),
        "--oneshot",
        "-Q",
        "--run-budget",
        "60",
        "--in",
        str(tmp_path / "HermesFactory"),
    )


def test_lock_is_live_inside_the_quiet_window(tmp_path: Path):
    record = operator.LockRecord(
        path=tmp_path / "demo.lock.json",
        project="demo",
        started_at=int(NOW) - 60,
        incident_ids=("inc-1",),
        session_log=tmp_path / "session.log",
    )

    assert lock_is_live(
        record,
        now=NOW,
        quiet_seconds=900,
        max_session_seconds=7200,
        log_mtime=lambda path: NOW - 30,
    )
    assert not lock_is_live(
        record,
        now=NOW,
        quiet_seconds=900,
        max_session_seconds=7200,
        log_mtime=lambda path: NOW - 1000,
    )


def test_lock_is_not_live_beyond_the_maximum_session_age(tmp_path: Path):
    record = operator.LockRecord(
        path=tmp_path / "demo.lock.json",
        project="demo",
        started_at=int(NOW) - 9000,
        incident_ids=(),
        session_log=tmp_path / "session.log",
    )

    assert not lock_is_live(
        record,
        now=NOW,
        quiet_seconds=900,
        max_session_seconds=7200,
        log_mtime=lambda path: NOW - 1,
    )


def test_lock_round_trips_through_the_lock_file(tmp_path: Path):
    path = tmp_path / "demo.operator.lock.json"
    record = operator.LockRecord(
        path=path,
        project="demo",
        started_at=int(NOW),
        incident_ids=("inc-1",),
        session_log=tmp_path / "session.log",
    )

    operator.write_lock(record)

    assert read_lock(path) == record


# --- supervision -----------------------------------------------------------


class FakeSupervisor:
    def __init__(self, state: CronState, cron_directory: Path | None = None) -> None:
        self.state = state
        self.cron_directory_path = cron_directory or Path("unused")
        self.spawns: list[tuple[tuple[str, ...], Path]] = []

    def cron_state(self, profile: str) -> CronState:
        return self.state

    def cron_directory(self, profile: str) -> Path:
        return self.cron_directory_path

    def spawn(self, argv: tuple[str, ...], log_path: Path) -> int:
        self.spawns.append((argv, log_path))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("session started", encoding="utf-8")
        return 4242


def test_supervise_launches_one_session_and_records_a_live_lock(tmp_path: Path):
    supervisor = FakeSupervisor(
        cron_state(
            [job_payload(failure_streak=3, last_status="failed")],
            [incident_payload(state="alerted")],
        )
    )
    spec = plan(tmp_path)

    result = supervise(
        supervisor,
        spec,
        now=lambda: NOW,
        spawn=supervisor.spawn,
        log_mtime=lambda path: NOW,
    )

    assert result.launched is True
    assert result.reason == "launched_operator_session"
    assert len(supervisor.spawns) == 1
    assert supervisor.spawns[0][0] == build_session_argv(spec)
    lock = read_lock(spec.lock_path())
    assert lock is not None
    assert lock.project == "demo"
    assert lock.incident_ids == ("inc-1",)
    assert lock.session_log == result.log_path


def test_supervise_suppresses_a_second_session_while_one_is_live(tmp_path: Path):
    supervisor = FakeSupervisor(
        cron_state([job_payload(failure_streak=4, last_status="failed")])
    )
    spec = plan(tmp_path)
    first = supervise(
        supervisor,
        spec,
        now=lambda: NOW,
        spawn=supervisor.spawn,
        log_mtime=lambda path: NOW,
    )

    second = supervise(
        supervisor,
        spec,
        now=lambda: NOW,
        spawn=supervisor.spawn,
        log_mtime=lambda path: NOW,
    )

    assert first.launched is True
    assert second.launched is False
    assert second.reason == "operator_session_already_running"
    assert len(supervisor.spawns) == 1


def test_supervise_replaces_a_lock_left_by_a_dead_session(tmp_path: Path):
    supervisor = FakeSupervisor(
        cron_state([job_payload(failure_streak=4, last_status="failed")])
    )
    spec = plan(tmp_path)
    orphan = operator.LockRecord(
        path=spec.lock_path(),
        project="demo",
        started_at=int(NOW) - 5000,
        incident_ids=(),
        session_log=tmp_path / "logs" / "orphan.log",
    )
    operator.write_lock(orphan)

    result = supervise(
        supervisor,
        spec,
        now=lambda: NOW,
        spawn=supervisor.spawn,
        log_mtime=lambda path: NOW - 5000,
    )

    assert result.launched is True
    assert result.reason == "launched_operator_session"
    assert result.replaced_stale_lock is True
    assert len(supervisor.spawns) == 1


@pytest.mark.parametrize(
    "state,reason",
    [
        (cron_state([job_payload(last_status="ok", failure_streak=0)]), "healthy"),
        (cron_state([job_payload(failure_streak=1)]), "degraded"),
        (cron_state([job_payload(enabled=False, state="paused")]), "paused"),
        (cron_state([]), "unregistered"),
    ],
)
def test_supervise_never_launches_unless_the_engine_stopped(tmp_path: Path, state, reason):
    supervisor = FakeSupervisor(state)
    spec = plan(tmp_path)

    result = supervise(
        supervisor,
        spec,
        now=lambda: NOW,
        spawn=supervisor.spawn,
        log_mtime=lambda path: NOW,
    )

    assert result.launched is False
    assert result.reason == reason
    assert supervisor.spawns == []


def test_supervise_dry_run_never_spawns(tmp_path: Path):
    supervisor = FakeSupervisor(
        cron_state([job_payload(failure_streak=3, last_status="failed")])
    )
    spec = plan(tmp_path)

    result = supervise(
        supervisor,
        spec,
        now=lambda: NOW,
        spawn=supervisor.spawn,
        log_mtime=lambda path: NOW,
        dry_run=True,
    )

    assert result.launched is False
    assert result.reason == "dry_run"
    assert supervisor.spawns == []
    assert not spec.lock_path().exists()


def test_supervise_requires_an_existing_prompt_file(tmp_path: Path):
    supervisor = FakeSupervisor(
        cron_state([job_payload(failure_streak=3, last_status="failed")])
    )
    spec = plan(tmp_path, prompt=tmp_path / "missing.md")

    with pytest.raises(FactoryError):
        supervise(
            supervisor,
            spec,
            now=lambda: NOW,
            spawn=supervisor.spawn,
            log_mtime=lambda path: NOW,
        )


# --- CLI -------------------------------------------------------------------


def test_cli_assess_reports_the_verdict_as_json(tmp_path: Path, capsys):
    state = cron_state([job_payload(failure_streak=3, last_status="failed")])
    client = FakeSupervisor(state)

    code = main(
        ["operator", "assess", "--project", "demo", "--json"],
        client_factory=lambda executable: client,
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "stopped"
    assert payload["jobs"][0]["failure_streak"] == 3


def test_cli_supervise_dry_run_reports_without_spawning(tmp_path: Path, monkeypatch, capsys):
    state = cron_state([job_payload(failure_streak=3, last_status="failed")])
    client = FakeSupervisor(state, cron_directory=tmp_path / "cron")
    prompt = tmp_path / "OPERATOR-SESSION-PROMPT.md"
    prompt.write_text("repair the factory", encoding="utf-8")
    monkeypatch.setattr(
        "hermes_factory.operator.default_spawn",
        lambda argv, log_path: pytest.fail("the CLI must not spawn in dry-run mode"),
    )

    code = main(
        [
            "operator",
            "supervise",
            "--project",
            "demo",
            "--prompt",
            str(prompt),
            "--repo",
            str(tmp_path / "HermesFactory"),
            "--dry-run",
            "--json",
        ],
        client_factory=lambda executable: client,
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["launched"] is False
    assert payload["reason"] == "dry_run"
    assert payload["command"][:4] == ["hermes", "-p", "factory-operator", "chat"]


# --- client ----------------------------------------------------------------


def test_client_reads_the_profile_cron_state(tmp_path: Path):
    profile_directory = tmp_path / "profile"
    (profile_directory / "cron").mkdir(parents=True)
    (profile_directory / "config.yaml").write_text("key: value\n", encoding="utf-8")
    (profile_directory / "cron" / "jobs.json").write_text(
        json.dumps({"jobs": [job_payload()]}), encoding="utf-8"
    )
    connection = sqlite3.connect(profile_directory / "cron" / "executions.db")
    connection.execute(
        "create table cron_incidents (id text, job_id text, state text, failure_type text,"
        " first_seen_at text, last_seen_at text, error text, output_file text)"
    )
    connection.execute(
        "insert into cron_incidents values ('inc-1','job-1','alerted','script',?,?,?,null)",
        (STALE, RECENT, "Script exited with code 2"),
    )
    connection.execute(
        "create table executions (id text, job_id text, status text, started_at text,"
        " finished_at text)"
    )
    connection.execute(
        "insert into executions values ('exec-1','job-1','running',?,null)", (RECENT,)
    )
    connection.commit()
    connection.close()

    def fake_run(argv, **kwargs):
        assert argv == ["hermes", "config", "path"]
        return CompletedProcess(argv, 0, str(profile_directory / "config.yaml"), "")

    state = HermesClient(executable="hermes", runner=fake_run).cron_state("default")

    assert [item.id for item in state.jobs] == ["job-1"]
    assert [item.id for item in state.incidents] == ["inc-1"]
    assert [item.status for item in state.executions] == ["running"]


def test_client_reports_a_missing_cron_directory(tmp_path: Path):
    profile_directory = tmp_path / "profile"
    profile_directory.mkdir()
    (profile_directory / "config.yaml").write_text("key: value\n", encoding="utf-8")

    def fake_run(argv, **kwargs):
        return CompletedProcess(argv, 0, str(profile_directory / "config.yaml"), "")

    with pytest.raises(FactoryError):
        HermesClient(executable="hermes", runner=fake_run).cron_state("default")


def test_client_treats_a_profile_without_jobs_as_unregistered(tmp_path: Path):
    profile_directory = tmp_path / "profile"
    (profile_directory / "cron").mkdir(parents=True)
    (profile_directory / "config.yaml").write_text("key: value\n", encoding="utf-8")

    def fake_run(argv, **kwargs):
        return CompletedProcess(argv, 0, str(profile_directory / "config.yaml"), "")

    state = HermesClient(executable="hermes", runner=fake_run).cron_state("default")

    assert state.jobs == ()
    assert assess_engine(state, "demo", now=NOW).verdict == "unregistered"
