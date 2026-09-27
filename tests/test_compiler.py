from pathlib import Path

import pytest

from hermes_factory.compiler import MissionPlan, PlannedTask, compile_mission
from hermes_factory.mission_loader import load_mission
from hermes_factory.rig_loader import load_rig

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
