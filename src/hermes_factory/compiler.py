from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hermes_factory.decomposition import (
    DecompositionManifest,
    ExecutionPacketSpec,
    canonical_source_contract_sha256,
)
from hermes_factory.mission_loader import topological_slices
from hermes_factory.models import MissionSpec, ProofSpec, RigSpec, SliceSpec
from hermes_factory.task_templates import (
    decomposition_source_contract,
    render_acceptance_body,
    render_commit_body,
    render_implementation_body,
    render_packet_implementation_body,
    render_review_body,
)


@dataclass(frozen=True, slots=True)
class PlannedTask:
    key: str
    title: str
    body: str
    profile: str | None
    parents: tuple[str, ...]
    skills: tuple[str, ...]
    initial_status: str | None
    idempotency_key: str
    task_class: str


@dataclass(frozen=True, slots=True)
class MissionPlan:
    board_slug: str
    tasks: tuple[PlannedTask, ...]
    phase: str
    pending_materialization: bool


def compile_decomposition_phase(rig: RigSpec, mission: MissionSpec) -> MissionPlan:
    from hermes_factory.task_templates import render_decomposition_body

    workflow = rig.workflows[mission.workflow]
    if workflow.decomposition_seat is None:
        raise ValueError("decomposition phase requires a decomposition seat")
    decomposition_seat = rig.seats[workflow.decomposition_seat]
    tasks = tuple(
        PlannedTask(
            key=f"{slice_spec.id}:decomposition",
            title=f"Decompose {slice_spec.title}",
            body=render_decomposition_body(rig, mission, slice_spec),
            profile=decomposition_seat.profile,
            parents=(),
            skills=decomposition_seat.skills,
            initial_status=None,
            idempotency_key=(
                f"factory:{rig.project.slug}:{mission.id}:{slice_spec.id}:decomposition"
            ),
            task_class="decomposition",
        )
        for slice_spec in topological_slices(mission)
    )
    return MissionPlan(
        board_slug=rig.board.slug,
        tasks=tasks,
        phase="decomposition",
        pending_materialization=True,
    )


def compile_execution_phase(rig: RigSpec, mission: MissionSpec) -> MissionPlan:
    workflow = rig.workflows[mission.workflow]
    implementation_seat = rig.seats[workflow.implementation_seat]
    review_seat = rig.seats[workflow.review_seat]
    acceptance_seat = rig.seats[workflow.acceptance_seat]

    terminal_keys: list[str] = []
    tasks: list[PlannedTask] = []
    ordered_slices = topological_slices(mission)
    commit_reviewed_slices = rig.policies.git_commit_policy == "reviewed-slices"

    def terminal_key(slice_id: str) -> str:
        suffix = "commit" if commit_reviewed_slices else "review"
        return f"{slice_id}:{suffix}"

    dependent_slices = {
        slice_spec.id: tuple(
            candidate.id
            for candidate in ordered_slices
            if slice_spec.id in candidate.depends_on
        )
        for slice_spec in ordered_slices
    }

    for slice_spec in ordered_slices:
        implementation_key = f"{slice_spec.id}:implementation"
        review_key = f"{slice_spec.id}:review"
        implementation_parents = tuple(
            terminal_key(dependency) for dependency in slice_spec.depends_on
        )
        tasks.append(
            PlannedTask(
                key=implementation_key,
                title=f"Implement {slice_spec.title}",
                body=render_implementation_body(rig, mission, slice_spec),
                profile=implementation_seat.profile,
                parents=implementation_parents,
                skills=implementation_seat.skills,
                initial_status=None,
                idempotency_key=(
                    f"factory:{rig.project.slug}:{mission.id}:{slice_spec.id}:implementation"
                ),
                task_class="implementation",
            )
        )
        next_slices = dependent_slices[slice_spec.id]
        if commit_reviewed_slices:
            handoff_target = (
                f"local commit task for slice {slice_spec.id!r} "
                f"(profile {acceptance_seat.profile!r})"
            )
        elif next_slices:
            handoff_target = ", ".join(
                f"implementation task for slice {next_slice!r} "
                f"(profile {implementation_seat.profile!r})"
                for next_slice in next_slices
            )
        else:
            handoff_target = (
                f"acceptance task for mission {mission.id!r} "
                f"(profile {acceptance_seat.profile!r})"
            )
        tasks.append(
            PlannedTask(
                key=review_key,
                title=f"Review {slice_spec.title}",
                body=render_review_body(
                    rig, mission, slice_spec, handoff_target=handoff_target
                ),
                profile=review_seat.profile,
                parents=(implementation_key,),
                skills=review_seat.skills,
                initial_status=None,
                idempotency_key=(
                    f"factory:{rig.project.slug}:{mission.id}:{slice_spec.id}:review"
                ),
                task_class="review",
            )
        )
        if commit_reviewed_slices:
            commit_key = f"{slice_spec.id}:commit"
            tasks.append(
                PlannedTask(
                    key=commit_key,
                    title=f"Commit {slice_spec.title}",
                    body=render_commit_body(rig, mission, slice_spec),
                    profile=acceptance_seat.profile,
                    parents=(review_key,),
                    skills=acceptance_seat.skills,
                    initial_status=None,
                    idempotency_key=(
                        f"factory:{rig.project.slug}:{mission.id}:{slice_spec.id}:commit"
                    ),
                    task_class="commit",
                )
            )
        terminal_keys.append(terminal_key(slice_spec.id))

    blocked = (
        mission.human_acceptance
        and rig.policies.decision_authority == "human"
    )
    tasks.append(
        PlannedTask(
            key="acceptance",
            title=f"Accept {mission.title}",
            body=render_acceptance_body(rig, mission),
            profile=None if blocked else acceptance_seat.profile,
            parents=tuple(terminal_keys),
            skills=acceptance_seat.skills,
            initial_status="blocked" if blocked else None,
            idempotency_key=f"factory:{rig.project.slug}:{mission.id}:acceptance",
            task_class="acceptance",
        )
    )

    return MissionPlan(
        board_slug=rig.board.slug,
        tasks=tuple(tasks),
        phase="execution",
        pending_materialization=False,
    )


def _topological_packets(
    packets: tuple[ExecutionPacketSpec, ...],
) -> tuple[ExecutionPacketSpec, ...]:
    by_id = {packet.id: packet for packet in packets}
    remaining = list(packets)
    ordered: list[ExecutionPacketSpec] = []
    completed: set[str] = set()
    while remaining:
        ready = next(
            (
                packet
                for packet in remaining
                if all(dependency in completed for dependency in packet.depends_on)
            ),
            None,
        )
        if ready is None:
            raise ValueError("materialized packet graph contains a cycle")
        if any(dependency not in by_id for dependency in ready.depends_on):
            raise ValueError(f"packet {ready.id!r} has an unknown dependency")
        remaining.remove(ready)
        ordered.append(ready)
        completed.add(ready.id)
    return tuple(ordered)


def compile_materialized_execution_phase(
    rig: RigSpec,
    mission: MissionSpec,
    manifests: Mapping[str, DecompositionManifest],
) -> MissionPlan:
    """Compile trusted, validated decomposition results into execution cards."""
    ordered_slices = topological_slices(mission)
    expected_slice_ids = {slice_spec.id for slice_spec in ordered_slices}
    missing = sorted(expected_slice_ids - set(manifests))
    if missing:
        raise ValueError("missing decomposition manifests: " + ", ".join(missing))
    unknown = sorted(set(manifests) - expected_slice_ids)
    if unknown:
        raise ValueError("unknown decomposition manifests: " + ", ".join(unknown))

    for slice_spec in ordered_slices:
        manifest = manifests[slice_spec.id]
        current_contract_hash = canonical_source_contract_sha256(
            decomposition_source_contract(rig, mission, slice_spec)
        )
        if manifest.version != 1:
            raise ValueError(
                f"manifest for slice {slice_spec.id!r} has unsupported version"
            )
        if manifest.source_contract_sha256 != current_contract_hash:
            raise ValueError(
                f"manifest for slice {slice_spec.id!r} does not match current source contract"
            )
        if (
            manifest.project != rig.project.slug
            or manifest.mission != mission.id
            or manifest.source_slice != slice_spec.id
            or manifest.limitations
        ):
            raise ValueError(f"manifest for slice {slice_spec.id!r} is not materializable")

    workflow = rig.workflows[mission.workflow]
    implementation_seat = rig.seats[workflow.implementation_seat]
    review_seat = rig.seats[workflow.review_seat]
    acceptance_seat = rig.seats[workflow.acceptance_seat]
    commit_reviewed_slices = rig.policies.git_commit_policy == "reviewed-slices"

    ordered_packets = {
        slice_spec.id: _topological_packets(manifests[slice_spec.id].packets)
        for slice_spec in ordered_slices
    }
    packet_children = {
        slice_id: {
            packet.id: tuple(
                candidate.id
                for candidate in packets
                if packet.id in candidate.depends_on
            )
            for packet in packets
        }
        for slice_id, packets in ordered_packets.items()
    }
    terminal_packets = {
        slice_id: tuple(
            packet.id for packet in packets if not packet_children[slice_id][packet.id]
        )
        for slice_id, packets in ordered_packets.items()
    }
    source_children = {
        slice_spec.id: tuple(
            candidate.id
            for candidate in ordered_slices
            if slice_spec.id in candidate.depends_on
        )
        for slice_spec in ordered_slices
    }

    def packet_base(slice_id: str, packet_id: str) -> str:
        return f"{slice_id}:{packet_id}"

    def packet_terminal(slice_id: str, packet_id: str) -> str:
        suffix = "commit" if commit_reviewed_slices else "review"
        return f"{packet_base(slice_id, packet_id)}:{suffix}"

    tasks: list[PlannedTask] = []
    slices_by_id = {slice_spec.id: slice_spec for slice_spec in ordered_slices}
    for source_slice in ordered_slices:
        for packet in ordered_packets[source_slice.id]:
            base = packet_base(source_slice.id, packet.id)
            implementation_key = f"{base}:implementation"
            review_key = f"{base}:review"
            if packet.depends_on:
                implementation_parents = tuple(
                    packet_terminal(source_slice.id, dependency)
                    for dependency in packet.depends_on
                )
            else:
                implementation_parents = tuple(
                    packet_terminal(dependency_slice, terminal_packet)
                    for dependency_slice in source_slice.depends_on
                    for terminal_packet in terminal_packets[dependency_slice]
                )
            packet_slice = SliceSpec(
                id=packet.id,
                title=packet.title,
                goal=packet.goal,
                depends_on=packet.depends_on,
                acceptance=packet.acceptance,
                proof=ProofSpec(
                    commands=packet.proof.commands,
                    artifacts=packet.proof.artifacts,
                    changed_files_required=packet.proof.changed_files_required,
                ),
            )
            tasks.append(
                PlannedTask(
                    key=implementation_key,
                    title=f"Implement {source_slice.title}: {packet.title}",
                    body=render_packet_implementation_body(
                        rig, mission, source_slice, packet
                    ),
                    profile=implementation_seat.profile,
                    parents=implementation_parents,
                    skills=implementation_seat.skills,
                    initial_status=None,
                    idempotency_key=(
                        f"factory:{rig.project.slug}:{mission.id}:{source_slice.id}:"
                        f"packet:{packet.id}:implementation"
                    ),
                    task_class="implementation",
                )
            )

            next_targets = [
                f"implementation packet {child!r} in slice {source_slice.id!r}"
                for child in packet_children[source_slice.id][packet.id]
            ]
            if packet.id in terminal_packets[source_slice.id]:
                for child_slice_id in source_children[source_slice.id]:
                    child_slice = slices_by_id[child_slice_id]
                    next_targets.extend(
                        f"implementation packet {candidate.id!r} in slice {child_slice.id!r}"
                        for candidate in ordered_packets[child_slice.id]
                        if not candidate.depends_on
                    )
            handoff_target = ", ".join(next_targets) or f"mission {mission.id!r} acceptance"
            tasks.append(
                PlannedTask(
                    key=review_key,
                    title=f"Review {source_slice.title}: {packet.title}",
                    body=render_review_body(
                        rig, mission, packet_slice, handoff_target=handoff_target
                    ),
                    profile=review_seat.profile,
                    parents=(implementation_key,),
                    skills=review_seat.skills,
                    initial_status=None,
                    idempotency_key=(
                        f"factory:{rig.project.slug}:{mission.id}:{source_slice.id}:"
                        f"packet:{packet.id}:review"
                    ),
                    task_class="review",
                )
            )
            if commit_reviewed_slices:
                tasks.append(
                    PlannedTask(
                        key=f"{base}:commit",
                        title=f"Commit {source_slice.title}: {packet.title}",
                        body=render_commit_body(rig, mission, packet_slice),
                        profile=acceptance_seat.profile,
                        parents=(review_key,),
                        skills=acceptance_seat.skills,
                        initial_status=None,
                        idempotency_key=(
                            f"factory:{rig.project.slug}:{mission.id}:{source_slice.id}:"
                            f"packet:{packet.id}:commit"
                        ),
                        task_class="commit",
                    )
                )

    terminal_source_slices = [
        slice_spec for slice_spec in ordered_slices if not source_children[slice_spec.id]
    ]
    acceptance_parents = tuple(
        packet_terminal(slice_spec.id, packet_id)
        for slice_spec in terminal_source_slices
        for packet_id in terminal_packets[slice_spec.id]
    )
    blocked = mission.human_acceptance and rig.policies.decision_authority == "human"
    tasks.append(
        PlannedTask(
            key="acceptance",
            title=f"Accept {mission.title}",
            body=render_acceptance_body(rig, mission),
            profile=None if blocked else acceptance_seat.profile,
            parents=acceptance_parents,
            skills=acceptance_seat.skills,
            initial_status="blocked" if blocked else None,
            idempotency_key=f"factory:{rig.project.slug}:{mission.id}:acceptance",
            task_class="acceptance",
        )
    )
    return MissionPlan(
        board_slug=rig.board.slug,
        tasks=tuple(tasks),
        phase="execution",
        pending_materialization=False,
    )


def compile_mission(rig: RigSpec, mission: MissionSpec) -> MissionPlan:
    workflow = rig.workflows[mission.workflow]
    if rig.version >= 2 and workflow.decomposition_seat is not None:
        return compile_decomposition_phase(rig, mission)
    return compile_execution_phase(rig, mission)
