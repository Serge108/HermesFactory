from pathlib import Path
import re

from hermes_factory.stall_taxonomy import stall_taxonomy

_TESTS_DIR = Path(__file__).parent


def _existing_test_names() -> set[str]:
    names: set[str] = set()
    for path in _TESTS_DIR.glob("test_*.py"):
        names |= set(re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.M))
    return names


def test_every_stall_class_names_an_existing_regression_test():
    """A stall class is not closed until a real test guards its recovery."""
    names = _existing_test_names()

    unresolved = {
        item.code: item.regression_test
        for item in stall_taxonomy()
        if item.regression_test not in names
    }

    assert unresolved == {}


def test_provider_park_class_is_the_code_the_reconciler_implements():
    """A class is executable only while the reconciler actually selects its code."""
    from hermes_factory import reconcile as reconcile_module

    classes = {item.code for item in stall_taxonomy()}

    assert reconcile_module._STALL_TERMINAL_PROVIDER_PARK in classes


def test_unparseable_plan_class_is_the_code_the_reconciler_implements():
    """A class is executable only while the reconciler actually selects its code."""
    from hermes_factory import reconcile as reconcile_module

    classes = {item.code for item in stall_taxonomy()}

    assert reconcile_module._STALL_UNPARSEABLE_RECOVERY_PLAN in classes


def test_taxonomy_covers_timeout_capability_dispatch_dependency_and_human_classes():
    classes = {item.code: item for item in stall_taxonomy()}

    assert set(classes) >= {
        "worker_timeout",
        "progress_checkpoint",
        "decomposer_timeout",
        "recovery_coordinator_insufficient_kanban_authority",
        "awaiting_dispatch",
        "normal_dependency_wait",
        "stale_runner",
        "stale_dependency_edge",
        "review_rejected",
        "no_agent_activity",
        "terminal_provider_park",
        "unparseable_recovery_plan",
        "human_gate",
        "corrupt_state",
        "unknown_operational_stall",
    }
    assert classes["worker_timeout"].automatic is True
    provider_park = classes["terminal_provider_park"]
    assert provider_park.automatic is True
    assert provider_park.recovery_owner == "factory"
    assert provider_park.regression_test == (
        "test_reconcile_reopens_provider_parked_factory_card_once"
    )
    unparseable = classes["unparseable_recovery_plan"]
    assert unparseable.automatic is True
    assert unparseable.recovery_owner == "factory"
    assert "operator" not in unparseable.fallback.lower()
    assert unparseable.regression_test == (
        "test_reconcile_disposes_an_unparseable_recovery_plan_once"
    )
    assert classes["progress_checkpoint"].recovery_owner == "factory"
    assert classes["decomposer_timeout"].recovery_owner == "factory"
    unknown = classes["unknown_operational_stall"]
    assert unknown.automatic is True
    assert unknown.recovery_owner == "coordinator"
    assert "structured outcome" in unknown.recovery
    assert "new taxonomy" not in unknown.recovery.lower()
    assert "operator" not in unknown.fallback.lower()
    assert unknown.regression_test == (
        "test_reconcile_routes_unclassified_block_to_coordinator"
    )
    assert (
        classes["recovery_coordinator_insufficient_kanban_authority"].recovery_owner
        == "factory"
    )
    assert all(
        item.detection
        and item.cause
        and item.recovery
        and item.authority
        and item.verification
        and item.fallback
        and item.regression_test
        for item in classes.values()
    )