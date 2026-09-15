from __future__ import annotations

import pytest

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
