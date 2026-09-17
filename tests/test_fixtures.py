from __future__ import annotations

import pytest

from bip375_interop.errors import ConfigurationError
from bip375_interop.fixtures import build_bip375_fixture
from bip375_interop.models import Scenario
from bip375_interop.psbt_maps import parse_psbt


def test_generated_fixture_is_unresolved_and_has_one_owned_input_per_signer() -> None:
    pytest.importorskip("embit")
    scenario = Scenario.from_dict({
        "name": "two-way",
        "suite": "bip375",
        "network": "regtest",
        "signers": [
            {"name": "one", "backend": "seedsigner", "seed_id": "test-a"},
            {"name": "two", "backend": "jade", "seed_id": "test-b"},
        ],
        "suite_config": {"contribution_mode": "per-input"},
        "inputs": [
            {"owner": "one", "type": "p2wpkh", "amount_sat": 100_000},
            {"owner": "two", "type": "p2wpkh", "amount_sat": 110_000},
        ],
        "outputs": [{"type": "silent-payment", "amount_sat": 209_000}],
    })

    psbt = parse_psbt(build_bip375_fixture(scenario))

    assert len(psbt.inputs) == 2
    assert len(psbt.outputs) == 1
    assert psbt.outputs[0].get(b"\x04") is None
    assert psbt.outputs[0].get(b"\x09") is not None
    assert all(item.get(b"\x03") == b"\x01\x00\x00\x00" for item in psbt.inputs)


def test_generated_fixture_supports_p2tr_inputs() -> None:
    pytest.importorskip("embit")
    scenario = Scenario.from_dict({
        "name": "taproot-two-way",
        "suite": "bip375",
        "network": "regtest",
        "signers": [
            {"name": "one", "backend": "coldcard", "seed_id": "test-a"},
            {"name": "two", "backend": "jade", "seed_id": "test-b"},
        ],
        "suite_config": {"contribution_mode": "per-input"},
        "inputs": [
            {"owner": "one", "type": "p2wpkh", "amount_sat": 100_000},
            {"owner": "two", "type": "p2tr", "amount_sat": 110_000},
        ],
        "outputs": [{"type": "silent-payment", "amount_sat": 209_000}],
    })

    psbt = parse_psbt(build_bip375_fixture(scenario))

    assert len(psbt.inputs) == 2
    # PSBT_IN_TAP_INTERNAL_KEY (0x17) is present only on the P2TR input.
    assert psbt.inputs[0].get(b"\x17") is None
    assert psbt.inputs[1].get(b"\x17") is not None
    # SIGHASH_ALL on both inputs (see fixtures.py: real Jade firmware
    # normalizes a taproot input's sighash type to explicit SIGHASH_ALL).
    assert psbt.inputs[0].get(b"\x03") == b"\x01\x00\x00\x00"
    assert psbt.inputs[1].get(b"\x03") == b"\x01\x00\x00\x00"


def test_generated_fixture_supports_sp_spend_bip376_inputs() -> None:
    pytest.importorskip("embit")
    scenario = Scenario.from_dict({
        "name": "sp-spend-two-way",
        "suite": "bip375",
        "network": "regtest",
        "signers": [
            {"name": "one", "backend": "coldcard", "seed_id": "test-a"},
            {"name": "two", "backend": "jade", "seed_id": "test-b"},
        ],
        "suite_config": {"contribution_mode": "per-input"},
        "inputs": [
            {"owner": "one", "type": "sp-spend", "amount_sat": 100_000},
            {"owner": "two", "type": "sp-spend", "amount_sat": 110_000},
        ],
        "outputs": [{"type": "p2wpkh", "amount_sat": 209_000}],
    })

    psbt = parse_psbt(build_bip375_fixture(scenario))

    assert len(psbt.inputs) == 2
    for inp in psbt.inputs:
        key_types = {entry.key_type for entry in inp.entries}
        assert 0x20 in key_types  # PSBT_IN_SP_TWEAK (BIP-376)
        assert 0x1F in key_types  # PSBT_IN_SP_SPEND_BIP32_DERIVATION (BIP-376)
    # Plain P2WPKH output: resolved script, no Silent Payment output info.
    assert psbt.outputs[0].get(b"\x04") is not None
    assert psbt.outputs[0].get(b"\x09") is None


def test_generated_fixture_supports_sighash_default_on_p2tr_input() -> None:
    pytest.importorskip("embit")
    scenario = Scenario.from_dict({
        "name": "taproot-sighash-default",
        "suite": "bip375",
        "network": "regtest",
        "signers": [{"name": "one", "backend": "coldcard", "seed_id": "test-a"}],
        "inputs": [{"owner": "one", "type": "p2tr", "amount_sat": 100_000, "sighash": "default"}],
        "outputs": [{"type": "silent-payment", "amount_sat": 90_000}],
    })

    psbt = parse_psbt(build_bip375_fixture(scenario))

    assert psbt.inputs[0].get(b"\x03") == b"\x00\x00\x00\x00"


def test_generated_fixture_supports_sighash_default_on_sp_spend_input() -> None:
    pytest.importorskip("embit")
    scenario = Scenario.from_dict({
        "name": "sp-spend-sighash-default",
        "suite": "bip375",
        "network": "regtest",
        "signers": [{"name": "one", "backend": "coldcard", "seed_id": "test-a"}],
        "inputs": [{"owner": "one", "type": "sp-spend", "amount_sat": 100_000, "sighash": "default"}],
        "outputs": [{"type": "p2wpkh", "amount_sat": 90_000}],
    })

    psbt = parse_psbt(build_bip375_fixture(scenario))

    assert psbt.inputs[0].get(b"\x03") == b"\x00\x00\x00\x00"


def test_generated_fixture_rejects_unknown_sighash_value() -> None:
    pytest.importorskip("embit")
    scenario = Scenario.from_dict({
        "name": "bad-sighash",
        "suite": "bip375",
        "network": "regtest",
        "signers": [{"name": "one", "backend": "coldcard", "seed_id": "test-a"}],
        "inputs": [{"owner": "one", "type": "p2tr", "amount_sat": 100_000, "sighash": "none"}],
        "outputs": [{"type": "silent-payment", "amount_sat": 90_000}],
    })

    with pytest.raises(ConfigurationError, match="sighash"):
        build_bip375_fixture(scenario)


def test_generated_fixture_supports_p2tr_output() -> None:
    pytest.importorskip("embit")
    scenario = Scenario.from_dict({
        "name": "sp-spend-to-taproot",
        "suite": "bip375",
        "network": "regtest",
        "signers": [{"name": "one", "backend": "coldcard", "seed_id": "test-a"}],
        "suite_config": {"contribution_mode": "global"},
        "inputs": [{"owner": "one", "type": "sp-spend", "amount_sat": 100_000}],
        "outputs": [{"type": "p2tr", "amount_sat": 99_000}],
    })

    psbt = parse_psbt(build_bip375_fixture(scenario))

    assert psbt.outputs[0].get(b"\x04") is not None
    assert psbt.outputs[0].get(b"\x09") is None


def test_generated_fixture_supports_global_contribution_mode() -> None:
    pytest.importorskip("embit")
    scenario = Scenario.from_dict({
        "name": "single-owner",
        "suite": "bip375",
        "network": "regtest",
        "signers": [{"name": "one", "backend": "seedsigner", "seed_id": "test-a"}],
        "suite_config": {"contribution_mode": "global"},
        "inputs": [{"owner": "one", "type": "p2wpkh", "amount_sat": 100_000}],
        "outputs": [{"type": "silent-payment", "amount_sat": 99_000}],
    })

    psbt = parse_psbt(build_bip375_fixture(scenario))

    assert len(psbt.inputs) == 1
    assert psbt.outputs[0].get(b"\x09") is not None
