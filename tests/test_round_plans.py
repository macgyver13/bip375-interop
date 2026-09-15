from bip375_interop.suites import get_suite
from bip375_interop.models import Scenario
from bip375_interop.suites import scenario_rounds


def test_suite_rounds_are_protocol_specific():
    signers = ("a", "b")
    assert get_suite("bip375").rounds(signers) == (
        ("contribute", signers), ("sign", signers)
    )
    assert get_suite("musig2-sp").rounds(signers) == (
        ("round1", signers), ("round2", signers)
    )


def test_plain_bip375_schedule_returns_early_signers_for_second_pass():
    scenario = Scenario.from_dict({
        "name": "two", "suite": "bip375", "network": "regtest",
        "signers": [
            {"name": "a", "backend": "seedsigner", "seed_id": "test-a"},
            {"name": "b", "backend": "seedsigner", "seed_id": "test-b"},
        ],
        "suite_config": {"contribution_mode": "per-input"},
    })
    assert [(item.name, item.signers) for item in scenario_rounds(scenario)] == [
        ("contribute", ("a",)),
        ("resolve-sign", ("b",)),
        ("sign", ("a",)),
    ]
