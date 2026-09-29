from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hermes_factory import __version__
from hermes_factory.board import apply_mission, ensure_board
from hermes_factory.compiler import compile_mission
from hermes_factory.errors import FactoryError, HermesCommandError
from hermes_factory.health import audit_tasks, build_proof_map, verify_git_commit
from hermes_factory.hermes_cli import HermesClient
from hermes_factory.metrics import compute_metrics
from hermes_factory.mission_loader import load_mission
from hermes_factory.models import RigSpec
from hermes_factory.operator import (
    DEFAULT_FAILURE_STREAK_THRESHOLD,
    DEFAULT_LOCK_QUIET_SECONDS,
    DEFAULT_MAX_SESSION_SECONDS,
    DEFAULT_SESSION_BUDGET,
    DEFAULT_STALE_AFTER_SECONDS,
    OperatorPlan,
    assess_engine,
    engine_lock_path,
    heartbeat_lock,
    project_lock_name,
    supervise,
    supervise_payload,
    verdict_payload,
)
from hermes_factory.reconcile import reconcile_board
from hermes_factory.rig_loader import load_rig
from hermes_factory.stall_taxonomy import stall_taxonomy

ClientFactory = Callable[[str], HermesClient]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes-factory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    version = subparsers.add_parser("version")
    version.set_defaults(handler_key="version")
    validate = subparsers.add_parser("validate")
    validate.add_argument("--rig", type=Path, required=True)
    validate.add_argument("--hermes", default="hermes")
    validate.set_defaults(handler_key="validate")
    apply = subparsers.add_parser("apply")
    apply.add_argument("--rig", type=Path, required=True)
    apply.add_argument("--hermes", default="hermes")
    apply.set_defaults(handler_key="apply")
    mission = subparsers.add_parser("mission")
    mission_subparsers = mission.add_subparsers(dest="mission_command", required=True)
    plan = mission_subparsers.add_parser("plan")
    plan.add_argument("mission", type=Path)
    plan.add_argument("--rig", type=Path, required=True)
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--hermes", default="hermes")
    plan.set_defaults(handler_key="mission-plan")
    mission_apply = mission_subparsers.add_parser("apply")
    mission_apply.add_argument("mission", type=Path)
    mission_apply.add_argument("--rig", type=Path, required=True)
    mission_apply.add_argument("--json", action="store_true")
    mission_apply.add_argument("--hermes", default="hermes")
    mission_apply.set_defaults(handler_key="mission-apply")
    status = subparsers.add_parser("status")
    status.add_argument("--rig", type=Path, required=True)
    status.add_argument("--json", action="store_true")
    status.add_argument("--hermes", default="hermes")
    status.set_defaults(handler_key="status")
    report = subparsers.add_parser("report")
    report.add_argument("--rig", type=Path, required=True)
    report.add_argument("--json", action="store_true")
    report.add_argument("--hermes", default="hermes")
    report.set_defaults(handler_key="report")
    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--rig", type=Path, required=True)
    reconcile.add_argument("--json", action="store_true")
    reconcile.add_argument("--dispatch", action="store_true")
    reconcile.add_argument("--fixture-recovery", action="store_true")
    reconcile.add_argument("--recovery-runtime")
    reconcile.add_argument("--hermes", default="hermes")
    reconcile.set_defaults(handler_key="reconcile")
    taxonomy = subparsers.add_parser("stall-taxonomy")
    taxonomy.add_argument("--json", action="store_true")
    taxonomy.set_defaults(handler_key="stall-taxonomy")
    operator_parser = subparsers.add_parser("operator")
    operator_subparsers = operator_parser.add_subparsers(
        dest="operator_command", required=True
    )
    assess = operator_subparsers.add_parser("assess")
    _add_operator_arguments(assess)
    assess.set_defaults(handler_key="operator-assess")
    for name, key in (("supervise", "operator-supervise"), ("assign", "operator-assign")):
        session_parser = operator_subparsers.add_parser(
            name,
            help=(
                "assess the loop and start a repair session if it stopped"
                if name == "supervise"
                else "start a repair session the owner assigned, whatever the verdict"
            ),
        )
        _add_operator_arguments(session_parser)
        _add_session_arguments(session_parser)
        session_parser.set_defaults(handler_key=key)
    operator_heartbeat = operator_subparsers.add_parser(
        "heartbeat",
        help="refresh the lock heartbeat that opts a session into the quiet window",
    )
    operator_heartbeat.add_argument("--project", required=True)
    operator_heartbeat.add_argument(
        "--log", type=Path, required=True, help="this session's own operator log file"
    )
    operator_heartbeat.add_argument("--profile", default="default")
    operator_heartbeat.add_argument("--lock-directory", type=Path)
    operator_heartbeat.add_argument("--json", action="store_true")
    operator_heartbeat.add_argument("--hermes", default="hermes")
    operator_heartbeat.set_defaults(handler_key="operator-heartbeat")
    return parser


def _add_session_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--operator-profile", default="factory-operator")
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_SESSION_BUDGET,
        help="session wall-clock budget in seconds (hermes chat --run-budget)",
    )
    parser.add_argument("--log-directory", type=Path)
    parser.add_argument("--lock-directory", type=Path)
    parser.add_argument(
        "--quiet-seconds", type=int, default=DEFAULT_LOCK_QUIET_SECONDS
    )
    parser.add_argument(
        "--max-session-seconds", type=int, default=DEFAULT_MAX_SESSION_SECONDS
    )
    parser.add_argument("--dry-run", action="store_true")


def _add_operator_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True)
    parser.add_argument("--job", action="append", default=[])
    parser.add_argument("--threshold", type=int, default=DEFAULT_FAILURE_STREAK_THRESHOLD)
    parser.add_argument(
        "--stale-after", type=int, default=DEFAULT_STALE_AFTER_SECONDS
    )
    parser.add_argument("--profile", default="default")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--hermes", default="hermes")


def validate_rig_profiles(rig: RigSpec, client: HermesClient) -> tuple[str, ...]:
    profiles = dict.fromkeys(seat.profile for seat in rig.seats.values())
    return tuple(profile for profile in profiles if not client.profile_exists(profile))


def warn_nonisolated_seats(rig: RigSpec) -> None:
    seats_by_profile: dict[str, list[str]] = {}
    for seat_name, seat in rig.seats.items():
        seats_by_profile.setdefault(seat.profile, []).append(seat_name)
    for profile, seat_names in seats_by_profile.items():
        if len(seat_names) > 1:
            print(
                f"warning: seats {', '.join(seat_names)} share Hermes profile "
                f"{profile!r}; these seats are not isolated",
                file=sys.stderr,
            )


def _report_missing_profiles(missing: tuple[str, ...]) -> int:
    print(f"error: missing Hermes profiles: {', '.join(missing)}", file=sys.stderr)
    return 2


def handle_version(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    print(f"hermes-factory {__version__}")
    return 0


def handle_validate(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    rig = load_rig(args.rig)
    missing = validate_rig_profiles(rig, client_factory(args.hermes))
    if missing:
        return _report_missing_profiles(missing)
    warn_nonisolated_seats(rig)
    print(f"valid: {rig.project.slug} ({len(rig.seats)} seats)")
    return 0


def handle_apply(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    rig = load_rig(args.rig)
    client = client_factory(args.hermes)
    missing = validate_rig_profiles(rig, client)
    if missing:
        return _report_missing_profiles(missing)
    warn_nonisolated_seats(rig)
    result = ensure_board(client, rig)
    print(
        f"created board {result.slug}"
        if result.created
        else f"board {result.slug} already exists"
    )
    return 0


def handle_mission_plan(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    rig = load_rig(args.rig)
    plan = compile_mission(rig, load_mission(args.mission, rig))
    tasks = [
        {
            "key": task.key,
            "title": task.title,
            "profile": task.profile,
            "parents": list(task.parents),
            "idempotency_key": task.idempotency_key,
            "initial_status": task.initial_status,
            "task_class": task.task_class,
        }
        for task in plan.tasks
    ]
    if args.json:
        print(
            json.dumps(
                {
                    "board_slug": plan.board_slug,
                    "phase": plan.phase,
                    "pending_materialization": plan.pending_materialization,
                    "tasks": tasks,
                },
                indent=2,
            )
        )
    else:
        for task in tasks:
            parents = ", ".join(task["parents"]) or "-"
            assignee = task["profile"] or "(unassigned)"
            print(f"{task['key']} -> {assignee} (parents: {parents})")
    return 0


def handle_mission_apply(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    rig = load_rig(args.rig)
    client = client_factory(args.hermes)
    missing = validate_rig_profiles(rig, client)
    if missing:
        return _report_missing_profiles(missing)
    warn_nonisolated_seats(rig)
    mission = load_mission(args.mission, rig)
    plan = compile_mission(rig, mission)
    ensure_board(client, rig)
    applied = apply_mission(client, rig, plan)
    if args.json:
        print(json.dumps({"task_ids": applied.task_ids}, indent=2))
    else:
        for key, task_id in applied.task_ids.items():
            print(f"{key} -> {task_id}")
    return 0


def handle_status(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    rig = load_rig(args.rig)
    client = client_factory(args.hermes)
    board = ["kanban", "--board", rig.board.slug]
    stats = client.run_json(board + ["stats", "--json"])
    tasks = client.run_json(board + ["list", "--json"])
    diagnostics = client.run_json(board + ["diagnostics", "--json"])

    runs_by_task: dict[str, list] = {}
    attachments_by_task: dict[str, list] = {}
    for task in tasks:
        task_id = task.get("id")
        task_class = (task.get("metadata") or {}).get("task_class")
        if task.get("status") == "done" or task_class == "review":
            runs_by_task[task_id] = client.run_json(
                board + ["runs", task_id, "--json"]
            )
        if task.get("status") == "done":
            attachments_by_task[task_id] = client.run_json(
                board + ["attachments", task_id, "--json"]
            )

    findings = audit_tasks(
        tasks,
        runs_by_task,
        diagnostics,
        attachments_by_task=attachments_by_task,
        commit_verifier=lambda object_id: verify_git_commit(
            rig.project.cwd, object_id
        ),
    )
    counts = {
        "critical": sum(1 for finding in findings if finding.severity == "critical"),
        "error": sum(1 for finding in findings if finding.severity == "error"),
        "warning": sum(1 for finding in findings if finding.severity == "warning"),
    }
    if args.json:
        print(
            json.dumps(
                {"stats": stats, "findings": [asdict(finding) for finding in findings]},
                indent=2,
            )
        )
    else:
        print(
            f"findings: {counts['critical']} critical, {counts['error']} error, "
            f"{counts['warning']} warning"
        )
        for finding in findings:
            suffix = f" ({finding.task_id})" if finding.task_id else ""
            print(f"{finding.severity}: {finding.code}: {finding.message}{suffix}")
    return 1 if counts["critical"] or counts["error"] else 0


def _hydrate_task_identities(
    client: HermesClient, board_slug: str, tasks: Any
) -> Any:
    """Add the identity fields the installed Kanban list projection omits.

    ``hermes kanban list --json`` carries neither ``idempotency_key`` nor task
    metadata, so a report built from list rows alone cannot tell a decomposition or
    packet card from any other card. The key is read back through the client's
    read-only record path, exactly like the reconciler does. A report is a snapshot
    that always exits 0, so a record that cannot be read leaves the card
    unidentified instead of failing the run.
    """
    if not isinstance(tasks, list):
        return tasks
    hydrated: list = []
    for task in tasks:
        if not isinstance(task, dict) or isinstance(task.get("idempotency_key"), str):
            hydrated.append(task)
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id:
            hydrated.append(task)
            continue
        try:
            record = client.task_record(board_slug, task_id)
        except HermesCommandError:
            hydrated.append(task)
            continue
        key = record.get("idempotency_key") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or record.get("id") != task_id
            or not isinstance(key, str)
        ):
            hydrated.append(task)
            continue
        hydrated.append({**task, "idempotency_key": key})
    return hydrated


def handle_report(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    rig = load_rig(args.rig)
    client = client_factory(args.hermes)
    board = ["kanban", "--board", rig.board.slug]
    stats = client.run_json(board + ["stats", "--json"])
    tasks = _hydrate_task_identities(
        client, rig.board.slug, client.run_json(board + ["list", "--json"])
    )
    diagnostics = client.run_json(board + ["diagnostics", "--json"])

    runs_by_task: dict[str, list] = {}
    attachments_by_task: dict[str, list] = {}
    for task in tasks:
        task_id = task.get("id")
        if not isinstance(task_id, str):
            continue
        runs_by_task[task_id] = client.run_json(
            board + ["runs", task_id, "--json"]
        )
        if task.get("status") == "done":
            attachments_by_task[task_id] = client.run_json(
                board + ["attachments", task_id, "--json"]
            )

    findings = audit_tasks(
        tasks,
        runs_by_task,
        diagnostics,
        attachments_by_task=attachments_by_task,
        commit_verifier=lambda object_id: verify_git_commit(
            rig.project.cwd, object_id
        ),
    )
    proof_by_task = build_proof_map(tasks, runs_by_task, findings)
    metrics = compute_metrics(
        tasks, runs_by_task, stats, proof_by_task=proof_by_task
    )
    if args.json:
        print(
            json.dumps(
                {"report": {"board": rig.board.slug}, "metrics": asdict(metrics)},
                indent=2,
            )
        )
    else:
        print(f"done: {metrics.done}  (totals: {metrics.totals or '-'})")
        throughput = (
            f"{metrics.throughput_per_day}"
            if metrics.throughput_per_day is not None
            else "-"
        )
        proof_rate = (
            f"{metrics.proof_rate:.2f}" if metrics.proof_rate is not None else "-"
        )
        rework_rate = (
            f"{metrics.rework_rate:.2f}" if metrics.rework_rate is not None else "-"
        )
        print(
            f"throughput_per_day={throughput}  proof_rate={proof_rate}  "
            f"rework_rate={rework_rate}  changes_requested={metrics.changes_requested}"
        )
        lead = (
            f"{metrics.median_lead_time_minutes}"
            if metrics.median_lead_time_minutes is not None
            else "-"
        )
        print(f"median lead time: {lead} minutes")
        decomposition_lead = (
            f"{metrics.decomposition_lead_time_minutes} minutes"
            if metrics.decomposition_lead_time_minutes is not None
            else "not observed"
        )
        packet_lead = (
            f"{metrics.median_packet_lead_time_minutes} minutes"
            if metrics.median_packet_lead_time_minutes is not None
            else "not observed"
        )
        packet_counts = ", ".join(
            f"{slice_id}: {count}"
            for slice_id, count in (metrics.packets_per_architectural_slice or {}).items()
        ) or "not observed"
        first_attempt = (
            f"{metrics.builder_first_attempt_completion_rate:.2f}"
            if metrics.builder_first_attempt_completion_rate is not None
            else "not observed"
        )
        checkpoint_rate = (
            f"{metrics.progress_checkpoint_rate:.2f}"
            if metrics.progress_checkpoint_rate is not None
            else "not observed"
        )
        timeout_rates = ", ".join(
            f"{task_class}: {rate:.2f}"
            for task_class, rate in (metrics.hard_timeout_rate_by_task_class or {}).items()
        ) or "not observed"
        review_rework = (
            f"{metrics.rework_rate_after_review:.2f}"
            if metrics.rework_rate_after_review is not None
            else "not observed"
        )
        print(f"decomposition lead time: {decomposition_lead}")
        print(f"packets per architectural slice: {packet_counts}")
        print(f"builder first-attempt completion rate: {first_attempt}")
        print(f"progress checkpoint rate: {checkpoint_rate}")
        print(f"hard timeout rate by task class: {timeout_rates}")
        print(f"median packet lead time: {packet_lead}")
        print(f"rework rate after review: {review_rework}")
        action_status = metrics.action_latency_observation
        first_command = (
            f"{metrics.time_to_first_command_minutes} minutes"
            if metrics.time_to_first_command_minutes is not None
            else action_status
        )
        first_mutation = (
            f"{metrics.time_to_first_file_mutation_minutes} minutes"
            if metrics.time_to_first_file_mutation_minutes is not None
            else action_status
        )
        print(f"time to first command: {first_command}")
        print(f"time to first file mutation: {first_mutation}")
        open_seats = ", ".join(
            f"{seat}: {count}"
            for seat, count in sorted(metrics.open_work_by_seat.items())
        ) or "-"
        print(f"open work: {open_seats}")
    return 0


def handle_reconcile(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    rig = load_rig(args.rig)
    result = reconcile_board(
        client_factory(args.hermes),
        rig,
        dispatch=args.dispatch,
        fixture_recovery=args.fixture_recovery,
        recovery_runtime=args.recovery_runtime,
    )
    payload = {
        "scanned": result.scanned,
        "eligible": result.eligible,
        "recovery_task_ids": result.recovery_task_ids,
        "provider_reopened_task_ids": list(result.provider_reopened_task_ids),
        "finalized_recovery_task_ids": result.finalized_recovery_task_ids,
        "board_incident_task_id": result.board_incident_task_id,
        "dispatched": result.dispatched,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"scanned {result.scanned} blocked tasks; "
            f"created or reused {len(result.recovery_task_ids)} recovery tasks; "
            f"board incident {result.board_incident_task_id or '-'}; "
            f"reopened {len(result.provider_reopened_task_ids)} provider-parked tasks; "
            f"finalized {len(result.finalized_recovery_task_ids)} recovery plans; "
            f"dispatched {result.dispatched}"
        )
        for task_id, recovery_id in result.recovery_task_ids.items():
            print(f"{task_id} -> {recovery_id}")
        for task_id in result.provider_reopened_task_ids:
            print(f"provider park {task_id} -> reopened")
    return 0


def handle_stall_taxonomy(
    args: argparse.Namespace, client_factory: ClientFactory
) -> int:
    classes = []
    for item in stall_taxonomy():
        payload = asdict(item)
        payload["reason_code"] = payload.pop("code")
        classes.append(payload)
    if args.json:
        print(json.dumps({"stall_classes": classes}, indent=2))
    else:
        for item in classes:
            automatic = "automatic" if item["automatic"] else "manual"
            print(
                f"{item['reason_code']}: {automatic}; owner={item['recovery_owner']}; "
                f"detection={item['detection']}"
            )
    return 0


def handle_operator_assess(args: argparse.Namespace, client_factory: ClientFactory) -> int:
    client = client_factory(args.hermes)
    state = client.cron_state(args.profile)
    verdict = assess_engine(
        state,
        args.project,
        job_ids=tuple(args.job),
        threshold=args.threshold,
        stale_after=args.stale_after,
    )
    if args.json:
        print(json.dumps(verdict_payload(verdict), indent=2))
    else:
        print(f"{verdict.verdict}: {verdict.project} ({', '.join(verdict.reasons)})")
        for item in verdict.jobs:
            incidents = (
                f" incidents={','.join(item.open_incident_ids)}"
                if item.open_incident_ids
                else ""
            )
            print(
                f"  {item.job_id} {item.name}: streak={item.failure_streak} "
                f"last_status={item.last_status or '-'} {item.reason}{incidents}"
            )
    return 0


def handle_operator_supervise(
    args: argparse.Namespace, client_factory: ClientFactory
) -> int:
    return _run_operator_session(args, client_factory, force=False)


def handle_operator_assign(
    args: argparse.Namespace, client_factory: ClientFactory
) -> int:
    """Start the session the engine's owner assigned, whatever the verdict says."""
    return _run_operator_session(args, client_factory, force=True)


def _operator_root(client: HermesClient, args: argparse.Namespace) -> Path:
    if args.lock_directory is not None:
        return args.lock_directory
    return client.cron_directory(args.profile).parent / "operator" / args.project


def _run_operator_session(
    args: argparse.Namespace, client_factory: ClientFactory, *, force: bool
) -> int:
    client = client_factory(args.hermes)
    operator_root = _operator_root(client, args)
    plan = OperatorPlan(
        project=args.project,
        profile=args.operator_profile,
        prompt=args.prompt,
        repo=args.repo,
        budget=args.budget,
        log_directory=args.log_directory or operator_root / "logs",
        lock_directory=operator_root,
    )
    result = supervise(
        client,
        plan,
        hermes=args.hermes,
        cron_profile=args.profile,
        job_ids=tuple(args.job),
        threshold=args.threshold,
        stale_after=args.stale_after,
        quiet_seconds=args.quiet_seconds,
        max_session_seconds=args.max_session_seconds,
        dry_run=args.dry_run,
        force=force,
    )
    if args.json:
        print(json.dumps(supervise_payload(result), indent=2))
    else:
        print(f"{result.verdict.verdict}: {result.reason}")
        if result.engine_holder:
            print(f"engine lock held by: {result.engine_holder}")
        if result.command:
            print("command: " + " ".join(result.command))
        if result.log_path is not None:
            print(f"log: {result.log_path}")
        print(f"lock: {result.lock_path}")
    return 0


def handle_operator_heartbeat(
    args: argparse.Namespace, client_factory: ClientFactory
) -> int:
    """Refresh the heartbeat of the lock whose session log is the one named.

    A session that heartbeats opts into the quiet window: the supervisor reads a
    stopped heartbeat as a dead session, so the circuit recovers in minutes instead
    of waiting out the whole session budget. The engine lock of the same session is
    refreshed with it, so both expire together.
    """
    client = client_factory(args.hermes)
    lock_directory = _operator_root(client, args)
    observed = time.time()
    lock = lock_directory / project_lock_name(args.project)
    if not heartbeat_lock(lock, session_log=args.log, now=observed):
        raise FactoryError(
            f"no operator lock for {args.project} owns {args.log}: "
            "this session no longer owns the circuit"
        )
    engine = engine_lock_path(lock_directory)
    payload = {
        "heartbeat": True,
        "project": args.project,
        "lock": str(lock),
        "engine_lock": str(engine),
        "engine_heartbeat": heartbeat_lock(
            engine, session_log=args.log, now=observed
        ),
        "heartbeat_at": int(observed),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"heartbeat {int(observed)} for {args.project} ({lock})")
    return 0


HANDLERS: dict[str, Callable[[argparse.Namespace, ClientFactory], int]] = {
    "version": handle_version,
    "validate": handle_validate,
    "apply": handle_apply,
    "mission-plan": handle_mission_plan,
    "mission-apply": handle_mission_apply,
    "status": handle_status,
    "report": handle_report,
    "reconcile": handle_reconcile,
    "stall-taxonomy": handle_stall_taxonomy,
    "operator-assess": handle_operator_assess,
    "operator-supervise": handle_operator_supervise,
    "operator-assign": handle_operator_assign,
    "operator-heartbeat": handle_operator_heartbeat,
}


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: ClientFactory = HermesClient,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.handler_key](args, client_factory)
    except FactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def entrypoint() -> None:
    raise SystemExit(main())
