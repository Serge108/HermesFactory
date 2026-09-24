from pathlib import Path

import pytest


@pytest.fixture
def valid_rig(tmp_path: Path) -> Path:
    cwd = tmp_path / "project"
    cwd.mkdir()
    rig = tmp_path / "rig.yaml"
    rig.write_text(
        f"""
version: 1
project: {{slug: demo, name: Demo, cwd: '{cwd.as_posix()}'}}
board: {{slug: demo-rig, name: Demo Rig}}
seats:
  builder: {{profile: builder, role: implementation, skills: []}}
  reviewer: {{profile: reviewer, role: independent_review, skills: []}}
workflows:
  delivery: {{implementation_seat: builder, review_seat: reviewer, acceptance_seat: builder}}
policies: {{require_proof: true, human_acceptance: false, max_retries: 2, max_runtime: 30m, completion_contract: local-only}}
""",
        encoding="utf-8",
    )
    return rig
