from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from bip375_interop.adapters.btclib import BtclibAdapter, BtclibValidationError
from bip375_interop.models import Checkout, HarnessConfig, Scenario
from bip375_interop.preflight import validator_build_problems


SCENARIO = {
    "name": "single", "suite": "bip375", "network": "regtest",
    "signers": [{"name": "one", "backend": "coldcard", "seed_id": "test-a"}],
    "inputs": [{"owner": "one", "type": "p2wpkh", "amount_sat": 100_000}],
    "outputs": [{"type": "silent-payment", "recipient_id": "recipient-a", "amount_sat": 90_000}],
}


def _fake_checkout(tmp_path: Path, *, built: bool = True) -> Path:
    checkout = tmp_path / "btclib-wallet"
    (checkout / "src/btclib_wallet/psbt").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("")
    (checkout / "src/btclib_wallet/psbt/psbt.py").write_text("")
    if built:
        (checkout / ".venv/bin").mkdir(parents=True)
        (checkout / ".venv/bin/python").write_text("")
    return checkout


def _runner(lines):
    def run(argv, **_kwargs):
        stdout = "".join(json.dumps(line) + "\n" for line in lines)
        return subprocess.CompletedProcess(argv, 0, stdout, "")
    return run


def test_scenario_validators_accept_btclib():
    assert Scenario.from_dict({**SCENARIO, "validators": ["btclib"]}).validators == ("btclib",)


def test_btclib_builds_its_own_venv_and_validates_with_it(tmp_path: Path):
    adapter = BtclibAdapter(_fake_checkout(tmp_path))

    venv, install = adapter.plan_build()
    plan = adapter.plan_validate([tmp_path / "final.psbt"])

    assert venv.argv[-2:] == ("venv", ".venv")
    assert install.argv[0] == str(adapter.python)
    assert install.argv[-1] == ".[secp256k1]"
    assert plan.argv[0] == str(adapter.python)
    assert plan.argv[1] == "-I"
    assert plan.argv[2].endswith("btclib_validate.py")
    assert plan.argv[3:] == (str(tmp_path / "final.psbt"),)


def test_btclib_only_validates_final_psbt():
    assert BtclibAdapter.snapshot_glob == "final.psbt"


def test_btclib_rejection_raises_with_file_and_reason(tmp_path: Path):
    adapter = BtclibAdapter(_fake_checkout(tmp_path), runner=_runner([
        {"file": "/run/final.psbt", "ok": False, "error": "extract_tx: bad signature"},
    ]))

    with pytest.raises(BtclibValidationError, match="final.psbt: extract_tx: bad signature"):
        adapter.validate([Path("/run/final.psbt")])


def test_btclib_unbuilt_venv_is_reported_by_adapter_and_preflight(tmp_path: Path):
    checkout = _fake_checkout(tmp_path, built=False)
    config = HarnessConfig(Path("artifacts"), {"btclib": Checkout("btclib", checkout)})
    scenario = Scenario.from_dict({**SCENARIO, "validators": ["btclib"]})

    with pytest.raises(BtclibValidationError, match="not built"):
        BtclibAdapter(checkout).validate([])
    assert validator_build_problems(config, [scenario]) == [
        f"btclib: {checkout / '.venv/bin/python'} is not built (run bip375-interop build btclib)"
    ]


@pytest.mark.skipif(
    not os.environ.get("BTCLIB_CHECKOUT"),
    reason="set BTCLIB_CHECKOUT to a btclib-wallet checkout built with bip375-interop build btclib",
)
def test_real_btclib_accepts_signed_fixture_and_rejects_tampered_signature(tmp_path: Path):
    pytest.importorskip("embit")
    from bip375_interop.fixtures import build_bip375_fixture
    from bip375_interop.psbt_maps import PsbtEntry, PsbtMap, PsbtV2, parse_psbt
    from bip375_interop.test_seeds import mnemonic_for
    from embit import bip32, bip39
    from embit.psbt import SIGHASH
    from embit.silent_payments import SilentPaymentsPSBT

    psbt = SilentPaymentsPSBT.parse(build_bip375_fixture(Scenario.from_dict(SCENARIO)))
    psbt.sign_with(bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-a"))),
                   sighash=SIGHASH.ALL)
    signed = psbt.serialize()
    good = tmp_path / "good.psbt"
    good.write_bytes(signed)

    parsed = parse_psbt(signed)
    inputs = list(parsed.inputs)
    entry = next(e for e in inputs[0].entries if e.key_type == 0x02)
    tampered = bytearray(entry.value)
    tampered[10] ^= 1
    inputs[0] = PsbtMap(tuple(
        e if e.key_type != 0x02 else PsbtEntry(e.key, bytes(tampered)) for e in inputs[0].entries
    ))
    bad = tmp_path / "bad.psbt"
    bad.write_bytes(PsbtV2(parsed.globals, tuple(inputs), parsed.outputs).serialize())

    adapter = BtclibAdapter(os.environ["BTCLIB_CHECKOUT"])

    assert adapter.validate([good])[0]["ok"]
    with pytest.raises(BtclibValidationError, match="bad.psbt"):
        adapter.validate([good, bad])
