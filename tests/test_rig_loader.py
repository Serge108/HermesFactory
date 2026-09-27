from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hermes_factory.errors import SpecError
from hermes_factory.rig_loader import load_rig, parse_duration


def write_rig(tmp_path: Path, *, cwd: str) -> Path:
    spec = tmp_path / "rig.yaml"
    spec.write_text(
        f"""
version: 1
project: {{slug: demo, name: Demo, cwd: '{cwd}'}}
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
    return spec


def test_load_rig_preserves_windows_path_with_spaces(tmp_path: Path):
    cwd = tmp_path / "Project With Spaces"
    cwd.mkdir()

    rig = load_rig(write_rig(tmp_path, cwd=cwd.as_posix()))

    assert rig.project.cwd == cwd.resolve()
    assert rig.workflows["delivery"].review_seat == "reviewer"


def test_load_rig_defaults_decision_authority_to_coordinator(tmp_path: Path):
    rig = load_rig(write_rig(tmp_path, cwd="."))

    assert rig.policies.decision_authority == "coordinator"


def test_load_rig_requires_explicit_authority_with_human_acceptance(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, "human_acceptance: false", "human_acceptance: true")

    with pytest.raises(SpecError, match="decision_authority"):
        load_rig(spec)


def test_load_rig_rejects_unknown_workflow_seat(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    text = spec.read_text(encoding="utf-8").replace("review_seat: reviewer", "review_seat: missing")
    spec.write_text(text, encoding="utf-8")

    with pytest.raises(SpecError, match="unknown seat 'missing'"):
        load_rig(spec)


def replace_in_rig(spec: Path, old: str, new: str) -> None:
    spec.write_text(spec.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [
        ("version: 1", "version: 1\nextra: value", "rig"),
        ("project: {slug: demo, name: Demo, cwd:", "project: {extra: value, slug: demo, name: Demo, cwd:", "project"),
        ("board: {slug: demo-rig, name: Demo Rig}", "board: {slug: demo-rig, name: Demo Rig, extra: value}", "board"),
        ("builder: {profile: builder, role: implementation, skills: []}", "builder: {profile: builder, role: implementation, skills: [], extra: value}", "seat 'builder'"),
        ("delivery: {implementation_seat: builder, review_seat: reviewer, acceptance_seat: builder}", "delivery: {implementation_seat: builder, review_seat: reviewer, acceptance_seat: builder, extra: value}", "workflow 'delivery'"),
        ("completion_contract: local-only}", "completion_contract: local-only, extra: value}", "policies"),
    ],
)
def test_load_rig_rejects_unknown_keys_at_every_level(
    tmp_path: Path, old: str, new: str, field: str
):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, old, new)

    with pytest.raises(SpecError, match=rf"{field}.*unknown key 'extra'"):
        load_rig(spec)


def test_load_rig_rejects_duplicate_keys(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, "version: 1", "version: 1\nversion: 1")

    with pytest.raises(SpecError, match="duplicate key 'version'"):
        load_rig(spec)


def test_load_rig_rejects_seat_names_that_collide_after_trimming(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(
        spec,
        "  builder: {profile: builder, role: implementation, skills: []}",
        "  builder: {profile: builder, role: implementation, skills: []}\n"
        "  ' builder ': {profile: other, role: implementation, skills: []}",
    )

    with pytest.raises(SpecError, match="seat slug.*collision.*builder"):
        load_rig(spec)


def test_load_rig_rejects_workflow_names_that_collide_after_trimming(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    workflow = "{implementation_seat: builder, review_seat: reviewer, acceptance_seat: builder}"
    replace_in_rig(
        spec,
        f"  delivery: {workflow}",
        f"  delivery: {workflow}\n  ' delivery ': {workflow}",
    )

    with pytest.raises(SpecError, match="workflow slug.*collision.*delivery"):
        load_rig(spec)


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [
        ("slug: demo", "slug: Demo", "project.slug"),
        ("slug: demo-rig", "slug: demo_", "board.slug"),
        ("builder:", "builder_:", "seat"),
        ("delivery:", "delivery_:", "workflow"),
    ],
)
def test_load_rig_rejects_invalid_slugs(
    tmp_path: Path, old: str, new: str, field: str
):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, old, new)

    with pytest.raises(SpecError, match=rf"{field}.*valid slug"):
        load_rig(spec)


def test_load_rig_rejects_missing_cwd(tmp_path: Path):
    spec = write_rig(tmp_path, cwd="missing")

    with pytest.raises(SpecError, match="project.cwd.*exist"):
        load_rig(spec)


def test_load_rig_rejects_cwd_that_is_not_directory(tmp_path: Path):
    cwd = tmp_path / "file.txt"
    cwd.write_text("not a directory", encoding="utf-8")
    spec = write_rig(tmp_path, cwd=cwd.as_posix())

    with pytest.raises(SpecError, match="project.cwd.*directory"):
        load_rig(spec)


def test_load_rig_rejects_empty_role(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, "role: implementation", "role: ' '")

    with pytest.raises(SpecError, match="seat 'builder'.role.*empty"):
        load_rig(spec)


def test_load_rig_rejects_unsupported_version(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, "version: 1", "version: 2")

    with pytest.raises(SpecError, match="unsupported version 2"):
        load_rig(spec)


def test_load_rig_rejects_max_retries_less_than_one(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, "max_retries: 2", "max_retries: 0")

    with pytest.raises(SpecError, match="max_retries.*at least 1"):
        load_rig(spec)


@pytest.mark.parametrize("value", ["false", "1", "null"])
def test_load_rig_rejects_human_acceptance_that_is_not_boolean(
    tmp_path: Path, value: str
):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, "human_acceptance: false", f"human_acceptance: '{value}'")

    with pytest.raises(SpecError, match="human_acceptance.*boolean"):
        load_rig(spec)


def test_load_rig_trims_identifiers_without_normalizing_them(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, "profile: builder", "profile: ' Builder.Profile '")

    rig = load_rig(spec)

    assert rig.seats["builder"].profile == "Builder.Profile"


def test_load_rig_stores_skills_as_tuple(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(spec, "skills: []", "skills: [python, review]", )

    rig = load_rig(spec)

    assert rig.seats["builder"].skills == ("python", "review")


def test_loaded_rig_rejects_field_reassignment(tmp_path: Path):
    rig = load_rig(write_rig(tmp_path, cwd="."))

    with pytest.raises(FrozenInstanceError):
        rig.version = 2


def test_loaded_rig_seats_mapping_is_read_only(tmp_path: Path):
    rig = load_rig(write_rig(tmp_path, cwd="."))

    with pytest.raises(TypeError):
        rig.seats["builder"] = rig.seats["reviewer"]


def test_loaded_rig_workflows_mapping_is_read_only(tmp_path: Path):
    rig = load_rig(write_rig(tmp_path, cwd="."))

    with pytest.raises(TypeError):
        rig.workflows["replacement"] = rig.workflows["delivery"]


def test_load_rig_accepts_task_notification_subscriptions(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(
        spec,
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

    rig = load_rig(spec)

    assert len(rig.notifications.task_subscriptions) == 1
    subscription = rig.notifications.task_subscriptions[0]
    assert subscription.platform == "telegram"
    assert subscription.chat_id == "1234567890"
    assert subscription.user_id == "1234567890"
    assert subscription.chat_type == "dm"
    assert subscription.notifier_profile == "default"
    assert subscription.delivery_mode == "notify"


def test_load_rig_rejects_invalid_notification_delivery_mode(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(
        spec,
        "policies:",
        "notifications:\n"
        "  task_subscriptions:\n"
        "    - platform: telegram\n"
        "      chat_id: '1234567890'\n"
        "      user_id: '1234567890'\n"
        "      chat_type: dm\n"
        "      notifier_profile: default\n"
        "      delivery_mode: loud\n"
        "policies:",
    )

    with pytest.raises(SpecError, match="delivery_mode.*notify"):
        load_rig(spec)


def test_parse_duration_rejects_invalid_value():
    with pytest.raises(SpecError, match="duration"):
        parse_duration("soon")


def test_load_rig_accepts_coordinator_authority_and_reviewed_slice_commits(
    tmp_path: Path,
):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(
        spec,
        "completion_contract: local-only}",
        "completion_contract: local-only, decision_authority: coordinator, "
        "git_commit_policy: reviewed-slices}",
    )

    rig = load_rig(spec)

    assert rig.policies.decision_authority == "coordinator"
    assert rig.policies.git_commit_policy == "reviewed-slices"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("decision_authority", "committee", "decision_authority.*coordinator"),
        ("git_commit_policy", "always", "git_commit_policy.*reviewed-slices"),
    ],
)
def test_load_rig_rejects_invalid_autonomy_policy(
    tmp_path: Path, field: str, value: str, expected: str
):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(
        spec,
        "completion_contract: local-only}",
        f"completion_contract: local-only, {field}: {value}}}",
    )

    with pytest.raises(SpecError, match=expected):
        load_rig(spec)


def test_load_rig_defaults_coordinator_runtime_to_max_runtime(tmp_path: Path):
    rig = load_rig(write_rig(tmp_path, cwd="."))

    assert rig.policies.max_runtime == "30m"
    assert rig.policies.coordinator_runtime == "30m"


def test_load_rig_reads_explicit_coordinator_runtime(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(
        spec,
        "max_runtime: 30m, completion_contract",
        "max_runtime: 30m, coordinator_runtime: 45m, completion_contract",
    )

    rig = load_rig(spec)

    assert rig.policies.max_runtime == "30m"
    assert rig.policies.coordinator_runtime == "45m"


def test_load_rig_rejects_invalid_coordinator_runtime(tmp_path: Path):
    spec = write_rig(tmp_path, cwd=".")
    replace_in_rig(
        spec,
        "completion_contract: local-only}",
        "completion_contract: local-only, coordinator_runtime: soon}",
    )

    with pytest.raises(SpecError, match="invalid duration"):
        load_rig(spec)
