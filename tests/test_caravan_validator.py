from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from bip375_interop.adapters.caravan import CaravanAdapter, CaravanValidationError
from bip375_interop.catalog import discover, select
from bip375_interop.errors import ConfigurationError
from bip375_interop.models import Scenario
from bip375_interop.psbt_maps import PsbtEntry, PsbtMap, parse_psbt


SCENARIO = {
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
    "outputs": [{"type": "silent-payment", "recipient_id": "recipient-a", "amount_sat": 209_000}],
}


def _fake_checkout(tmp_path: Path) -> Path:
    (tmp_path / "turbo.json").write_text("{}")
    dist = tmp_path / "packages/caravan-psbt/dist"
    dist.mkdir(parents=True)
    (tmp_path / "packages/caravan-psbt/package.json").write_text("{}")
    (dist / "index.js").write_text("")
    return tmp_path


def _runner(lines):
    def run(argv, **_kwargs):
        stdout = "".join(json.dumps(line) + "\n" for line in lines)
        return subprocess.CompletedProcess(argv, 0, stdout, "")
    return run


def test_scenario_validators_are_opt_in_and_checked():
    assert Scenario.from_dict(SCENARIO).validators == ()
    assert Scenario.from_dict({**SCENARIO, "validators": ["caravan"]}).validators == ("caravan",)
    with pytest.raises(ConfigurationError, match="unknown validators"):
        Scenario.from_dict({**SCENARIO, "validators": ["nope"]})
    with pytest.raises(ConfigurationError, match="bip375 suite only"):
        Scenario.from_dict({**SCENARIO, "suite": "musig2-sp", "validators": ["caravan"]})


def test_catalog_selects_validator_scenarios_for_caravan_project(tmp_path: Path):
    (tmp_path / "plain.yaml").write_text(json.dumps({**SCENARIO, "name": "plain"}))
    (tmp_path / "checked.yaml").write_text(
        json.dumps({**SCENARIO, "name": "checked", "validators": ["caravan"]})
    )

    entries = select(discover(tmp_path), "caravan")

    assert [entry.scenario.name for entry in entries] == ["checked"]


def test_caravan_plan_passes_dist_and_snapshots(tmp_path: Path):
    adapter = CaravanAdapter(_fake_checkout(tmp_path))

    plan = adapter.plan_validate([tmp_path / "final.psbt"])

    assert plan.argv[0] == "node"
    assert plan.argv[1].endswith("caravan_validate.cjs")
    assert plan.argv[2:] == (str(adapter.dist), str(tmp_path / "final.psbt"))


def test_caravan_rejection_raises_with_file_and_reason(tmp_path: Path):
    adapter = CaravanAdapter(_fake_checkout(tmp_path), runner=_runner([
        {"file": "/run/00-initial.psbt", "ok": True, "error": None},
        {"file": "/run/final.psbt", "ok": False, "error": "bad DLEQ"},
    ]))

    with pytest.raises(CaravanValidationError, match="final.psbt: bad DLEQ"):
        adapter.validate([Path("/run/00-initial.psbt"), Path("/run/final.psbt")])


def test_caravan_unbuilt_checkout_is_reported(tmp_path: Path):
    adapter = CaravanAdapter(_fake_checkout(tmp_path))
    adapter.dist.unlink()

    with pytest.raises(CaravanValidationError, match="not built"):
        adapter.validate([])


@pytest.mark.skipif(
    not os.environ.get("CARAVAN_CHECKOUT"),
    reason="set CARAVAN_CHECKOUT to a built caravan checkout",
)
def test_real_caravan_accepts_fixture_and_rejects_non_sighash_all(tmp_path: Path):
    pytest.importorskip("embit")
    from bip375_interop.fixtures import build_bip375_fixture

    raw = build_bip375_fixture(Scenario.from_dict(SCENARIO))
    good = tmp_path / "good.psbt"
    good.write_bytes(raw)
    parsed = parse_psbt(raw)
    sighash_none = PsbtEntry(b"\x03", (2).to_bytes(4, "little"))
    first = PsbtMap(parsed.inputs[0].entries + (sighash_none,))
    bad = tmp_path / "bad.psbt"
    bad.write_bytes(replace(parsed, inputs=(first, *parsed.inputs[1:])).serialize())
    adapter = CaravanAdapter(os.environ["CARAVAN_CHECKOUT"])

    assert adapter.validate([good])[0]["ok"]
    with pytest.raises(CaravanValidationError, match="bad.psbt: .*non-SIGHASH_ALL"):
        adapter.validate([good, bad])
