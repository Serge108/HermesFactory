"""External supervision of a Factory watchdog loop.

The Factory repairs tasks. Nothing inside the Factory repairs the Factory: a board
card can only be created by the code that is already failing, so an engine defect
makes every reconcile pass fail with no card to show for it. This module supplies
the external half of that split.

Every input is read from a source outside the board: the Hermes cron job list and
the durable cron failure incidents. Both exist whether or not the Factory is able
to create a card, which is the property the trigger depends on.

The module never mutates Kanban state. Its only side effects are one lock file and
one detached operator session, and the lock is what keeps a failing engine from
launching a session on every tick.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from hermes_factory.errors import FactoryError

OPERATOR_JOB_MARKER = "Hermes Factory operator:"

ENGINE_LOCK_NAME = "engine.operator.lock.json"

DEFAULT_FAILURE_STREAK_THRESHOLD = 3
DEFAULT_STALE_AFTER_SECONDS = 600
DEFAULT_LOCK_QUIET_SECONDS = 900
DEFAULT_MAX_SESSION_SECONDS = 7200
DEFAULT_SESSION_BUDGET = 3600

HEALTHY = "healthy"
DEGRADED = "degraded"
STOPPED = "stopped"
PAUSED = "paused"
UNREGISTERED = "unregistered"

REASON_PAUSED = "watchdog_paused_by_operator"
REASON_STREAK = "failure_streak_reached_threshold"
REASON_BELOW_THRESHOLD = "failure_streak_below_threshold"
REASON_NOT_TICKING = "watchdog_not_ticking"
REASON_UNREADABLE_RUN = "last_run_unreadable"

_SEVERITY = {HEALTHY: 0, DEGRADED: 1, STOPPED: 2, PAUSED: 3}
_REASON_CLASS = {
    REASON_PAUSED: PAUSED,
    REASON_STREAK: STOPPED,
    REASON_NOT_TICKING: STOPPED,
    REASON_BELOW_THRESHOLD: DEGRADED,
    REASON_UNREADABLE_RUN: DEGRADED,
}


@dataclass(frozen=True, slots=True)
class CronJob:
    id: str
    name: str
    script: str | None
    enabled: bool
    state: str
    last_status: str | None
    last_error: str | None
    failure_streak: int
    workdir: str | None
    last_run_at: str | None


@dataclass(frozen=True, slots=True)
class CronIncident:
    id: str
    job_id: str
    state: str
    failure_type: str | None
    first_seen_at: str
    last_seen_at: str
    error: str | None
    output_file: str | None


@dataclass(frozen=True, slots=True)
class CronExecution:
    id: str
    job_id: str
    status: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class CronState:
    jobs: tuple[CronJob, ...]
    incidents: tuple[CronIncident, ...]
    executions: tuple[CronExecution, ...] = ()
    cron_directory: Path | None = None


@dataclass(frozen=True, slots=True)
class JobVerdict:
    job_id: str
    name: str
    failure_streak: int
    last_status: str | None
    open_incident_ids: tuple[str, ...]
    reason: str
    healthy: bool


@dataclass(frozen=True, slots=True)
class EngineVerdict:
    verdict: str
    project: str
    observed_at: int
    reasons: tuple[str, ...]
    jobs: tuple[JobVerdict, ...]

    def launches(self) -> bool:
        return self.verdict == STOPPED


@dataclass(frozen=True, slots=True)
class LockRecord:
    path: Path
    project: str
    started_at: int
    incident_ids: tuple[str, ...]
    session_log: Path | None
    heartbeat_at: int | None = None


@dataclass(frozen=True, slots=True)
class OperatorPlan:
    project: str
    profile: str
    prompt: Path
    repo: Path
    budget: int
    log_directory: Path
    lock_directory: Path

    def lock_path(self) -> Path:
        return self.lock_directory / project_lock_name(self.project)

    def engine_lock_path(self) -> Path:
        """One engine, several rigs: the lock every project's tick consults."""
        return engine_lock_path(self.lock_directory)

    def session_log_path(self, started_at: int) -> Path:
        return self.log_directory / f"{self.project}-operator-{started_at}.log"


def project_lock_name(project: str) -> str:
    return f"{project}.operator.lock.json"


def engine_lock_path(lock_directory: Path) -> Path:
    """The engine-scoped lock sits one directory above the per-project lock."""
    return lock_directory.parent / ENGINE_LOCK_NAME


@dataclass(frozen=True, slots=True)
class SuperviseResult:
    verdict: EngineVerdict
    launched: bool
    reason: str
    lock_path: Path
    log_path: Path | None
    command: tuple[str, ...]
    replaced_stale_lock: bool = False
    engine_lock_path: Path | None = None
    engine_holder: str | None = None


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FactoryError(f"Hermes cron {field} is not text")
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FactoryError(f"Hermes cron {field} is missing")
    return value


def parse_cron_jobs(payload: Any) -> tuple[CronJob, ...]:
    if not isinstance(payload, dict):
        raise FactoryError("Hermes cron job payload is not an object")
    entries = payload.get("jobs")
    if not isinstance(entries, list):
        raise FactoryError("Hermes cron job payload has no job list")
    jobs = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise FactoryError("Hermes cron job entry is not an object")
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise FactoryError("Hermes cron job enabled flag is not a boolean")
        state = entry.get("state", "")
        if not isinstance(state, str):
            raise FactoryError("Hermes cron job state is not text")
        streak = entry.get("failure_streak", 0)
        if isinstance(streak, bool) or not isinstance(streak, int):
            raise FactoryError("Hermes cron job failure streak is not an integer")
        jobs.append(
            CronJob(
                id=_text(entry.get("id"), "job id"),
                name=_text(entry.get("name"), "job name"),
                script=_optional_text(entry.get("script"), "job script"),
                enabled=enabled,
                state=state,
                last_status=_optional_text(entry.get("last_status"), "job status"),
                last_error=_optional_text(entry.get("last_error"), "job error"),
                failure_streak=streak,
                workdir=_optional_text(entry.get("workdir"), "job workdir"),
                last_run_at=_optional_text(entry.get("last_run_at"), "job last run"),
            )
        )
    return tuple(jobs)


def parse_cron_incidents(rows: Sequence[Mapping[str, Any]]) -> tuple[CronIncident, ...]:
    incidents = []
    for row in rows:
        record = dict(row)
        incidents.append(
            CronIncident(
                id=_text(record.get("id"), "incident id"),
                job_id=_text(record.get("job_id"), "incident job id"),
                state=_text(record.get("state"), "incident state"),
                failure_type=_optional_text(record.get("failure_type"), "incident failure type"),
                first_seen_at=_text(record.get("first_seen_at"), "incident first seen"),
                last_seen_at=_text(record.get("last_seen_at"), "incident last seen"),
                error=_optional_text(record.get("error"), "incident error"),
                output_file=_optional_text(record.get("output_file"), "incident output file"),
            )
        )
    return tuple(incidents)


def parse_cron_executions(rows: Sequence[Mapping[str, Any]]) -> tuple[CronExecution, ...]:
    executions = []
    for row in rows:
        record = dict(row)
        executions.append(
            CronExecution(
                id=_text(record.get("id"), "execution id"),
                job_id=_text(record.get("job_id"), "execution job id"),
                status=_text(record.get("status"), "execution status"),
                started_at=_optional_text(record.get("started_at"), "execution start"),
                finished_at=_optional_text(record.get("finished_at"), "execution finish"),
            )
        )
    return tuple(executions)


def is_operator_job(job: CronJob) -> bool:
    """A supervision job must never be bound to the project it supervises."""
    return job.name.startswith(OPERATOR_JOB_MARKER)


def _workdir_project(workdir: str | None) -> str | None:
    if workdir is None:
        return None
    parts = [part for part in workdir.replace("\\", "/").split("/") if part]
    return parts[-1] if parts else None


def bound_jobs(
    state: CronState,
    project: str,
    *,
    job_ids: Sequence[str] = (),
) -> tuple[CronJob, ...]:
    by_id = {job.id: job for job in state.jobs}
    if job_ids:
        selected = []
        for job_id in job_ids:
            job = by_id.get(job_id)
            if job is None:
                raise FactoryError(f"Hermes cron has no job {job_id}")
            if is_operator_job(job):
                raise FactoryError(f"job {job_id} is an operator supervision job")
            selected.append(job)
        return tuple(selected)
    suffix = f": {project}"
    return tuple(
        job
        for job in state.jobs
        if not is_operator_job(job)
        and (job.name.endswith(suffix) or _workdir_project(job.workdir) == project)
    )


def _open_incidents(state: CronState, job_id: str) -> tuple[str, ...]:
    return tuple(
        incident.id
        for incident in state.incidents
        if incident.job_id == job_id and incident.state in ("detected", "alerted")
    )


def _open_incident_ids(verdict: EngineVerdict) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            incident_id
            for item in verdict.jobs
            for incident_id in item.open_incident_ids
        )
    )


def _epoch(timestamp: str | None) -> float | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromisoformat(timestamp).timestamp()
    except ValueError:
        return None


def _job_activity(state: CronState, job_id: str) -> tuple[bool, float | None]:
    """Return whether the job is running now and its newest authoritative activity.

    ``jobs.json`` records ``last_run_at`` only when a run finishes, so a job with a
    long pass looks stale while it is working. The execution rows carry the start
    time, which is the signal that proves the loop is alive.
    """
    running = False
    newest: float | None = None
    for execution in state.executions:
        if execution.job_id != job_id:
            continue
        if execution.status == "running":
            running = True
        for stamp in (execution.finished_at, execution.started_at):
            value = _epoch(stamp)
            if value is not None and (newest is None or value > newest):
                newest = value
    return running, newest


def _job_reason(
    job: CronJob,
    *,
    running: bool,
    newest_activity: float | None,
    threshold: int,
    stale_after: int,
    now: float,
) -> str:
    if not job.enabled or job.state != "scheduled":
        return REASON_PAUSED
    if job.failure_streak >= threshold:
        return REASON_STREAK
    if job.last_status == "failed":
        return REASON_BELOW_THRESHOLD
    if running:
        return HEALTHY
    stamps = [value for value in (_epoch(job.last_run_at), newest_activity) if value is not None]
    if not stamps:
        return REASON_UNREADABLE_RUN
    if now - max(stamps) >= stale_after:
        return REASON_NOT_TICKING
    return HEALTHY


def assess_engine(
    state: CronState,
    project: str,
    *,
    job_ids: Sequence[str] = (),
    threshold: int = DEFAULT_FAILURE_STREAK_THRESHOLD,
    stale_after: int = DEFAULT_STALE_AFTER_SECONDS,
    now: float | None = None,
) -> EngineVerdict:
    """Classify the engine from cron evidence alone.

    ``paused`` outranks every other verdict: pausing the watchdog is how an
    operator takes the loop down for a repair, so a paused job is declared intent
    rather than a stop condition.
    """
    observed = int(time.time() if now is None else now)
    jobs = bound_jobs(state, project, job_ids=job_ids)
    if not jobs:
        return EngineVerdict(
            UNREGISTERED, project, observed, ("no_watchdog_job_for_project",), ()
        )
    verdicts = []
    for job in jobs:
        running, newest_activity = _job_activity(state, job.id)
        reason = _job_reason(
            job,
            running=running,
            newest_activity=newest_activity,
            threshold=threshold,
            stale_after=stale_after,
            now=observed,
        )
        verdicts.append(
            JobVerdict(
                job_id=job.id,
                name=job.name,
                failure_streak=job.failure_streak,
                last_status=job.last_status,
                open_incident_ids=_open_incidents(state, job.id),
                reason=reason,
                healthy=reason == HEALTHY,
            )
        )
    classes = [_REASON_CLASS.get(item.reason, HEALTHY) for item in verdicts]
    worst = max(classes, key=lambda value: _SEVERITY[value])
    reasons = tuple(
        dict.fromkeys(
            item.reason
            for item, value in zip(verdicts, classes, strict=True)
            if value == worst
        )
    )
    return EngineVerdict(worst, project, observed, reasons, tuple(verdicts))


def _lock_payload(record: LockRecord) -> dict[str, Any]:
    return {
        "project": record.project,
        "started_at": record.started_at,
        "incident_ids": list(record.incident_ids),
        "session_log": None if record.session_log is None else str(record.session_log),
        "heartbeat_at": record.heartbeat_at,
    }


def write_lock(record: LockRecord) -> None:
    record.path.parent.mkdir(parents=True, exist_ok=True)
    try:
        record.path.write_text(
            json.dumps(_lock_payload(record), indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise FactoryError(f"failed to write operator lock {record.path}") from exc


def acquire_lock(record: LockRecord) -> bool:
    """Create a lock exclusively, so exactly one tick can win the race.

    The engine-scoped lock is taken this way: acquisition never waits, so a
    supervisor that loses the race declines to launch instead of blocking on the
    winner, and two ticks cannot both decide they hold the engine.
    """
    record.path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(record.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    except OSError as exc:
        raise FactoryError(f"failed to write operator lock {record.path}") from exc
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(_lock_payload(record), indent=2))
    except OSError as exc:
        raise FactoryError(f"failed to write operator lock {record.path}") from exc
    return True


def read_lock(path: Path) -> LockRecord | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactoryError(f"operator lock {path} is unreadable") from exc
    if not isinstance(payload, dict):
        raise FactoryError(f"operator lock {path} is malformed")
    started_at = payload.get("started_at")
    if isinstance(started_at, bool) or not isinstance(started_at, int):
        raise FactoryError(f"operator lock {path} has no start time")
    incident_ids = payload.get("incident_ids", [])
    if not isinstance(incident_ids, list) or any(
        not isinstance(item, str) for item in incident_ids
    ):
        raise FactoryError(f"operator lock {path} has malformed incident ids")
    session_log = payload.get("session_log")
    if session_log is not None and not isinstance(session_log, str):
        raise FactoryError(f"operator lock {path} has a malformed session log")
    heartbeat_at = payload.get("heartbeat_at")
    if heartbeat_at is not None and (
        isinstance(heartbeat_at, bool) or not isinstance(heartbeat_at, int)
    ):
        raise FactoryError(f"operator lock {path} has a malformed heartbeat")
    return LockRecord(
        path=path,
        project=_text(payload.get("project"), "operator lock project"),
        started_at=started_at,
        incident_ids=tuple(incident_ids),
        session_log=None if session_log is None else Path(session_log),
        heartbeat_at=heartbeat_at,
    )


def release_lock(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise FactoryError(f"failed to remove operator lock {path}") from exc
    return True


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:  # pragma: no cover - resolve is best effort on both paths
        return os.path.normcase(str(left)) == os.path.normcase(str(right))


def terminal_record_path(lock: LockRecord) -> Path | None:
    """The record the session contract requires before it exits.

    ``templates/operator-session-prompt.md`` has the session write
    ``<operator root>/records/<stem of its own log>.json``. That file is the one
    terminal signal the circuit can read, so it ends the session's claim early
    instead of making the next tick wait out the whole session budget.
    """
    if lock.session_log is None:
        return None
    return lock.path.parent / "records" / f"{lock.session_log.stem}.json"


def session_finished(lock: LockRecord) -> bool:
    path = terminal_record_path(lock)
    return path is not None and path.is_file()


def lock_is_live(
    record: LockRecord,
    *,
    now: float,
    quiet_seconds: int,
    max_session_seconds: int,
    finished: bool = False,
) -> bool:
    """Decide whether a running operator session owns the lock.

    Two signals are deliberately not trusted.

    Process liveness is not probed: on Windows ``os.kill(pid, 0)`` raises for a live
    process and returns quietly for a dead one, so it cannot distinguish them.

    The session's own log is not a liveness signal either. ``build_session_argv``
    passes ``-Q``, so the session's stdout is block-buffered and its log file can stay
    empty for the whole session: the session recorded as
    ``mechanoids-simulator-operator-1790714123`` still had a 0-byte log 2579 s in,
    while it was working. Reading that silence as death released the lock 15 minutes
    into a session whose budget was two hours, and only the paused watchdog kept a
    second session from starting over the first. The lock is therefore the session's
    claim for its whole ``max_session_seconds`` budget.

    ``finished`` is the terminal record, when the session wrote one.

    ``quiet_seconds`` applies to a session that opted into it by writing a heartbeat:
    the supervisor calls that lock dead once the heartbeat stops, which is how a
    crashed heartbeat session is recovered in minutes instead of at its budget.
    """
    if finished:
        return False
    if now - record.started_at >= max_session_seconds:
        return False
    if record.heartbeat_at is None:
        return True
    return (now - record.heartbeat_at) < quiet_seconds


def heartbeat_lock(path: Path, *, session_log: Path, now: float) -> bool:
    """Refresh the heartbeat of the lock that owns ``session_log``.

    Keyed on the session's own log rather than its project name: a session whose
    lock was replaced by a newer one must not be able to keep its replacement alive.
    """
    record = read_lock(path)
    if record is None or record.session_log is None:
        return False
    if not _same_path(record.session_log, session_log):
        return False
    write_lock(replace(record, heartbeat_at=int(now)))
    return True


def build_session_argv(plan: OperatorPlan, *, hermes: str = "hermes") -> tuple[str, ...]:
    """Build the detached operator session command.

    ``--run-budget`` is a wall-clock ceiling in seconds, not a step count, and
    ``-Q`` keeps the session log readable because the process has no terminal.
    """
    return (
        hermes,
        "-p",
        plan.profile,
        "chat",
        "--query-file",
        str(plan.prompt),
        "--oneshot",
        "-Q",
        "--run-budget",
        str(plan.budget),
        "--in",
        str(plan.repo),
    )


def default_spawn(argv: Sequence[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"shell": False, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
    else:
        kwargs["start_new_session"] = True
    with open(log_path, "a", encoding="utf-8") as handle:
        process = subprocess.Popen(list(argv), stdout=handle, stderr=subprocess.STDOUT, **kwargs)
    return process.pid


def supervise(
    client: Any,
    plan: OperatorPlan,
    *,
    hermes: str = "hermes",
    cron_profile: str = "default",
    job_ids: Sequence[str] = (),
    threshold: int = DEFAULT_FAILURE_STREAK_THRESHOLD,
    stale_after: int = DEFAULT_STALE_AFTER_SECONDS,
    quiet_seconds: int = DEFAULT_LOCK_QUIET_SECONDS,
    max_session_seconds: int = DEFAULT_MAX_SESSION_SECONDS,
    now: Callable[[], float] = time.time,
    spawn: Callable[[Sequence[str], Path], int] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> SuperviseResult:
    """Assess one external watchdog loop and start a repair session if it stopped.

    ``force`` starts the session whatever the verdict says, which is how the engine's
    owner assigns a task the watchdog did not raise. Everything else, the locks and
    the spawn, is the identical path, so an assigned session is recorded exactly like
    a watchdog one.

    Two locks gate a launch. ``<project>.operator.lock.json`` is the project's own
    idempotency record: while a session holds it, later ticks for that project launch
    nothing. ``engine.operator.lock.json``, one directory up, is the engine's
    serialization gate: the operator edits one repository and pauses one loop, so two
    rigs failing at once must not put two sessions on the same checkout. Neither is
    waited on, so a tick that loses the race declines to launch and cannot deadlock.
    """
    if not plan.prompt.is_file():
        raise FactoryError(f"operator session prompt is missing: {plan.prompt}")
    spawn_session = default_spawn if spawn is None else spawn
    observed = now()
    state = client.cron_state(cron_profile)
    verdict = assess_engine(
        state,
        plan.project,
        job_ids=job_ids,
        threshold=threshold,
        stale_after=stale_after,
        now=observed,
    )
    lock_path = plan.lock_path()
    engine_lock_path = plan.engine_lock_path()
    replaced = False
    existing = read_lock(lock_path)
    if existing is not None:
        if lock_is_live(
            existing,
            now=observed,
            quiet_seconds=quiet_seconds,
            max_session_seconds=max_session_seconds,
            finished=session_finished(existing),
        ):
            return SuperviseResult(
                verdict,
                False,
                "operator_session_already_running",
                lock_path,
                existing.session_log,
                (),
                False,
                engine_lock_path,
                plan.project,
            )
        replaced = release_lock(lock_path)
    holder = read_lock(engine_lock_path)
    if holder is not None:
        if lock_is_live(
            holder,
            now=observed,
            quiet_seconds=quiet_seconds,
            max_session_seconds=max_session_seconds,
            finished=session_finished(holder),
        ):
            return SuperviseResult(
                verdict,
                False,
                "engine_session_already_running",
                lock_path,
                holder.session_log,
                (),
                replaced,
                engine_lock_path,
                holder.project,
            )
        release_lock(engine_lock_path)
    if not force and not verdict.launches():
        return SuperviseResult(
            verdict, False, verdict.verdict, lock_path, None, (), replaced, engine_lock_path
        )
    argv = build_session_argv(plan, hermes=hermes)
    if dry_run:
        return SuperviseResult(
            verdict, False, "dry_run", lock_path, None, argv, replaced, engine_lock_path
        )
    started_at = int(observed)
    log_path = plan.session_log_path(started_at)
    incident_ids = _open_incident_ids(verdict)
    if not acquire_lock(
        LockRecord(
            path=engine_lock_path,
            project=plan.project,
            started_at=started_at,
            incident_ids=incident_ids,
            session_log=log_path,
        )
    ):
        return SuperviseResult(
            verdict,
            False,
            "engine_session_already_running",
            lock_path,
            None,
            (),
            replaced,
            engine_lock_path,
            None,
        )
    spawn_session(argv, log_path)
    write_lock(
        LockRecord(
            path=lock_path,
            project=plan.project,
            started_at=started_at,
            incident_ids=incident_ids,
            session_log=log_path,
        )
    )
    reason = "launched_assigned_session" if force else "launched_operator_session"
    return SuperviseResult(
        verdict, True, reason, lock_path, log_path, argv, replaced, engine_lock_path
    )


def verdict_payload(verdict: EngineVerdict) -> dict[str, Any]:
    return {
        "verdict": verdict.verdict,
        "project": verdict.project,
        "observed_at": verdict.observed_at,
        "reasons": list(verdict.reasons),
        "launches_operator_session": verdict.launches(),
        "jobs": [
            {
                "job_id": item.job_id,
                "name": item.name,
                "failure_streak": item.failure_streak,
                "last_status": item.last_status,
                "open_incident_ids": list(item.open_incident_ids),
                "reason": item.reason,
                "healthy": item.healthy,
            }
            for item in verdict.jobs
        ],
    }


def supervise_payload(result: SuperviseResult) -> dict[str, Any]:
    payload = verdict_payload(result.verdict)
    payload.update(
        {
            "launched": result.launched,
            "reason": result.reason,
            "lock": str(result.lock_path),
            "engine_lock": (
                None if result.engine_lock_path is None else str(result.engine_lock_path)
            ),
            "engine_holder": result.engine_holder,
            "log": None if result.log_path is None else str(result.log_path),
            "command": list(result.command),
            "replaced_stale_lock": result.replaced_stale_lock,
        }
    )
    return payload
