from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from hermes_factory.errors import SpecError
from hermes_factory.health import run_succeeded
from hermes_factory.rig_loader import validate_slug

_OPEN_STATUSES = {"ready", "running", "blocked"}
_FAILED_RUN_OUTCOMES = {"failed", "spawn_failed", "timed_out", "crashed"}
_DAY_SECONDS = 86_400
_MINUTES_PER_HOUR = 60
_PACKET_CLASSES = frozenset({"implementation", "review", "commit"})
_SLICE_CLASSES = _PACKET_CLASSES | frozenset({"decomposition"})
_DECOMPOSITION_RETRY_TAIL = ("decomposition", "retry", "1")


@dataclass(frozen=True, slots=True)
class BoardMetrics:
    totals: dict[str, int]
    done: int
    throughput_per_day: float | None
    proof_rate: float | None
    rework_rate: float | None
    changes_requested: int
    failed_runs: int
    total_runs: int
    median_lead_time_minutes: float | None
    max_lead_time_minutes: float | None
    open_work_by_seat: dict[str, int]
    oldest_ready_age_seconds: int | None
    decomposition_lead_time_minutes: float | None = None
    packets_per_architectural_slice: dict[str, int] | None = None
    builder_first_attempt_completion_rate: float | None = None
    progress_checkpoint_rate: float | None = None
    hard_timeout_rate_by_task_class: dict[str, float] | None = None
    median_packet_lead_time_minutes: float | None = None
    rework_rate_after_review: float | None = None
    time_to_first_command_minutes: float | None = None
    time_to_first_file_mutation_minutes: float | None = None
    action_latency_observation: str = "unavailable"


def _runs_for(runs_by_task: dict[str, list[dict[str, Any]]], task: dict[str, Any]) -> list[dict[str, Any]]:
    runs = runs_by_task.get(task.get("id"), [])
    return _ordered_runs([run for run in runs if isinstance(run, dict)])


def _has_proof(task: dict[str, Any], runs: list[dict[str, Any]]) -> bool:
    if task.get("result"):
        return True
    return any(run_succeeded(run) for run in runs)


def _lead_minutes(task: dict[str, Any]) -> float | None:
    created_at = task.get("created_at")
    completed_at = task.get("completed_at")
    if not isinstance(created_at, (int, float)) or not isinstance(completed_at, (int, float)):
        return None
    if completed_at < created_at:
        return None
    return (completed_at - created_at) / _MINUTES_PER_HOUR


def _is_slug(value: str) -> bool:
    """Whether a key segment is exactly the slug Factory validates segments against."""
    try:
        return validate_slug(value, "idempotency key segment") == value
    except SpecError:
        return False


def _key_parts(key: object) -> tuple[str, ...] | None:
    if not isinstance(key, str):
        return None
    parts = tuple(key.split(":"))
    if len(parts) < 2 or parts[0] != "factory" or any(not part for part in parts):
        return None
    return parts


def _key_identity(key: object) -> tuple[str, tuple[str, str] | None] | None:
    """The task class, and the packet identity, a trusted Factory key encodes.

    Only the exact shapes the compiler emits are trusted. A materialized packet is
    ``factory:<project>:<mission>:<slice>:packet:<packet id>:<class>`` and a slice
    unit is ``factory:<project>:<mission>:<slice>:<class>``, where the slice itself
    is its own single packet. The legacy six-segment packet key is still parsed.
    Every other key - a reconciler recovery card, a retry replacement, an
    acceptance card - is refused rather than counted as a packet.
    """
    parts = _key_parts(key)
    if parts is None:
        return None
    project, mission, slice_id = parts[1], parts[2], parts[3]
    if not (_is_slug(project) and _is_slug(mission) and _is_slug(slice_id)):
        return None
    if len(parts) == 4 and parts[3] == "acceptance":
        return "acceptance", None
    if len(parts) == 5 and parts[4] in _SLICE_CLASSES:
        task_class = parts[4]
        if task_class not in _PACKET_CLASSES:
            return task_class, None
        return task_class, (slice_id, slice_id)
    if len(parts) == 7 and parts[4] == "packet" and parts[6] in _PACKET_CLASSES:
        if _is_slug(parts[5]):
            return parts[6], (slice_id, parts[5])
        return None
    if len(parts) == 7 and parts[4:] == _DECOMPOSITION_RETRY_TAIL:
        return "decomposition", None
    if len(parts) == 6 and parts[5] in _PACKET_CLASSES:
        if _is_slug(parts[4]):
            return parts[5], (slice_id, parts[4])
        return None
    return None


def _task_class(task: dict[str, Any]) -> str:
    metadata = task.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("task_class"), str):
        return metadata["task_class"]
    identity = _key_identity(task.get("idempotency_key"))
    if identity is None:
        return "unknown"
    return identity[0]


def _packet_identity(task: dict[str, Any]) -> tuple[str, str, str] | None:
    identity = _key_identity(task.get("idempotency_key"))
    if identity is None or identity[1] is None:
        return None
    slice_id, packet_id = identity[1]
    return slice_id, packet_id, identity[0]


def _ended_at(run: dict[str, Any]) -> float | None:
    value = run.get("ended_at")
    return float(value) if isinstance(value, (int, float)) else None


def _started_at(run: dict[str, Any]) -> float | None:
    value = run.get("started_at")
    return float(value) if isinstance(value, (int, float)) else None


def _is_checkpoint(run: dict[str, Any]) -> bool:
    return any(
        isinstance(run.get(field), str)
        and run[field].startswith("Factory progress checkpoint:")
        for field in ("summary", "error")
    )


def _is_terminal_run(run: dict[str, Any]) -> bool:
    return _ended_at(run) is not None or run.get("status") not in {None, "running"}


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _run_order_key(run: dict[str, Any]) -> tuple[int, float, int, float, int, float, str]:
    """Authoritative attempt order from the run schema: attempt, start, then id.

    Attempts are never ordered by the order their rows arrived in. A run that
    recorded neither an attempt index nor a start time sorts last, so it can never
    be mistaken for the first builder attempt.
    """
    attempt = _numeric(run.get("attempt"))
    started_at = _started_at(run)
    identifier = run.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, (int, str)):
        id_key: tuple[int, float, str] = (2, 0.0, "")
    elif isinstance(identifier, int):
        id_key = (0, float(identifier), "")
    else:
        id_key = (1, 0.0, identifier)
    return (
        attempt is None,
        attempt or 0.0,
        started_at is None,
        started_at or 0.0,
        *id_key,
    )


def _ordered_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(runs, key=_run_order_key)


def compute_metrics(
    tasks: list[dict[str, Any]],
    runs_by_task: dict[str, list[dict[str, Any]]],
    stats: dict[str, Any],
    *,
    proof_by_task: dict[str, bool] | None = None,
) -> BoardMetrics:
    tasks = [task for task in tasks if isinstance(task, dict)]
    now = stats.get("now")

    totals: dict[str, int] = {}
    open_work_by_seat: dict[str, int] = {}
    done_tasks: list[dict[str, Any]] = []
    lead_times: list[float] = []
    proofed: int = 0
    reworked: int = 0
    changes_requested = 0
    failed_runs = 0
    total_runs = 0

    for runs in runs_by_task.values():
        if not isinstance(runs, list):
            continue
        for run in runs:
            if not isinstance(run, dict):
                continue
            total_runs += 1
            if run.get("status") in _FAILED_RUN_OUTCOMES or run.get("outcome") in _FAILED_RUN_OUTCOMES:
                failed_runs += 1
            if run.get("outcome") == "changes_requested":
                changes_requested += 1

    for task in tasks:
        status = task.get("status") or "unknown"
        totals[status] = totals.get(status, 0) + 1
        assignee = task.get("assignee") or "unassigned"
        if status in _OPEN_STATUSES:
            open_work_by_seat[assignee] = open_work_by_seat.get(assignee, 0) + 1
        runs = _runs_for(runs_by_task, task)
        if status == "done":
            done_tasks.append(task)
            default_proof = _has_proof(task, runs)
            if (
                proof_by_task.get(task.get("id"), default_proof)
                if proof_by_task is not None
                else default_proof
            ):
                proofed += 1
            if len(runs) > 1:
                reworked += 1
            lead = _lead_minutes(task)
            if lead is not None:
                lead_times.append(lead)

    done = len(done_tasks)
    throughput_per_day: float | None = None
    if done and isinstance(now, (int, float)):
        created_stamps = [
            task["created_at"]
            for task in done_tasks
            if isinstance(task.get("created_at"), (int, float))
        ]
        if created_stamps:
            window_seconds = now - min(created_stamps)
            if window_seconds > 0:
                throughput_per_day = round(done / (window_seconds / _DAY_SECONDS), 4)

    proof_rate: float | None = None
    rework_rate: float | None = None
    if done:
        proof_rate = proofed / done
        rework_rate = reworked / done

    median_lead: float | None = round(median(lead_times), 2) if lead_times else None
    max_lead: float | None = round(max(lead_times), 2) if lead_times else None
    oldest_ready_age = stats.get("oldest_ready_age_seconds")
    if not isinstance(oldest_ready_age, int):
        oldest_ready_age = None

    tasks_by_packet: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    packets_per_slice: dict[str, set[str]] = {}
    decomposition_leads: list[float] = []
    terminal_runs_by_class: dict[str, int] = {}
    hard_timeouts_by_class: dict[str, int] = {}
    builder_first_attempts = 0
    builder_first_attempt_completions = 0
    checkpoint_opportunities = 0
    checkpointed = 0

    for task in tasks:
        task_class = _task_class(task)
        runs = _runs_for(runs_by_task, task)
        if task_class == "decomposition":
            lead = _lead_minutes(task)
            if lead is not None:
                decomposition_leads.append(lead)
        packet_identity = _packet_identity(task)
        if packet_identity is not None:
            slice_id, packet_id, packet_class = packet_identity
            packets_per_slice.setdefault(slice_id, set()).add(packet_id)
            tasks_by_packet.setdefault((slice_id, packet_id), {})[packet_class] = task
            if packet_class == "implementation" and runs:
                builder_first_attempts += 1
                if run_succeeded(runs[0]):
                    builder_first_attempt_completions += 1
                unsuccessful_attempts = [
                    run
                    for run in runs
                    if not run_succeeded(run) and _is_terminal_run(run)
                ]
                checkpoint_opportunities += len(unsuccessful_attempts)
                checkpointed += sum(
                    1 for run in unsuccessful_attempts if _is_checkpoint(run)
                )
        for run in runs:
            if not _is_terminal_run(run):
                continue
            terminal_runs_by_class[task_class] = terminal_runs_by_class.get(task_class, 0) + 1
            if run.get("status") == "timed_out" or run.get("outcome") == "timed_out":
                hard_timeouts_by_class[task_class] = hard_timeouts_by_class.get(task_class, 0) + 1

    packet_leads: list[float] = []
    reviewed_packets = 0
    reworked_after_review = 0
    for packet_tasks in tasks_by_packet.values():
        implementation = packet_tasks.get("implementation")
        terminal = packet_tasks.get("commit") or packet_tasks.get("review")
        if implementation is not None and terminal is not None:
            created_at = implementation.get("created_at")
            completed_at = terminal.get("completed_at")
            if (
                isinstance(created_at, (int, float))
                and isinstance(completed_at, (int, float))
                and completed_at >= created_at
            ):
                packet_leads.append((completed_at - created_at) / _MINUTES_PER_HOUR)

        review = packet_tasks.get("review")
        if implementation is None or review is None:
            continue
        implementation_runs = _runs_for(runs_by_task, implementation)
        review_observations = [
            run for run in _runs_for(runs_by_task, review) if _is_terminal_run(run)
        ]
        if not review_observations:
            continue
        reviewed_packets += 1
        requested_at = [
            ended
            for run in review_observations
            if run.get("outcome") == "changes_requested"
            and (ended := _ended_at(run)) is not None
        ]
        if requested_at and any(
            (started := _started_at(run)) is not None and started > min(requested_at)
            for run in implementation_runs
        ):
            reworked_after_review += 1

    hard_timeout_rates = {
        task_class: hard_timeouts_by_class.get(task_class, 0) / count
        for task_class, count in sorted(terminal_runs_by_class.items())
    }

    return BoardMetrics(
        totals=totals,
        done=done,
        throughput_per_day=throughput_per_day,
        proof_rate=proof_rate,
        rework_rate=rework_rate,
        changes_requested=changes_requested,
        failed_runs=failed_runs,
        total_runs=total_runs,
        median_lead_time_minutes=median_lead,
        max_lead_time_minutes=max_lead,
        open_work_by_seat=open_work_by_seat,
        oldest_ready_age_seconds=oldest_ready_age,
        decomposition_lead_time_minutes=(
            round(median(decomposition_leads), 2) if decomposition_leads else None
        ),
        packets_per_architectural_slice={
            slice_id: len(packet_ids)
            for slice_id, packet_ids in sorted(packets_per_slice.items())
        },
        builder_first_attempt_completion_rate=(
            builder_first_attempt_completions / builder_first_attempts
            if builder_first_attempts
            else None
        ),
        progress_checkpoint_rate=(
            checkpointed / checkpoint_opportunities if checkpoint_opportunities else None
        ),
        hard_timeout_rate_by_task_class=hard_timeout_rates,
        median_packet_lead_time_minutes=(
            round(median(packet_leads), 2) if packet_leads else None
        ),
        rework_rate_after_review=(
            reworked_after_review / reviewed_packets if reviewed_packets else None
        ),
    )
