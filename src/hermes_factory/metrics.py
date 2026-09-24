from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

_OPEN_STATUSES = {"ready", "running", "blocked"}
_FAILED_RUN_OUTCOMES = {"failed", "spawn_failed", "timed_out", "crashed"}
_DAY_SECONDS = 86_400
_MINUTES_PER_HOUR = 60


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


def _runs_for(runs_by_task: dict[str, list[dict[str, Any]]], task: dict[str, Any]) -> list[dict[str, Any]]:
    runs = runs_by_task.get(task.get("id"), [])
    return [run for run in runs if isinstance(run, dict)]


def _has_proof(task: dict[str, Any], runs: list[dict[str, Any]]) -> bool:
    if task.get("result"):
        return True
    for run in runs:
        if run.get("status") == "succeeded" or run.get("outcome") == "success":
            return True
    return False


def _lead_minutes(task: dict[str, Any]) -> float | None:
    created_at = task.get("created_at")
    completed_at = task.get("completed_at")
    if not isinstance(created_at, (int, float)) or not isinstance(completed_at, (int, float)):
        return None
    if completed_at < created_at:
        return None
    return (completed_at - created_at) / _MINUTES_PER_HOUR


def compute_metrics(
    tasks: list[dict[str, Any]],
    runs_by_task: dict[str, list[dict[str, Any]]],
    stats: dict[str, Any],
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
            if _has_proof(task, runs):
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
    )
