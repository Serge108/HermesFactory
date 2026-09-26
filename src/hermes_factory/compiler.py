from __future__ import annotations

from dataclasses import dataclass

from hermes_factory.mission_loader import topological_slices
from hermes_factory.models import MissionSpec, RigSpec
from hermes_factory.task_templates import (
    render_acceptance_body,
    render_commit_body,
    render_implementation_body,
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


@dataclass(frozen=True, slots=True)
class MissionPlan:
    board_slug: str
    tasks: tuple[PlannedTask, ...]


def compile_mission(rig: RigSpec, mission: MissionSpec) -> MissionPlan:
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
        )
    )

    return MissionPlan(board_slug=rig.board.slug, tasks=tuple(tasks))
