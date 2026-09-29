from pathlib import Path

import pytest

from hermes_factory.compiler import (
    MissionPlan,
    PlannedTask,
    compile_materialized_execution_phase,
    compile_mission,
)
from hermes_factory.decomposition import (
    DecompositionManifest,
    DecompositionProofSpec,
    ExecutionPacketSpec,
    canonical_source_contract_sha256,
)
from hermes_factory.mission_loader import load_mission
from hermes_factory.rig_loader import load_rig
from hermes_factory.task_templates import decomposition_source_contract

_TWO_SLICES = """\
version: 1
id: demo-mission
title: Demo mission
intent: Ship one verified behavior without expanding scope.
workflow: delivery
human_acceptance: true
acceptance:
  - The requested behavior exists.
slices:
  - id: implement
    title: Implement the requested behavior
    goal: Make the smallest code change satisfying the mission.
    depends_on: []
    acceptance:
      - A failing test was observed before implementation.
    proof:
      commands:
        - uv run pytest -q
      artifacts: []
      changed_files_required: true
  - id: polish
    title: Polish the implementation
    goal: Remove scratch code and tidy the touched files.
    depends_on: [implement]
    acceptance:
      - No scratch code remains in the changed files.
    proof:
      commands:
        - uv run pytest -q
      artifacts: []
      changed_files_required: true
"""


@pytest.fixture
def rig(valid_rig: Path, tmp_path: Path):
    # A mission that requests human acceptance must be loaded against a rig that
    # declares its authority explicitly.
    declared = tmp_path / "rig-declared.yaml"
    declared.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "completion_contract: local-only}",
            "completion_contract: local-only, decision_authority: coordinator}",
        ),
        encoding="utf-8",
    )
    return load_rig(declared)


@pytest.fixture
def mission(valid_rig: Path, tmp_path: Path, rig):
    path = tmp_path / "mission.yaml"
    path.write_text(_TWO_SLICES, encoding="utf-8")
    return load_mission(path, rig)


@pytest.fixture
def single_slice_mission(valid_rig: Path, tmp_path: Path, rig):
    text = _TWO_SLICES.split("  - id: polish")[0]
    path = tmp_path / "mission.yaml"
    path.write_text(text, encoding="utf-8")
    return load_mission(path, rig)


def _decomposer_rig(valid_rig: Path, tmp_path: Path):
    path = tmp_path / "rig-v2.yaml"
    text = valid_rig.read_text(encoding="utf-8")
    text = text.replace("version: 1", "version: 2", 1)
    text = text.replace(
        "  reviewer: {profile: reviewer, role: independent_review, skills: []}",
        "  reviewer: {profile: reviewer, role: independent_review, skills: []}\n"
        "  decomposer: {profile: decomposer-seat, role: decomposition, skills: []}",
    )
    text = text.replace(
        "delivery: {implementation_seat: builder, review_seat: reviewer, acceptance_seat: builder}",
        "delivery: {decomposition_seat: decomposer, implementation_seat: builder, "
        "review_seat: reviewer, acceptance_seat: builder}",
    )
    text = text.replace(
        "completion_contract: local-only}",
        "completion_contract: local-only, decision_authority: coordinator}",
    )
    path.write_text(text, encoding="utf-8")
    return load_rig(path)


def test_single_slice_yields_implementation_review_and_acceptance(rig, single_slice_mission):
    plan = compile_mission(rig, single_slice_mission)

    assert isinstance(plan, MissionPlan)
    assert plan.board_slug == "demo-rig"
    assert [task.key for task in plan.tasks] == [
        "implement:implementation",
        "implement:review",
        "acceptance",
    ]
    implementation = plan.tasks[0]
    assert isinstance(implementation, PlannedTask)
    assert implementation.profile == "builder"
    assert implementation.parents == ()
    assert implementation.idempotency_key == "factory:demo:demo-mission:implement:implementation"
    assert implementation.initial_status is None


def test_version_two_compiles_only_decomposition_tasks_before_materialization(
    valid_rig: Path, tmp_path: Path
):
    rig = _decomposer_rig(valid_rig, tmp_path)
    mission_path = tmp_path / "mission-v2.yaml"
    mission_path.write_text(_TWO_SLICES, encoding="utf-8")
    mission = load_mission(mission_path, rig)

    plan = compile_mission(rig, mission)

    assert plan.phase == "decomposition"
    assert plan.pending_materialization is True
    assert [task.key for task in plan.tasks] == [
        "implement:decomposition",
        "polish:decomposition",
    ]
    assert all(task.task_class == "decomposition" for task in plan.tasks)
    assert all(task.profile == "decomposer-seat" for task in plan.tasks)
    assert all(task.parents == () for task in plan.tasks)
    assert [task.idempotency_key for task in plan.tasks] == [
        "factory:demo:demo-mission:implement:decomposition",
        "factory:demo:demo-mission:polish:decomposition",
    ]


def test_version_one_plan_remains_direct_execution(rig, single_slice_mission):
    plan = compile_mission(rig, single_slice_mission)

    assert plan.phase == "execution"
    assert plan.pending_materialization is False


def test_review_task_depends_on_implementation_and_uses_review_profile(rig, single_slice_mission):
    plan = compile_mission(rig, single_slice_mission)
    review = plan.tasks[1]

    assert review.key == "implement:review"
    assert review.profile == "reviewer"
    assert review.parents == ("implement:implementation",)
    assert review.idempotency_key == "factory:demo:demo-mission:implement:review"


def test_acceptance_task_depends_on_all_review_tasks(rig, single_slice_mission):
    plan = compile_mission(rig, single_slice_mission)
    acceptance = plan.tasks[2]

    assert acceptance.key == "acceptance"
    assert acceptance.parents == ("implement:review",)
    assert acceptance.idempotency_key == "factory:demo:demo-mission:acceptance"


def test_human_acceptance_makes_acceptance_task_blocked(
    valid_rig: Path, single_slice_mission
):
    valid_rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "completion_contract: local-only}",
            "completion_contract: local-only, decision_authority: human}",
        ),
        encoding="utf-8",
    )
    human_rig = load_rig(valid_rig)
    plan = compile_mission(human_rig, single_slice_mission)
    acceptance = plan.tasks[2]

    assert acceptance.initial_status == "blocked"
    assert acceptance.profile is None


def test_dependent_slice_implementation_depends_on_previous_review(rig, mission):
    plan = compile_mission(rig, mission)
    keys = {task.key: task for task in plan.tasks}

    assert keys["polish:implementation"].parents == ("implement:review",)
    assert "implementation task for slice 'polish'" in keys["implement:review"].body
    assert "acceptance task for mission" not in keys["implement:review"].body


def test_task_bodies_include_declared_seat_roles(rig, single_slice_mission):
    plan = compile_mission(rig, single_slice_mission)

    assert "Assigned seat: builder; role: implementation." in plan.tasks[0].body
    assert "Assigned seat: reviewer; role: independent_review." in plan.tasks[1].body


def test_task_bodies_come_from_rendered_templates(rig, single_slice_mission):
    plan = compile_mission(rig, single_slice_mission)
    implementation = plan.tasks[0]

    assert "# Project" in implementation.body
    assert "# Proof required" in implementation.body
    assert "kanban_complete" in implementation.body
    assert "kanban_request_review" not in implementation.body


def test_no_human_acceptance_yields_unblocked_acceptance_task(
    valid_rig: Path, tmp_path: Path
):
    text = _TWO_SLICES.replace("human_acceptance: true", "human_acceptance: false").split(
        "  - id: polish"
    )[0]
    path = tmp_path / "mission.yaml"
    path.write_text(text, encoding="utf-8")
    rig = load_rig(valid_rig)
    plan = compile_mission(rig, load_mission(path, rig))
    acceptance = plan.tasks[2]

    assert acceptance.initial_status is None
    assert acceptance.profile == "builder"


def test_human_authority_without_human_acceptance_still_assigns_acceptance(
    valid_rig: Path, tmp_path: Path
):
    valid_rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "completion_contract: local-only}",
            "completion_contract: local-only, decision_authority: human}",
        ),
        encoding="utf-8",
    )
    text = _TWO_SLICES.replace("human_acceptance: true", "human_acceptance: false").split(
        "  - id: polish"
    )[0]
    path = tmp_path / "mission.yaml"
    path.write_text(text, encoding="utf-8")
    human_rig = load_rig(valid_rig)

    acceptance = compile_mission(human_rig, load_mission(path, human_rig)).tasks[2]

    assert acceptance.initial_status is None
    assert acceptance.profile == "builder"
    assert "the operator for mission completion" not in acceptance.body.lower()


def test_coordinator_authority_adds_reviewed_commit_gate_and_removes_human_stop(
    valid_rig: Path, tmp_path: Path
):
    valid_rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "completion_contract: local-only}",
            "completion_contract: local-only, decision_authority: coordinator, "
            "git_commit_policy: reviewed-slices}",
        ),
        encoding="utf-8",
    )
    rig = load_rig(valid_rig)
    path = tmp_path / "mission.yaml"
    path.write_text(_TWO_SLICES, encoding="utf-8")

    plan = compile_mission(rig, load_mission(path, rig))
    keys = {task.key: task for task in plan.tasks}

    assert [task.key for task in plan.tasks] == [
        "implement:implementation",
        "implement:review",
        "implement:commit",
        "polish:implementation",
        "polish:review",
        "polish:commit",
        "acceptance",
    ]
    assert keys["implement:commit"].parents == ("implement:review",)
    assert keys["polish:implementation"].parents == ("implement:commit",)
    assert keys["acceptance"].parents == ("implement:commit", "polish:commit")
    assert keys["acceptance"].profile == "builder"
    assert keys["acceptance"].initial_status is None
    assert "local git commit" in keys["implement:commit"].body.lower()
    assert "never push" in keys["implement:commit"].body.lower()
    assert "without waiting for the operator" in keys["acceptance"].body.lower()


def _packet(packet_id: str, *, depends_on: tuple[str, ...] = ()) -> ExecutionPacketSpec:
    return ExecutionPacketSpec(
        id=packet_id,
        title=f"Packet {packet_id}",
        goal=f"Deliver {packet_id}.",
        depends_on=depends_on,
        acceptance=("A failing test was observed before implementation.",),
        source_acceptance_indexes=(0,),
        likely_files=(f"src/{packet_id}.py",),
        symbols=(f"{packet_id}.run",),
        first_artifact=f"Add the failing {packet_id} test.",
        proof=DecompositionProofSpec(
            commands=("uv run pytest -q",),
            artifacts=(),
            changed_files_required=True,
        ),
    )


def _manifest(
    rig,
    mission,
    slice_id: str,
    packets: tuple[ExecutionPacketSpec, ...],
) -> DecompositionManifest:
    source_slice = next(item for item in mission.slices if item.id == slice_id)
    return DecompositionManifest(
        version=1,
        project="demo",
        mission="demo-mission",
        source_slice=slice_id,
        source_contract_sha256=canonical_source_contract_sha256(
            decomposition_source_contract(rig, mission, source_slice)
        ),
        packets=packets,
        limitations=(),
    )


def test_materialized_execution_plan_preserves_packet_and_cross_slice_dependencies(
    valid_rig: Path, tmp_path: Path
):
    rig = _decomposer_rig(valid_rig, tmp_path)
    mission_path = tmp_path / "mission-materialized.yaml"
    mission_path.write_text(_TWO_SLICES, encoding="utf-8")
    mission = load_mission(mission_path, rig)
    manifests = {
        "implement": _manifest(
            rig,
            mission,
            "implement",
            (_packet("red"), _packet("green", depends_on=("red",))),
        ),
        "polish": _manifest(rig, mission, "polish", (_packet("tidy"),)),
    }

    plan = compile_materialized_execution_phase(rig, mission, manifests)
    tasks = {task.key: task for task in plan.tasks}

    assert plan.phase == "execution"
    assert plan.pending_materialization is False
    assert tasks["implement:red:implementation"].parents == ()
    assert tasks["implement:red:review"].parents == (
        "implement:red:implementation",
    )
    assert tasks["implement:green:implementation"].parents == (
        "implement:red:review",
    )
    assert tasks["polish:tidy:implementation"].parents == (
        "implement:green:review",
    )
    assert tasks["acceptance"].parents == ("polish:tidy:review",)
    assert tasks["implement:red:implementation"].idempotency_key == (
        "factory:demo:demo-mission:implement:packet:red:implementation"
    )
    assert "Add the failing red test." in tasks["implement:red:implementation"].body


def test_materialization_requires_exactly_one_manifest_per_architectural_slice(
    valid_rig: Path, tmp_path: Path
):
    rig = _decomposer_rig(valid_rig, tmp_path)
    mission_path = tmp_path / "mission-materialized.yaml"
    mission_path.write_text(_TWO_SLICES, encoding="utf-8")
    mission = load_mission(mission_path, rig)

    with pytest.raises(ValueError, match="missing decomposition manifests.*polish"):
        compile_materialized_execution_phase(
            rig,
            mission,
            {"implement": _manifest(rig, mission, "implement", (_packet("red"),))},
        )


def test_materialization_rejects_a_manifest_bound_to_a_stale_source_contract(
    valid_rig: Path, tmp_path: Path
):
    rig = _decomposer_rig(valid_rig, tmp_path)
    mission_path = tmp_path / "mission-materialized.yaml"
    mission_path.write_text(_TWO_SLICES, encoding="utf-8")
    mission = load_mission(mission_path, rig)
    manifests = {
        "implement": _manifest(rig, mission, "implement", (_packet("red"),)),
        "polish": _manifest(rig, mission, "polish", (_packet("tidy"),)),
    }
    manifests["implement"] = DecompositionManifest(
        version=1,
        project=manifests["implement"].project,
        mission=manifests["implement"].mission,
        source_slice=manifests["implement"].source_slice,
        source_contract_sha256="a" * 64,
        packets=manifests["implement"].packets,
        limitations=(),
    )

    with pytest.raises(ValueError, match="source contract"):
        compile_materialized_execution_phase(rig, mission, manifests)
