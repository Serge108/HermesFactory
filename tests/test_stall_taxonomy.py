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


def test_taxonomy_covers_timeout_capability_dispatch_dependency_and_human_classes():
    classes = {item.code: item for item in stall_taxonomy()}

    assert set(classes) >= {
        "worker_timeout",
        "recovery_coordinator_insufficient_kanban_authority",
        "awaiting_dispatch",
        "normal_dependency_wait",
        "stale_runner",
        "stale_dependency_edge",
        "review_rejected",
        "human_gate",
        "corrupt_state",
        "unknown_operational_stall",
    }
    assert classes["worker_timeout"].automatic is True
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