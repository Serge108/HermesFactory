from __future__ import annotations

import json

from hermes_factory.decomposition import (
    ExecutionPacketSpec,
    canonical_source_contract_sha256,
)
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
_COMPLETION_METADATA_FIELDS = {
    "decomposition": [
        "version",
        "project",
        "mission",
        "source_slice",
        "source_contract_sha256",
        "packets",
        "limitations",
    ],
    "implementation": ["changed_files", "commands", "limitations"],
    "review": ["changed_files", "commands", "limitations"],
    "commit": ["commit", "decision_log", "limitations"],
    "recovery": [
        "replacement_task_ids",
        "rewires",
        "superseded_task_ids",
        "limitations",
    ],
    "incident": ["decision_log", "limitations"],
    "acceptance": [],
}

WORKER_PROTOCOL = """\
Work only on the assigned slice. Re-read this intent chain before changing scope.
Record commands actually run and their real exit status. Attach required artifacts.
Include a concise summary and structured metadata in the task's terminal lifecycle transition.
Do not request input, permission, approval, or confirmation from the operator.
Resolve in-scope ambiguity from the mission intent and acceptance criteria. Choose the safest reversible or non-destructive option and record the assumption.
For an unapproved destructive or external-write action, do not perform it: complete every independent safe step and record the precise technical limitation for coordinator handling, never as a question to the operator.
Do not treat another agent's approval as human approval."""

COORDINATOR_ACCEPTANCE_PROTOCOL = """\
Work only inside the accepted mission scope. Re-read this intent chain before changing scope.
Record commands actually run and their real exit status. Attach required artifacts.
Include a concise summary and structured metadata in the task's terminal lifecycle transition.
Independently verify the evidence and exercise delegated acceptance authority for in-scope decisions.
Do not request input, permission, approval, or confirmation from the operator.
Choose the safest reversible or non-destructive option for any remaining ambiguity, record the decision, and complete every independently safe step.
Do not perform an unapproved destructive or irreversible external write; record the precise limitation as a terminal mission result instead of asking the operator.
Record the decision as coordinator acceptance; do not represent it as human approval."""

REVIEW_REJECTION_PREFIX = "Factory review rejected:"
HUMAN_GATE_MARKER = "<!-- hermes-factory:human-gate -->"

KANBAN_HANDBOOK = """\
Report results in the Kanban task summary with structured metadata and attach required artifacts as Kanban attachments instead of terminal messages."""


def _completion_metadata_section(task_class: str, proof: ProofSpec | None) -> str:
    if task_class == "decomposition":
        metadata = {
            "version": 1,
            "project": "project-slug",
            "mission": "mission-id",
            "source_slice": "architectural-slice-id",
            "source_contract_sha256": "<64 lowercase hexadecimal characters>",
            "packets": [
                {
                    "id": "bounded-slug",
                    "title": "Concrete reviewable behavior",
                    "goal": "One observable outcome",
                    "depends_on": [],
                    "acceptance": ["Observable packet-level criterion"],
                    "source_acceptance_indexes": [0],
                    "likely_files": ["relative/path.py"],
                    "symbols": ["module.symbol"],
                    "first_artifact": "Add a failing focused test for the behavior.",
                    "proof": {
                        "commands": ["focused proof command"],
                        "artifacts": [],
                        "changed_files_required": True,
                    },
                }
            ],
            "limitations": [],
        }
        explanation = (
            "Return these exact root and nested keys as structured run metadata. "
            "Do not wrap the manifest, rename fields, or return it only as prose."
        )
    elif task_class in {"implementation", "review"}:
        metadata = {
            "changed_files": ["path/to/file"],
            "commands": [
                {"command": command, "exit_code": 0}
                for command in (proof.commands if proof else ())
            ],
            "limitations": [],
        }
        explanation = (
            "`changed_files` must be a flat list of path strings, `commands` must be a list of "
            "objects with string `command` and integer `exit_code`, and `limitations` must be a flat "
            "list of strings. Keep every required command text exactly as rendered above. "
            "Do not use `cmd`, `exit`, nested changed-file groups, or prose-only command records."
        )
    elif task_class == "commit":
        metadata = {
            "commit": "<full-length lowercase Git commit object id>",
            "decision_log": [],
            "limitations": [],
        }
        explanation = (
            "`commit` must be the exact full-length lowercase Git commit object id created by "
            "this task; `decision_log` and `limitations` must be flat lists of strings."
        )
    elif task_class == "recovery":
        metadata = {
            "replacement_task_ids": ["replacement-task-id"],
            "rewires": [
                {
                    "old_parent": "superseded-review-id",
                    "new_parent": "replacement-review-id",
                    "child": "downstream-task-id",
                }
            ],
            "superseded_task_ids": ["superseded-task-id"],
            "limitations": [],
        }
        explanation = (
            "Every id must be an exact Kanban task id. `rewires` must contain only objects with "
            "the exact keys `old_parent`, `new_parent`, and `child`."
        )
    elif task_class == "incident":
        metadata = {"decision_log": [], "limitations": []}
        explanation = (
            "`decision_log` and `limitations` must be flat lists of non-empty strings. "
            "Record every diagnosis and corrective action in `decision_log`."
        )
    else:
        metadata = {}
        explanation = "No structured completion metadata is required."
    return (
        "Completion metadata schema (exact keys and nested field names):\n"
        f"```json\n{json.dumps(metadata, indent=2)}\n```\n"
        f"{explanation}"
    )


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
        "required_metadata": list(_COMPLETION_METADATA_FIELDS[task_class]),
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
    expected_metadata = _COMPLETION_METADATA_FIELDS.get(task_class)
    return (
        expected_metadata is not None
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
        sections.append(
            (
                "# Slice",
                f"Slice id: {slice_spec.id}\n{slice_spec.title}: {slice_spec.goal}",
            )
        )
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
    rig: RigSpec,
    mission: MissionSpec,
    seat_name: str,
    role: str,
    *,
    runtime: str | None = None,
) -> str:
    authority = (
        "The coordinator has delegated decision authority inside the accepted mission. Product "
        "ambiguities are resolved autonomously from mission intent and acceptance criteria; choose "
        "the safest reversible or non-destructive option and record the decision without operator input.\n"
        if rig.policies.decision_authority == "coordinator"
        else "Human authority applies only to the explicit final acceptance gate. During execution, "
        "choose the safest reversible or non-destructive option, record assumptions, and do not request "
        "interim operator input.\n"
    )
    return (
        "# Scope boundaries\n"
        f"Project {rig.project.slug}; mission {mission.id}; workflow {mission.workflow}.\n"
        f"Assigned seat: {seat_name}; role: {role}.\n"
        f"Failure limit: {rig.policies.max_retries} consecutive non-successful runs; "
        f"bounded runtime per task: {runtime or rig.policies.max_runtime}.\n"
        "When a task reaches the failure limit, do not retry the same scope: the coordinator "
        "must decompose it into smaller sequential tasks, rewire dependencies, and remove the "
        "superseded card.\n"
        f"Completion contract: {rig.policies.completion_contract}.\n"
        f"{authority}"
        "Anything beyond the slice goal and acceptance lists must be recorded for coordinator handling; "
        "continue every independently safe in-scope step instead of asking the operator."
    )


def _handoff_section(rig: RigSpec, mission: MissionSpec, target: str) -> str:
    return f"# Handoff\n{KANBAN_HANDBOOK}\nNext target: {target}."


def decomposition_source_contract(
    rig: RigSpec, mission: MissionSpec, slice_spec: SliceSpec
) -> dict[str, object]:
    """Return the canonical Factory-owned source data bound to decomposition."""
    proof = slice_spec.proof
    return {
        "version": 1,
        "project": {
            "slug": rig.project.slug,
            "name": rig.project.name,
            "cwd": str(rig.project.cwd),
        },
        "mission": {
            "version": mission.version,
            "id": mission.id,
            "title": mission.title,
            "intent": mission.intent,
            "workflow": mission.workflow,
            "human_acceptance": mission.human_acceptance,
            "slice_ids": [item.id for item in mission.slices],
            "acceptance": list(mission.acceptance),
        },
        "source_slice": {
            "id": slice_spec.id,
            "title": slice_spec.title,
            "goal": slice_spec.goal,
            "depends_on": list(slice_spec.depends_on),
            "acceptance": list(slice_spec.acceptance),
            "proof": (
                {
                    "commands": list(proof.commands),
                    "artifacts": list(proof.artifacts),
                    "changed_files_required": proof.changed_files_required,
                }
                if proof
                else None
            ),
        },
        "source_acceptance": [*mission.acceptance, *slice_spec.acceptance],
    }


def render_decomposition_body(
    rig: RigSpec, mission: MissionSpec, slice_spec: SliceSpec
) -> str:
    workflow = rig.workflows[mission.workflow]
    seat_name = workflow.decomposition_seat
    if seat_name is None:
        raise SpecError("workflow does not define a decomposition seat")
    if (
        rig.policies.decomposer_runtime is None
        or rig.policies.max_packets_per_slice is None
        or rig.policies.max_packet_acceptance_items is None
    ):
        raise SpecError("rig does not define decomposition policy limits")
    seat = rig.seats[seat_name]
    source_contract = decomposition_source_contract(rig, mission, slice_spec)
    source_hash = canonical_source_contract_sha256(source_contract)
    manifest = {
        "version": 1,
        "project": rig.project.slug,
        "mission": mission.id,
        "source_slice": slice_spec.id,
        "source_contract_sha256": source_hash,
        "packets": [
            {
                "id": "bounded-slug",
                "title": "Concrete reviewable behavior",
                "goal": "One observable outcome",
                "depends_on": [],
                "acceptance": ["Observable packet-level criterion"],
                "source_acceptance_indexes": [0],
                "likely_files": ["relative/path.py"],
                "symbols": ["module.symbol"],
                "first_artifact": "Add a failing focused test for the behavior.",
                "proof": {
                    "commands": ["focused proof command"],
                    "artifacts": [],
                    "changed_files_required": True,
                },
            }
        ],
        "limitations": [],
    }
    parts = [
        _intent_sections(rig, mission, slice_spec),
        _proof_section(rig, slice_spec),
        _audit_contract("decomposition", None),
        _completion_metadata_section("decomposition", None),
        (
            "# Immutable source contract\n"
            "This canonical Factory-owned JSON is read-only. Its SHA-256 binds the result to the "
            "architecture and acceptance boundaries shown here.\n"
            f"Source contract SHA-256: {source_hash}\n"
            f"```json\n{json.dumps(source_contract, indent=2, ensure_ascii=False)}\n```"
        ),
        (
            "# Decomposition output\nReturn exactly one version-1 manifest with this JSON shape:\n"
            f"```json\n{json.dumps(manifest, indent=2)}\n```\n"
            "Every source acceptance index must be covered by at least one packet. Preserve all "
            "architecture, goal, dependency, acceptance, and proof boundaries. Each packet must be a "
            "small vertical reviewable behavior with exact inspected likely files and symbols, one early "
            "durable first artifact, and a narrow proof command. Record blocking uncertainty in "
            "`limitations`; never silently revise the source contract."
        ),
        (
            "# Packet limits\n"
            f"workflow.decomposition_seat: {seat_name}.\n"
            f"decomposer_runtime: {rig.policies.decomposer_runtime}.\n"
            f"Builder runtime budget: {rig.policies.max_runtime}.\n"
            f"Maximum packets for this slice: {rig.policies.max_packets_per_slice}.\n"
            "Maximum acceptance items per packet: "
            f"{rig.policies.max_packet_acceptance_items}."
        ),
        _scope_section(
            rig,
            mission,
            seat_name,
            seat.role,
            runtime=rig.policies.decomposer_runtime,
        ),
        (
            "# Prohibited actions\n"
            "Do not edit the repository. Do not edit production or test files. Do not delegate. "
            "Do not create, delete, complete, block, or mutate tasks except completing this assigned "
            "decomposition task. Do not mutate the task graph or dependencies. Do not assign profiles. "
            "Do not change architecture. Do not change mission or slice acceptance. Do not ask the "
            "operator questions. Repository inspection and read-only commands are allowed only to make "
            "file, symbol, artifact, and proof guidance concrete."
        ),
        (
            "# Completion\nComplete by calling kanban_complete once with a concise summary and the "
            "entire manifest as structured run metadata. Run metadata preserves nested JSON exactly; "
            "do not put the authoritative manifest only in prose or an attachment."
        ),
        _handoff_section(rig, mission, "trusted Factory validation and graph materialization"),
    ]
    return _limit("\n\n".join(parts))


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
        _completion_metadata_section("implementation", slice_spec.proof),
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


def render_packet_implementation_body(
    rig: RigSpec,
    mission: MissionSpec,
    source_slice: SliceSpec,
    packet: ExecutionPacketSpec,
) -> str:
    """Render an implementation task from one validated execution packet."""
    trusted_proof = source_slice.proof
    if trusted_proof is None:
        if packet.proof.commands or packet.proof.artifacts or packet.proof.changed_files_required:
            raise SpecError("packet proof is not authorized by the source slice")
    else:
        if any(command not in trusted_proof.commands for command in packet.proof.commands):
            raise SpecError("packet proof command is not authorized by the source slice")
        if any(artifact not in trusted_proof.artifacts for artifact in packet.proof.artifacts):
            raise SpecError("packet proof artifact is not authorized by the source slice")
        if trusted_proof.changed_files_required and not packet.proof.changed_files_required:
            raise SpecError("packet proof cannot weaken the source slice changed-files requirement")
    packet_proof = ProofSpec(
        commands=packet.proof.commands,
        artifacts=packet.proof.artifacts,
        changed_files_required=packet.proof.changed_files_required,
    )
    packet_slice = SliceSpec(
        id=packet.id,
        title=packet.title,
        goal=packet.goal,
        depends_on=packet.depends_on,
        acceptance=packet.acceptance,
        proof=packet_proof,
    )
    base = render_implementation_body(rig, mission, packet_slice)
    checkpoint = {
        "version": 1,
        "changed_files": ["relative/path.py"],
        "completed_acceptance_indexes": [0],
        "remaining": ["Concrete unfinished behavior"],
        "commands": [{"command": "...", "exit_code": 1}],
        "next_action": "First exact action for the continuation",
        "limitations": [],
    }
    packet_protocol = (
        "# Execution packet\n"
        f"Source architectural slice: {source_slice.id}.\n"
        f"Likely files:\n{_list_lines(packet.likely_files)}\n"
        f"Likely symbols:\n{_list_lines(packet.symbols)}\n"
        f"Declared first artifact: {packet.first_artifact}\n"
        "Trusted packet proof commands validated against the immutable source slice:\n"
        f"{_list_lines(packet.proof.commands)}\n\n"
        "Inspect only the named files and symbols first; expand scope only when evidence requires it. "
        "Run the narrow baseline or reproduction command before broad analysis. Create the declared "
        "first durable artifact early, then run the focused check immediately. Complete one strict "
        "RED, GREEN, REFACTOR cycle. Heartbeat proves liveness, not material progress. Do not spend "
        "most of the run on a prose plan when exact files, symbols, and the first artifact are already "
        "provided. Reserve the final 25 percent of the runtime budget for proof and Kanban handoff.\n\n"
        "If the packet cannot be completed and verified safely inside the remaining budget, stop "
        "expanding scope, leave the repository syntactically valid where possible, run the narrowest "
        "relevant check, and block with the exact prefix `Factory progress checkpoint:` followed by "
        "this exact JSON schema:\n"
        f"```json\n{json.dumps(checkpoint, indent=2)}\n```"
    )
    handoff_marker = "\n\n# Handoff\n"
    if handoff_marker not in base:
        raise SpecError("implementation body is missing its handoff section")
    before, handoff = base.split(handoff_marker, 1)
    return _limit(f"{before}\n\n{packet_protocol}{handoff_marker}{handoff}")


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
        _completion_metadata_section("review", slice_spec.proof),
        WORKER_PROTOCOL,
        _scope_section(rig, mission, review_seat_name, review_seat.role),
        (
            "Independently verify the parent implementation result against the slice acceptance lists; "
            "re-run the recorded proof commands yourself. Do not accept an implementation claim without evidence.\n"
            "If verification passes, call kanban_complete with the reviewer summary and structured metadata. "
            "If verification fails, call kanban_block so dependent work cannot start, and begin the reason "
            f"with {REVIEW_REJECTION_PREFIX} followed by the actionable findings; the coordinator reacts "
            "automatically to that exact prefix. An unmarked block is an unclassified operational stall "
            "for coordinator diagnosis, never a request for operator input."
        ),
        _handoff_section(rig, mission, handoff_target),
    ]
    return _limit("\n\n".join(parts))


def render_acceptance_body(rig: RigSpec, mission: MissionSpec) -> str:
    workflow = rig.workflows[mission.workflow]
    acceptance_seat_name = workflow.acceptance_seat
    acceptance_seat = rig.seats[acceptance_seat_name]
    explicit_human_gate = (
        rig.policies.decision_authority == "human" and mission.human_acceptance
    )
    autonomous_acceptance = not explicit_human_gate
    protocol = (
        COORDINATOR_ACCEPTANCE_PROTOCOL if autonomous_acceptance else WORKER_PROTOCOL
    )
    parts = [
        _intent_sections(rig, mission, None),
        _proof_section(rig, None),
        _audit_contract("acceptance", None),
        protocol,
        _scope_section(
            rig,
            mission,
            acceptance_seat_name,
            acceptance_seat.role,
            runtime=rig.policies.coordinator_runtime,
        ),
    ]
    if autonomous_acceptance:
        if rig.policies.decision_authority == "coordinator":
            authority = (
                "The coordinator has delegated decision authority for this mission. "
                "Choose the recommended option for any remaining in-scope product ambiguity, record the "
                "decision and evidence, and complete acceptance without waiting for the operator."
            )
        else:
            authority = (
                "This mission has no explicit human gate. The assigned acceptance seat owns the final "
                "in-scope decision and must complete acceptance autonomously without requesting operator input."
            )
        parts.append(
            "# Delegated authority\n"
            f"{authority} Do not push commits or perform irreversible external actions."
        )
    else:
        parts.append(
            f"{HUMAN_GATE_MARKER}\n# Human gate\nThis mission requires human acceptance: a blocked task stays blocked until a human operator completes it. "
            "Agent review results are context for that decision, never acceptance."
        )
    target = "mission completion" if autonomous_acceptance else "the operator for mission completion"
    parts.append(_handoff_section(rig, mission, target))
    return _limit("\n\n".join(parts))


def render_commit_body(
    rig: RigSpec, mission: MissionSpec, slice_spec: SliceSpec
) -> str:
    workflow = rig.workflows[mission.workflow]
    coordinator_name = workflow.acceptance_seat
    coordinator = rig.seats[coordinator_name]
    parts = [
        _intent_sections(rig, mission, slice_spec),
        "# Proof required\nThe parent review must be complete and approved with its required proof.",
        _audit_contract("commit", None),
        _completion_metadata_section("commit", None),
        _scope_section(
            rig,
            mission,
            coordinator_name,
            coordinator.role,
            runtime=rig.policies.coordinator_runtime,
        ),
        (
            "# Commit policy\nInspect the parent implementation metadata and the live repository diff. "
            "Create one local Git commit containing only the reviewed slice. Preserve unrelated dirty-tree "
            "changes. If the reviewed content is already committed, verify and report the existing commit. "
            "Use a mission- and slice-specific message. Never push, reset, rebase, amend unrelated history, "
            "or include secrets. Complete with the exact commit hash, decision log, and limitations."
        ),
        _handoff_section(rig, mission, "the next dependent implementation or mission acceptance"),
    ]
    return _limit("\n\n".join(parts))


def render_recovery_contract() -> str:
    return render_task_contract("recovery", None)


def render_task_contract(task_class: str, proof: ProofSpec | None) -> str:
    """Render the exact audit and completion contract for a generated task."""
    return "\n\n".join(
        [
            _audit_contract(task_class, proof),
            _completion_metadata_section(task_class, proof),
        ]
    )
