from pathlib import Path

from bip375_interop.config import load_scenario
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
        "outputs": [{"type": "silent-payment", "amount_sat": 100_000}],
    })
    assert [(item.name, item.signers) for item in scenario_rounds(scenario)] == [
        ("contribute", ("a",)),
        ("resolve-sign", ("b",)),
        ("sign", ("a",)),
    ]


def test_plain_bip375_schedule_is_single_pass_without_an_sp_output():
    scenario = Scenario.from_dict({
        "name": "spend-only", "suite": "bip375", "network": "regtest",
        "signers": [
            {"name": "a", "backend": "coldcard", "seed_id": "test-a"},
            {"name": "b", "backend": "jade", "seed_id": "test-b"},
        ],
        "suite_config": {"contribution_mode": "per-input"},
        "outputs": [{"type": "p2wpkh", "amount_sat": 100_000}],
    })
    assert [(item.name, item.signers) for item in scenario_rounds(scenario)] == [
        ("resolve-sign", ("a", "b")),
    ]


def test_redundant_sign_round_appends_a_pass_on_the_completed_psbt():
    scenario = Scenario.from_dict({
        "name": "redundant", "suite": "bip375", "network": "regtest",
        "signers": [
            {"name": "a", "backend": "coldcard", "seed_id": "test-a"},
            {"name": "b", "backend": "jade", "seed_id": "test-b"},
        ],
        "suite_config": {"contribution_mode": "per-input", "redundant_sign_round": True},
        "outputs": [{"type": "silent-payment", "amount_sat": 100_000}],
    })
    assert [(item.name, item.signers) for item in scenario_rounds(scenario)] == [
        ("contribute", ("a",)),
        ("resolve-sign", ("b",)),
        ("sign", ("a",)),
        ("redundant-sign", ("a", "b")),
    ]


def test_coldcard_jade_two_way_scenario_uses_two_owner_schedule():
    scenario = load_scenario(
        Path(__file__).parents[1] / "scenarios/bip375-coldcard-jade-two-way.yaml"
    )

    assert [(item.name, item.signers) for item in scenario_rounds(scenario)] == [
        ("contribute", ("coldcard-a",)),
        ("resolve-sign", ("jade-b",)),
        ("sign", ("coldcard-a",)),
    ]
