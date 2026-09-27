from pathlib import Path

import pytest

from hermes_factory.errors import SpecError
from hermes_factory.mission_loader import load_mission, topological_slices
from hermes_factory.rig_loader import load_rig

_BASE = """\
version: 1
id: first-delivery
title: First delivery
intent: Produce one small, verified result without expanding scope.
workflow: delivery
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
"""

_CYCLE = """\
version: 1
id: first-delivery
title: First delivery
intent: Produce one small, verified result without expanding scope.
workflow: delivery
acceptance:
  - The requested behavior exists.
slices:
  - id: implement
    title: Implement the requested behavior
    goal: Make the smallest code change satisfying the mission.
    depends_on: [polish]
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

_NO_PROOF_SLICE = """\
version: 1
id: first-delivery
title: First delivery
intent: Produce one small, verified result without expanding scope.
workflow: delivery
acceptance:
  - The requested behavior exists.
slices:
  - id: implement
    title: Implement the requested behavior
    goal: Make the smallest code change satisfying the mission.
    depends_on: []
    acceptance:
      - A failing test was observed before implementation.
"""

_TWO_SLICES = """\
version: 1
id: first-delivery
title: First delivery
intent: Produce one small, verified result without expanding scope.
workflow: delivery
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


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "mission.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_mission_parses_valid_example(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    mission = load_mission(_write(tmp_path, _TWO_SLICES), rig)

    assert mission.id == "first-delivery"
    assert mission.workflow == "delivery"
    assert [slice_spec.id for slice_spec in mission.slices] == ["implement", "polish"]
    assert topological_slices(mission) == tuple(mission.slices)
    assert mission.slices[1].depends_on == ("implement",)
    assert mission.slices[0].proof.commands == ("uv run pytest -q",)
    assert mission.slices[0].proof.changed_files_required is True


def test_unknown_workflow_is_rejected(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    path = _write(tmp_path, _BASE.replace("workflow: delivery", "workflow: missing"))

    with pytest.raises(SpecError, match="unknown workflow 'missing'"):
        load_mission(path, rig)


def test_duplicate_slice_id_is_rejected(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    text = _BASE + """  - id: implement
    title: Duplicate slice
    goal: Duplicate slice.
    depends_on: []
    acceptance:
      - Duplicate.
    proof:
      commands:
        - uv run pytest -q
      artifacts: []
      changed_files_required: true
"""
    path = _write(tmp_path, text)

    with pytest.raises(SpecError, match="duplicate slice id 'implement'"):
        load_mission(path, rig)


def test_unknown_dependency_is_rejected(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    path = _write(tmp_path, _BASE.replace("depends_on: []", "depends_on: [ghost]"))

    with pytest.raises(SpecError, match="unknown slice 'ghost'"):
        load_mission(path, rig)


def test_dependency_cycle_is_rejected(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    path = _write(tmp_path, _CYCLE)

    with pytest.raises(SpecError, match="cycle"):
        load_mission(path, rig)


def test_empty_intent_is_rejected(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    path = _write(
        tmp_path,
        _BASE.replace("intent: Produce one small, verified result without expanding scope.", "intent: ' '"),
    )

    with pytest.raises(SpecError, match="intent must not be empty"):
        load_mission(path, rig)


def test_empty_acceptance_is_rejected(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    text = _BASE.replace("acceptance:\n  - The requested behavior exists.", "acceptance: []")
    path = _write(tmp_path, text)

    with pytest.raises(SpecError, match="acceptance must not be empty"):
        load_mission(path, rig)


@pytest.mark.parametrize(
    "artifact",
    ["reports/report.txt", "reports\\report.txt", "../report.txt", ".hidden", ".", ".."],
)
def test_unsafe_artifact_filename_is_rejected(
    valid_rig: Path, tmp_path: Path, artifact: str
):
    rig = load_rig(valid_rig)
    text = _BASE.replace("artifacts: []", f"artifacts: [{artifact}]")

    with pytest.raises(SpecError, match="artifact must be a safe filename"):
        load_mission(_write(tmp_path, text), rig)


def test_duplicate_artifact_filename_is_rejected(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    text = _BASE.replace("artifacts: []", "artifacts: [report.txt, report.txt]")

    with pytest.raises(SpecError, match="duplicate filenames"):
        load_mission(_write(tmp_path, text), rig)


def test_case_insensitive_duplicate_artifact_filename_is_rejected(
    valid_rig: Path, tmp_path: Path
):
    rig = load_rig(valid_rig)
    text = _BASE.replace("artifacts: []", "artifacts: [report.txt, REPORT.TXT]")

    with pytest.raises(SpecError, match="duplicate filenames"):
        load_mission(_write(tmp_path, text), rig)


def test_missing_proof_is_rejected_when_rig_requires_it(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    path = _write(tmp_path, _NO_PROOF_SLICE)

    with pytest.raises(SpecError, match="slice 'implement' must define proof"):
        load_mission(path, rig)


def test_missing_proof_is_allowed_when_rig_does_not_require_it(
    no_proof_rig: Path, tmp_path: Path
):
    rig = load_rig(no_proof_rig)
    mission = load_mission(_write(tmp_path, _NO_PROOF_SLICE), rig)

    assert mission.slices[0].proof is None


def test_mission_may_tighten_human_acceptance(no_proof_rig: Path, tmp_path: Path):
    rig = load_rig(no_proof_rig)
    text = _BASE.replace("workflow: delivery", "workflow: delivery\nhuman_acceptance: true")
    mission = load_mission(_write(tmp_path, text), rig)

    assert mission.human_acceptance is True


def test_mission_tightening_human_acceptance_requires_declared_authority(
    valid_rig: Path, tmp_path: Path
):
    rig = load_rig(valid_rig)
    text = _BASE.replace("workflow: delivery", "workflow: delivery\nhuman_acceptance: true")

    with pytest.raises(SpecError, match="decision_authority"):
        load_mission(_write(tmp_path, text), rig)


def test_mission_may_not_weaken_human_acceptance(human_rig: Path, tmp_path: Path):
    rig = load_rig(human_rig)
    text = _BASE.replace("workflow: delivery", "workflow: delivery\nhuman_acceptance: false")

    with pytest.raises(SpecError, match="weaken"):
        load_mission(_write(tmp_path, text), rig)


@pytest.fixture
def no_proof_rig(valid_rig: Path) -> Path:
    rig = valid_rig.parent / "no-proof-rig.yaml"
    rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "require_proof: true",
            "require_proof: false, decision_authority: coordinator",
        ),
        encoding="utf-8",
    )
    return rig


@pytest.fixture
def human_rig(valid_rig: Path) -> Path:
    rig = valid_rig.parent / "human-rig.yaml"
    rig.write_text(
        valid_rig.read_text(encoding="utf-8").replace(
            "human_acceptance: false",
            "human_acceptance: true, decision_authority: human",
        ),
        encoding="utf-8",
    )
    return rig
