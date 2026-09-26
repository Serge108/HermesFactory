from pathlib import Path

import pytest

from hermes_factory.board import BoardApplyResult, apply_mission, ensure_board
from hermes_factory.compiler import compile_mission
from hermes_factory.errors import HermesCommandError
from hermes_factory.mission_loader import load_mission
from hermes_factory.rig_loader import load_rig


class FakeHermesClient:
    def __init__(self, board_lists: list[object]) -> None:
        self.board_lists = board_lists
        self.json_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []

    def run_json(self, args: list[str]) -> object:
        self.json_calls.append(args)
        return self.board_lists.pop(0)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        return ""


def test_existing_active_board_is_reused(valid_rig: Path):
    client = FakeHermesClient(
        [[{"slug": "other", "archived": False}, {"slug": "demo-rig", "archived": False}]]
    )

    result = ensure_board(client, load_rig(valid_rig))

    assert result == BoardApplyResult(slug="demo-rig", created=False)
    assert client.json_calls == [["kanban", "boards", "list", "--json", "--all"]]
    assert client.run_calls == []


def test_missing_board_is_created_with_exact_arguments(valid_rig: Path):
    client = FakeHermesClient([[]])
    rig = load_rig(valid_rig)

    result = ensure_board(client, rig)

    assert result == BoardApplyResult(slug="demo-rig", created=True)
    assert client.json_calls == [["kanban", "boards", "list", "--json", "--all"]]
    assert client.run_calls == [
        [
            "kanban",
            "boards",
            "create",
            "demo-rig",
            "--name",
            "Demo Rig",
            "--description",
            "Managed by Hermes Factory",
            "--default-workdir",
            str(rig.project.cwd),
        ]
    ]


def test_second_ensure_reuses_board_from_updated_listing(valid_rig: Path):
    client = FakeHermesClient(
        [[], [{"slug": "demo-rig", "archived": False}]]
    )
    rig = load_rig(valid_rig)

    first = ensure_board(client, rig)
    second = ensure_board(client, rig)

    assert first.created is True
    assert second.created is False
    assert len(client.run_calls) == 1
    assert all("switch" not in call for call in client.json_calls + client.run_calls)


def test_archived_same_slug_does_not_count_as_existing(valid_rig: Path):
    client = FakeHermesClient([[{"slug": "demo-rig", "archived": True}]])

    result = ensure_board(client, load_rig(valid_rig))

    assert result.created is True
    assert len(client.run_calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        None,
        {"boards": []},
        ["demo-rig"],
        [{}],
        [{"slug": "demo-rig", "archived": "false"}],
    ],
)
def test_malformed_board_list_fails_with_clear_error(valid_rig: Path, response: object):
    client = FakeHermesClient([response])

    with pytest.raises(
        HermesCommandError, match="Hermes returned malformed board list JSON"
    ):
        ensure_board(client, load_rig(valid_rig))

    assert client.run_calls == []


_MISSION = """\
version: 1
id: demo-mission
title: Demo mission
intent: Ship one verified behavior without expanding scope.
workflow: delivery
human_acceptance: false
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


class FakeTaskClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.json_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []

    def run_json(self, args: list[str]) -> object:
        self.json_calls.append(args)
        return self.responses.pop(0)

    def run(self, args: list[str]) -> str:
        self.run_calls.append(args)
        return ""


def _flag_values(args: list[str], flag: str) -> list[str]:
    values: list[str] = []
    start = 0
    while True:
        try:
            index = args.index(flag, start)
        except ValueError:
            break
        values.append(args[index + 1])
        start = index + 1
    return values


@pytest.fixture
def plan(valid_rig: Path, tmp_path: Path):
    rig = load_rig(valid_rig)
    path = tmp_path / "mission.yaml"
    path.write_text(_MISSION, encoding="utf-8")
    return rig, compile_mission(rig, load_mission(path, rig))


def test_mission_tasks_are_created_in_dependency_order_with_flags(plan):
    rig, mission_plan = plan
    client = FakeTaskClient(
        [
            {"id": "task-1", "status": "ready", "title": "Implement the requested behavior"},
            {"id": "task-2", "status": "ready", "title": "Review the requested behavior"},
            {"id": "task-3", "status": "ready", "title": "Accept Demo mission"},
        ]
    )

    applied = apply_mission(client, rig, mission_plan)

    assert applied.task_ids == {
        "implement:implementation": "task-1",
        "implement:review": "task-2",
        "acceptance": "task-3",
    }
    assert len(client.json_calls) == 3
    for task, call in zip(mission_plan.tasks, client.json_calls, strict=True):
        assert call[:4] == ["kanban", "--board", "demo-rig", "create"]
        assert call[4] == task.title
        assert _flag_values(call, "--body") == [task.body]
        assert _flag_values(call, "--idempotency-key") == [task.idempotency_key]
        assert _flag_values(call, "--max-retries") == ["2"]
        assert _flag_values(call, "--max-runtime") == ["30m"]
        assert _flag_values(call, "--completion-contract") == ["local-only"]
        assert _flag_values(call, "--workspace") == [f"dir:{rig.project.cwd}"]
        assert _flag_values(call, "--created-by") == ["hermes-factory"]
        assert "--json" in call
    assert _flag_values(client.json_calls[1], "--parent") == ["task-1"]
    assert _flag_values(client.json_calls[2], "--parent") == ["task-2"]
    assert _flag_values(client.json_calls[0], "--parent") == []
    for call in client.json_calls:
        assert _flag_values(call, "--initial-status") == []


def test_human_acceptance_blocks_acceptance_task_without_assignee(
    valid_rig: Path, tmp_path: Path
):
    rig = load_rig(valid_rig)
    path = tmp_path / "mission.yaml"
    path.write_text(
        _MISSION.replace("human_acceptance: false", "human_acceptance: true"),
        encoding="utf-8",
    )
    mission_plan = compile_mission(rig, load_mission(path, rig))
    client = FakeTaskClient(
        [
            {"id": "task-1", "status": "ready", "title": "Implement the requested behavior"},
            {"id": "task-2", "status": "ready", "title": "Review the requested behavior"},
            {"id": "task-3", "status": "blocked", "title": "Accept Demo mission"},
        ]
    )

    apply_mission(client, rig, mission_plan)

    acceptance_call = client.json_calls[2]
    assert _flag_values(acceptance_call, "--initial-status") == ["blocked"]
    assert _flag_values(acceptance_call, "--assignee") == []


def test_non_blocked_mission_assigns_acceptance_to_acceptance_seat(plan):
    rig, mission_plan = plan
    client = FakeTaskClient(
        [
            {"id": "task-1", "status": "ready", "title": "Implement the requested behavior"},
            {"id": "task-2", "status": "ready", "title": "Review the requested behavior"},
            {"id": "task-3", "status": "ready", "title": "Accept Demo mission"},
        ]
    )

    apply_mission(client, rig, mission_plan)

    assert _flag_values(client.json_calls[0], "--assignee") == ["builder"]
    assert _flag_values(client.json_calls[1], "--assignee") == ["reviewer"]
    assert _flag_values(client.json_calls[2], "--assignee") == ["builder"]


def test_applying_same_plan_twice_returns_same_ids_and_keeps_keys(plan):
    rig, mission_plan = plan
    responses = [
        {"id": "task-1", "status": "ready", "title": "Implement the requested behavior"},
        {"id": "task-2", "status": "ready", "title": "Review the requested behavior"},
        {"id": "task-3", "status": "ready", "title": "Accept Demo mission"},
    ] * 2
    client = FakeTaskClient(responses)

    first = apply_mission(client, rig, mission_plan)
    second = apply_mission(client, rig, mission_plan)

    assert second.task_ids == first.task_ids
    assert len(client.json_calls) == 6
    assert _flag_values(client.json_calls[3], "--idempotency-key") == _flag_values(
        client.json_calls[0], "--idempotency-key"
    )
    assert _flag_values(client.json_calls[4], "--idempotency-key") == _flag_values(
        client.json_calls[1], "--idempotency-key"
    )
    assert _flag_values(client.json_calls[5], "--idempotency-key") == _flag_values(
        client.json_calls[2], "--idempotency-key"
    )


def test_apply_subscribes_every_created_or_reused_task_from_rig(
    valid_rig: Path, tmp_path: Path
):
    rig_text = valid_rig.read_text(encoding="utf-8").replace(
        "policies:",
        "notifications:\n"
        "  task_subscriptions:\n"
        "    - platform: telegram\n"
        "      chat_id: '1234567890'\n"
        "      user_id: '1234567890'\n"
        "      chat_type: dm\n"
        "      notifier_profile: default\n"
        "      delivery_mode: notify\n"
        "policies:",
    )
    valid_rig.write_text(rig_text, encoding="utf-8")
    rig = load_rig(valid_rig)
    mission_path = tmp_path / "mission.yaml"
    mission_path.write_text(_MISSION, encoding="utf-8")
    mission_plan = compile_mission(rig, load_mission(mission_path, rig))
    client = FakeTaskClient(
        [
            {"id": "task-1", "status": "ready", "title": "Implement the requested behavior"},
            {"id": "task-2", "status": "ready", "title": "Review the requested behavior"},
            {"id": "task-3", "status": "ready", "title": "Accept Demo mission"},
        ]
    )

    apply_mission(client, rig, mission_plan)

    assert client.run_calls == [
        [
            "kanban", "--board", "demo-rig", "notify-subscribe", task_id,
            "--platform", "telegram",
            "--chat-id", "1234567890",
            "--user-id", "1234567890",
            "--chat-type", "dm",
            "--notifier-profile", "default",
            "--delivery-mode", "notify",
        ]
        for task_id in ("task-1", "task-2", "task-3")
    ]


def test_malformed_task_response_fails_with_clear_error(plan):
    rig, mission_plan = plan
    client = FakeTaskClient(
        [
            {"id": "task-1", "status": "ready", "title": "Implement the requested behavior"},
            {"id": "", "status": "ready", "title": "Review the requested behavior"},
            {"id": "task-3", "status": "ready", "title": "Accept Demo mission"},
        ]
    )

    with pytest.raises(HermesCommandError, match="Hermes returned malformed task JSON"):
        apply_mission(client, rig, mission_plan)

    assert len(client.json_calls) == 2


def test_seat_skills_become_repeated_skill_flags(tmp_path: Path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    rig_path = tmp_path / "rig.yaml"
    rig_path.write_text(
        f"""
version: 1
project: {{slug: demo, name: Demo, cwd: '{cwd.as_posix()}'}}
board: {{slug: demo-rig, name: Demo Rig}}
seats:
  builder: {{profile: builder, role: implementation, skills: [tdd, git-workflow]}}
  reviewer: {{profile: reviewer, role: independent_review, skills: [github-code-review]}}
workflows:
  delivery: {{implementation_seat: builder, review_seat: reviewer, acceptance_seat: builder}}
policies: {{require_proof: true, human_acceptance: false, max_retries: 2, max_runtime: 30m, completion_contract: local-only}}
""",
        encoding="utf-8",
    )
    rig = load_rig(rig_path)
    mission_path = tmp_path / "mission.yaml"
    mission_path.write_text(_MISSION, encoding="utf-8")
    mission_plan = compile_mission(rig, load_mission(mission_path, rig))
    client = FakeTaskClient(
        [
            {"id": "task-1", "status": "ready", "title": "Implement the requested behavior"},
            {"id": "task-2", "status": "ready", "title": "Review the requested behavior"},
            {"id": "task-3", "status": "ready", "title": "Accept Demo mission"},
        ]
    )

    apply_mission(client, rig, mission_plan)

    assert _flag_values(client.json_calls[0], "--skill") == ["tdd", "git-workflow"]
    assert _flag_values(client.json_calls[1], "--skill") == ["github-code-review"]
    assert _flag_values(client.json_calls[2], "--skill") == ["tdd", "git-workflow"]
