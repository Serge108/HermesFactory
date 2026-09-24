from __future__ import annotations

from hermes_factory.errors import SpecError
from hermes_factory.models import MissionSpec, RigSpec, SliceSpec

BODY_CHARACTER_LIMIT = 12000

WORKER_PROTOCOL = """\
Work only on the assigned slice. Re-read this intent chain before changing scope.
Record commands actually run and their real exit status. Attach required artifacts.
Use request-review with a concise summary and structured metadata when implementation is ready.
Block the task when a product, security, destructive, external-write, or acceptance decision is required.
Do not treat another agent's approval as human approval."""

KANBAN_HANDBOOK = """\
Report results in the Kanban task summary with structured metadata and attach required artifacts as Kanban attachments instead of terminal messages."""


def _limit(body: str) -> str:
    if len(body) > BODY_CHARACTER_LIMIT:
        raise SpecError(
            f"rendered task body of {len(body)} characters exceeds {BODY_CHARACTER_LIMIT} characters; split the mission or slice instead of truncating"
        )
    return body


def _list_lines(items) -> str:
    if not items:
        return "- (none)"
    return "\n".join(f"- {item}" for item in items)


def _intent_sections(rig: RigSpec, mission: MissionSpec, slice_spec: SliceSpec | None) -> str:
    sections = [
        (
            "# Project",
            f"{rig.project.name} (slug: {rig.project.slug})\nWorking directory: {rig.project.cwd}",
        ),
        ("# Mission", f"{mission.title}: {mission.intent}"),
    ]
    if slice_spec is not None:
        sections.append(("# Slice", f"{slice_spec.title}: {slice_spec.goal}"))
    sections.append(
        (
            "# Acceptance",
            _list_lines((*mission.acceptance, *(slice_spec.acceptance if slice_spec else ()))),
        )
    )
    return "\n\n".join(f"{heading}\n{content}" for heading, content in sections)


def _proof_section(rig: RigSpec, slice_spec: SliceSpec | None) -> str:
    proof = slice_spec.proof if slice_spec else None
    lines = ["# Proof required"]
    if proof is None:
        lines.append("- No proof required by this rig policy.")
    else:
        lines.append(f"Commands that must be recorded:\n{_list_lines(proof.commands)}")
        lines.append(f"Artifacts that must be attached:\n{_list_lines(proof.artifacts)}")
        required = "yes" if proof.changed_files_required else "no"
        lines.append(f"Changed files must be recorded: {required}")
    return "\n".join(lines)


def _scope_section(rig: RigSpec, mission: MissionSpec) -> str:
    return (
        "# Scope boundaries\n"
        f"Project {rig.project.slug}; mission {mission.id}; workflow {mission.workflow}.\n"
        f"Retries allowed per task: {rig.policies.max_retries}; bounded runtime per task: {rig.policies.max_runtime}.\n"
        f"Completion contract: {rig.policies.completion_contract}.\n"
        "Anything beyond the slice goal and acceptance lists requires a blocked decision, not a code change."
    )


def _handoff_section(rig: RigSpec, mission: MissionSpec, target: str) -> str:
    return f"# Handoff\n{KANBAN_HANDBOOK}\nNext target: {target}."


def render_implementation_body(
    rig: RigSpec, mission: MissionSpec, slice_spec: SliceSpec
) -> str:
    workflow = rig.workflows[mission.workflow]
    review_profile = rig.seats[workflow.review_seat].profile
    parts = [
        _intent_sections(rig, mission, slice_spec),
        _proof_section(rig, slice_spec),
        WORKER_PROTOCOL,
        _scope_section(rig, mission),
        _handoff_section(rig, mission, f"review task for slice {slice_spec.id!r} (profile {review_profile!r})"),
    ]
    return _limit("\n\n".join(parts))


def render_review_body(
    rig: RigSpec, mission: MissionSpec, slice_spec: SliceSpec
) -> str:
    workflow = rig.workflows[mission.workflow]
    acceptance_profile = rig.seats[workflow.acceptance_seat].profile
    parts = [
        _intent_sections(rig, mission, slice_spec),
        _proof_section(rig, slice_spec),
        WORKER_PROTOCOL,
        _scope_section(rig, mission),
        (
            "Independently verify the parent implementation result against the slice acceptance lists; "
            "re-run the recorded proof commands yourself. Do not accept an implementation claim without evidence.\n"
            "Request changes with a concise request-review summary and structured metadata when proof is missing."
        ),
        _handoff_section(rig, mission, f"acceptance task for mission {mission.id!r} (profile {acceptance_profile!r})"),
    ]
    return _limit("\n\n".join(parts))


def render_acceptance_body(rig: RigSpec, mission: MissionSpec) -> str:
    parts = [
        _intent_sections(rig, mission, None),
        _proof_section(rig, None),
        WORKER_PROTOCOL,
        _scope_section(rig, mission),
    ]
    if mission.human_acceptance:
        parts.append(
            "# Human gate\nThis mission requires human acceptance: a blocked task stays blocked until a human operator completes it. "
            "Agent review results are context for that decision, never acceptance."
        )
    parts.append(_handoff_section(rig, mission, "the operator for mission completion"))
    return _limit("\n\n".join(parts))
