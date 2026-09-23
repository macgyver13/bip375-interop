from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from bip375_interop.adapters.spdk import SpdkAdapter, SpdkValidationError
from bip375_interop.catalog import discover, select
from bip375_interop.models import Scenario


SCENARIO = {
    "name": "two-way",
    "suite": "bip375",
    "network": "regtest",
    "signers": [
        {"name": "one", "backend": "coldcard", "seed_id": "test-a"},
        {"name": "two", "backend": "jade", "seed_id": "test-b"},
    ],
    "suite_config": {"contribution_mode": "per-input"},
    "inputs": [
        {"owner": "one", "type": "p2wpkh", "amount_sat": 100_000},
        {"owner": "two", "type": "p2wpkh", "amount_sat": 110_000},
    ],
    "outputs": [{"type": "silent-payment", "recipient_id": "recipient-a", "amount_sat": 209_000}],
}


def _fake_checkout(tmp_path: Path) -> Path:
    """A fake external spdk *library* checkout (see adapters/spdk.py)."""
    checkout = tmp_path / "spdk"
    (checkout / "psbt").mkdir(parents=True)
    (checkout / "psbt/Cargo.toml").write_text("")
    return checkout


def _fake_crate(tmp_path: Path) -> Path:
    """A fake in-repo spdk-cli crate dir, standing in for the real one."""
    crate = tmp_path / "spdk-cli"
    target = crate / "target/release"
    target.mkdir(parents=True)
    (target / "spdk-cli").write_text("")
    return crate


def _runner(lines):
    def run(argv, **_kwargs):
        stdout = "".join(json.dumps(line) + "\n" for line in lines)
        return subprocess.CompletedProcess(argv, 0, stdout, "")
    return run


def test_scenario_validators_accept_spdk():
    assert Scenario.from_dict({**SCENARIO, "validators": ["spdk"]}).validators == ("spdk",)


def test_catalog_selects_validator_scenarios_for_spdk_project(tmp_path: Path):
    (tmp_path / "plain.yaml").write_text(json.dumps({**SCENARIO, "name": "plain"}))
    (tmp_path / "checked.yaml").write_text(
        json.dumps({**SCENARIO, "name": "checked", "validators": ["spdk"]})
    )

    entries = select(discover(tmp_path), "spdk")

    assert [entry.scenario.name for entry in entries] == ["checked"]


def test_spdk_plan_passes_binary_and_snapshots(tmp_path: Path):
    adapter = SpdkAdapter(_fake_checkout(tmp_path), crate_dir=_fake_crate(tmp_path))

    plan = adapter.plan_validate([tmp_path / "final.psbt"])

    assert plan.argv == (str(adapter.binary), str(tmp_path / "final.psbt"))


def test_spdk_only_validates_final_psbt():
    assert SpdkAdapter.snapshot_glob == "final.psbt"


def test_spdk_rejection_raises_with_file_and_reason(tmp_path: Path):
    adapter = SpdkAdapter(
        _fake_checkout(tmp_path),
        crate_dir=_fake_crate(tmp_path),
        runner=_runner([
            {"file": "/run/final.psbt", "ok": False, "error": "interpreter_check: bad signature"},
        ]),
    )

    with pytest.raises(SpdkValidationError, match="final.psbt: interpreter_check: bad signature"):
        adapter.validate([Path("/run/final.psbt")])


def test_spdk_unbuilt_crate_is_reported(tmp_path: Path):
    adapter = SpdkAdapter(_fake_checkout(tmp_path), crate_dir=_fake_crate(tmp_path))
    adapter.binary.unlink()

    with pytest.raises(SpdkValidationError, match="not built"):
        adapter.validate([])


@pytest.mark.skipif(
    not os.environ.get("SPDK_CHECKOUT"),
    reason=(
        "set SPDK_CHECKOUT to the external spdk checkout, and build "
        "spdk-cli/ at the repo root with cargo build --release"
    ),
)
def test_real_spdk_accepts_signed_fixture_and_rejects_tampered_signature(tmp_path: Path):
    pytest.importorskip("embit")
    from bip375_interop.fixtures import build_bip375_fixture
    from bip375_interop.psbt_maps import PsbtEntry, PsbtMap, PsbtV2, parse_psbt
    from embit import bip32, bip39
    from embit.psbt import SIGHASH
    from embit.silent_payments import SilentPaymentsPSBT
    from bip375_interop.test_seeds import mnemonic_for

    scenario = Scenario.from_dict({
        "name": "single", "suite": "bip375", "network": "regtest",
        "signers": [{"name": "one", "backend": "coldcard", "seed_id": "test-a"}],
        "inputs": [{"owner": "one", "type": "p2wpkh", "amount_sat": 100_000}],
        "outputs": [{"type": "silent-payment", "recipient_id": "recipient-a", "amount_sat": 90_000}],
    })
    raw = build_bip375_fixture(scenario)
    psbt = SilentPaymentsPSBT.parse(raw)
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-a")))
    psbt.sign_with(root, sighash=SIGHASH.ALL)
    signed = psbt.serialize()

    good = tmp_path / "good.psbt"
    good.write_bytes(signed)

    parsed = parse_psbt(signed)
    inputs = list(parsed.inputs)
    entry = next(e for e in inputs[0].entries if e.key_type == 0x02)
    tampered_value = bytearray(entry.value)
    tampered_value[10] ^= 1
    inputs[0] = PsbtMap(tuple(
        e if e.key_type != 0x02 else PsbtEntry(e.key, bytes(tampered_value))
        for e in inputs[0].entries
    ))
    bad = tmp_path / "bad.psbt"
    bad.write_bytes(PsbtV2(parsed.globals, tuple(inputs), parsed.outputs).serialize())

    adapter = SpdkAdapter(os.environ["SPDK_CHECKOUT"])

    assert adapter.validate([good])[0]["ok"]
    with pytest.raises(SpdkValidationError, match="bad.psbt"):
        adapter.validate([good, bad])
