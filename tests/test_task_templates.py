from pathlib import Path

import pytest

from hermes_factory.errors import SpecError
from hermes_factory.mission_loader import load_mission
from hermes_factory.rig_loader import load_rig
from hermes_factory.task_templates import (
    render_acceptance_body,
    render_implementation_body,
    render_review_body,
)

HEADINGS = [
    "Project",
    "Mission",
    "Slice",
    "Acceptance",
    "Proof required",
    "Scope boundaries",
    "Handoff",
]

_MISSION_YAML = """\
version: 1
id: demo-mission
title: Demo mission
intent: Ship one verified behavior without expanding scope.
workflow: delivery
human_acceptance: true
acceptance:
  - The requested behavior exists.
  - Automated tests pass.
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
      artifacts:
        - report.txt
      changed_files_required: true
"""


@pytest.fixture
def rig(valid_rig: Path):
    return load_rig(valid_rig)


@pytest.fixture
def mission(valid_rig: Path, tmp_path: Path):
    path = tmp_path / "mission.yaml"
    path.write_text(_MISSION_YAML, encoding="utf-8")
    return load_mission(path, load_rig(valid_rig))


@pytest.fixture
def implement(mission):
    return mission.slices[0]


def _headings(body: str) -> list[str]:
    return [line[2:] for line in body.splitlines() if line.startswith("# ")]


def test_implementation_body_contains_each_heading_once(rig, mission, implement):
    body = render_implementation_body(rig, mission, implement)

    assert _headings(body) == HEADINGS


def test_implementation_body_orders_intent_chain(rig, mission, implement):
    body = render_implementation_body(rig, mission, implement)

    project_index = body.find(rig.project.name)
    mission_index = body.find(mission.intent)
    slice_index = body.find(implement.goal)

    assert 0 <= project_index < mission_index < slice_index


def test_implementation_body_renders_command_and_artifact_proof(rig, mission, implement):
    body = render_implementation_body(rig, mission, implement)

    assert "uv run pytest -q" in body
    assert "report.txt" in body
    assert "Changed files must be recorded: yes" in body


def test_implementation_body_directs_worker_to_kanban_handoffs_and_blocking(
    rig, mission, implement
):
    body = render_implementation_body(rig, mission, implement).lower()

    assert "request-review" in body
    assert "metadata" in body
    assert "attachment" in body
    assert "block" in body
    assert "terminal message" in body


def test_implementation_body_rejects_overflow(rig, tmp_path):
    path = tmp_path / "mission.yaml"
    path.write_text(
        _MISSION_YAML.replace(
            "intent: Ship one verified behavior without expanding scope.",
            f"intent: {'x' * 12000}",
        ),
        encoding="utf-8",
    )
    loaded = load_mission(path, rig)

    with pytest.raises(SpecError, match="exceeds"):
        render_implementation_body(rig, loaded, loaded.slices[0])


def test_review_body_contains_core_headings(rig, mission, implement):
    body = render_review_body(rig, mission, implement)

    assert _headings(body) == HEADINGS


def test_review_body_requires_independent_verification(rig, mission, implement):
    body = render_review_body(rig, mission, implement).lower()

    assert "independently verify" in body
    assert "evidence" in body
    assert "request-review" in body
    assert "attachment" in body


def test_acceptance_body_contains_mission_acceptance_and_human_gate(rig, mission):
    body = render_acceptance_body(rig, mission)

    assert "The requested behavior exists." in body
    assert "Automated tests pass." in body
    assert "human" in body.lower()


def test_acceptance_body_without_human_gate_omits_blocked_task(rig, tmp_path):
    path = tmp_path / "mission.yaml"
    path.write_text(
        _MISSION_YAML.replace("human_acceptance: true", "human_acceptance: false"),
        encoding="utf-8",
    )
    loaded = load_mission(path, rig)

    body = render_acceptance_body(rig, loaded)

    assert "The requested behavior exists." in body
    assert "human gate" not in body.lower()
