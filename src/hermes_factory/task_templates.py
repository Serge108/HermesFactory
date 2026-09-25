from __future__ import annotations

import json

from hermes_factory.errors import SpecError
from hermes_factory.models import MissionSpec, ProofSpec, RigSpec, SliceSpec

BODY_CHARACTER_LIMIT = 12000
AUDIT_CONTRACT_PREFIX = "Factory audit contract: "
AUDIT_CONTRACT_START = "<!-- hermes-factory:audit-contract -->"
AUDIT_CONTRACT_END = "<!-- /hermes-factory:audit-contract -->"
AUDIT_CONTRACT_KEYS = {
    "task_class",
    "required_commands",
    "required_artifacts",
    "changed_files_required",
    "required_metadata",
}
LEGACY_AUDIT_CONTRACT_KEYS = AUDIT_CONTRACT_KEYS - {"required_metadata"}
_COMPLETION_METADATA_FIELDS = ["changed_files", "commands", "limitations"]

WORKER_PROTOCOL = """\
Work only on the assigned slice. Re-read this intent chain before changing scope.
Record commands actually run and their real exit status. Attach required artifacts.
Include a concise summary and structured metadata in the task's terminal lifecycle transition.
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


def _audit_contract(task_class: str, proof: ProofSpec | None) -> str:
    contract = {
        "task_class": task_class,
        "required_commands": list(proof.commands) if proof else [],
        "required_artifacts": list(proof.artifacts) if proof else [],
        "changed_files_required": (
            proof.changed_files_required
            if proof and task_class == "implementation"
            else False
        ),
        "required_metadata": (
            list(_COMPLETION_METADATA_FIELDS)
            if task_class in {"implementation", "review"}
            else []
        ),
    }
    payload = AUDIT_CONTRACT_PREFIX + json.dumps(contract, separators=(",", ":"))
    return f"{AUDIT_CONTRACT_START}\n{payload}\n{AUDIT_CONTRACT_END}"


def _valid_audit_contract(value: object) -> bool:
    keys = set(value) if isinstance(value, dict) else set()
    if not isinstance(value, dict) or keys not in (
        AUDIT_CONTRACT_KEYS,
        LEGACY_AUDIT_CONTRACT_KEYS,
    ):
        return False
    task_class = value.get("task_class")
    expected_metadata = (
        _COMPLETION_METADATA_FIELDS
        if task_class in {"implementation", "review"}
        else []
    )
    return (
        task_class in {"implementation", "review", "acceptance"}
        and isinstance(value["required_commands"], list)
        and all(
            isinstance(command, str) and bool(command)
            for command in value["required_commands"]
        )
        and isinstance(value["required_artifacts"], list)
        and all(
            isinstance(artifact, str) and bool(artifact)
            for artifact in value["required_artifacts"]
        )
        and type(value["changed_files_required"]) is bool
        and (
            "required_metadata" not in value
            or value["required_metadata"] == expected_metadata
        )
    )


def parse_audit_contract(body: object) -> dict[str, object]:
    if not isinstance(body, str):
        return {}
    lines = body.splitlines()
    starts = [index for index, line in enumerate(lines) if line == AUDIT_CONTRACT_START]
    ends = [index for index, line in enumerate(lines) if line == AUDIT_CONTRACT_END]
    if len(starts) != 1 or len(ends) != 1:
        return {}
    start = starts[0]
    if start + 2 >= len(lines) or ends[0] != start + 2:
        return {}
    line = lines[start + 1]
    if not line.startswith(AUDIT_CONTRACT_PREFIX):
        return {}
    try:
        value = json.loads(line[len(AUDIT_CONTRACT_PREFIX) :])
    except json.JSONDecodeError:
        return {}
    return value if _valid_audit_contract(value) else {}


def contains_audit_contract_markers(body: object) -> bool:
    if not isinstance(body, str):
        return False
    lines = body.splitlines()
    return AUDIT_CONTRACT_START in lines or AUDIT_CONTRACT_END in lines


def looks_like_factory_task_body(body: object) -> bool:
    if not isinstance(body, str):
        return False
    headings = {line for line in body.splitlines() if line.startswith("# ")}
    return {
        "# Project",
        "# Mission",
        "# Proof required",
        "# Scope boundaries",
        "# Handoff",
    }.issubset(headings)


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


def _scope_section(
    rig: RigSpec, mission: MissionSpec, seat_name: str, role: str
) -> str:
    return (
        "# Scope boundaries\n"
        f"Project {rig.project.slug}; mission {mission.id}; workflow {mission.workflow}.\n"
        f"Assigned seat: {seat_name}; role: {role}.\n"
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
    implementation_seat_name = workflow.implementation_seat
    implementation_seat = rig.seats[implementation_seat_name]
    review_profile = rig.seats[workflow.review_seat].profile
    parts = [
        _intent_sections(rig, mission, slice_spec),
        _proof_section(rig, slice_spec),
        _audit_contract("implementation", slice_spec.proof),
        WORKER_PROTOCOL,
        _scope_section(
            rig, mission, implementation_seat_name, implementation_seat.role
        ),
        (
            "A separate review child already depends on this task. When implementation and proof are ready, "
            "call kanban_complete with the concise summary and structured metadata; completion releases the review child."
        ),
        _handoff_section(rig, mission, f"review task for slice {slice_spec.id!r} (profile {review_profile!r})"),
    ]
    return _limit("\n\n".join(parts))


def render_review_body(
    rig: RigSpec,
    mission: MissionSpec,
    slice_spec: SliceSpec,
    handoff_target: str | None = None,
) -> str:
    workflow = rig.workflows[mission.workflow]
    review_seat_name = workflow.review_seat
    review_seat = rig.seats[review_seat_name]
    acceptance_profile = rig.seats[workflow.acceptance_seat].profile
    if handoff_target is None:
        handoff_target = (
            f"acceptance task for mission {mission.id!r} "
            f"(profile {acceptance_profile!r})"
        )
    parts = [
        _intent_sections(rig, mission, slice_spec),
        _proof_section(rig, slice_spec),
        _audit_contract("review", slice_spec.proof),
        WORKER_PROTOCOL,
        _scope_section(rig, mission, review_seat_name, review_seat.role),
        (
            "Independently verify the parent implementation result against the slice acceptance lists; "
            "re-run the recorded proof commands yourself. Do not accept an implementation claim without evidence.\n"
            "If verification passes, call kanban_complete with the reviewer summary and structured metadata. "
            "If verification fails, call kanban_block with actionable findings so dependent work cannot start."
        ),
        _handoff_section(rig, mission, handoff_target),
    ]
    return _limit("\n\n".join(parts))


def render_acceptance_body(rig: RigSpec, mission: MissionSpec) -> str:
    workflow = rig.workflows[mission.workflow]
    acceptance_seat_name = workflow.acceptance_seat
    acceptance_seat = rig.seats[acceptance_seat_name]
    parts = [
        _intent_sections(rig, mission, None),
        _proof_section(rig, None),
        _audit_contract("acceptance", None),
        WORKER_PROTOCOL,
        _scope_section(
            rig, mission, acceptance_seat_name, acceptance_seat.role
        ),
    ]
    if mission.human_acceptance:
        parts.append(
            "# Human gate\nThis mission requires human acceptance: a blocked task stays blocked until a human operator completes it. "
            "Agent review results are context for that decision, never acceptance."
        )
    parts.append(_handoff_section(rig, mission, "the operator for mission completion"))
    return _limit("\n\n".join(parts))
