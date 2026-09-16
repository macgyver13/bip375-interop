from pathlib import Path

import pytest

from bip375_interop.catalog import changed_files, discover, select
from bip375_interop.cli import _psbt_bindings
from bip375_interop.errors import InteropError


def test_catalog_selects_backend_and_marks_external_psbt_cases(tmp_path: Path):
    (tmp_path / "jade.yaml").write_text("""
name: jade-case
suite: bip375
network: regtest
signers: [{name: jade-a, backend: jade, seed_id: test-a}]
inputs: [{owner: jade-a, type: p2wpkh, amount_sat: 100000}]
outputs: [{type: silent-payment, amount_sat: 99000}]
""")
    (tmp_path / "musig.yaml").write_text("""
name: musig-case
suite: musig2-sp
network: regtest
signers:
  - {name: jade-a, backend: jade, seed_id: test-a}
  - {name: jade-b, backend: jade, seed_id: test-b}
""")

    entries = select(discover(tmp_path), "jade", "affected")

    assert [entry.scenario.name for entry in entries] == ["jade-case", "musig-case"]
    assert entries[0].runnable_generated
    assert entries[1].reason == "no initial PSBT: store musig.psbt next to the scenario or pass --psbt"


def test_changed_files_includes_untracked_paths(tmp_path: Path, monkeypatch):
    calls = []

    def check_output(argv, text):
        calls.append(argv)
        return "tracked.py\n" if "diff" in argv else "untracked.py\n"

    monkeypatch.setattr("bip375_interop.catalog.subprocess.check_output", check_output)

    assert changed_files(tmp_path, "HEAD") == ("tracked.py", "untracked.py")
    assert calls[0][-1] == "HEAD"


def test_psbt_bindings_require_unique_scenario_equals_path_values():
    assert _psbt_bindings(["musig=round1.psbt"]) == {"musig": Path("round1.psbt")}
    with pytest.raises(InteropError, match="more than once"):
        _psbt_bindings(["musig=one.psbt", "musig=two.psbt"])
