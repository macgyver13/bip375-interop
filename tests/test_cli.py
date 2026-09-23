import json
from pathlib import Path

from bip375_interop.cli import main


def test_check_fails_when_every_scenario_is_blocked(tmp_path: Path, capsys):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    (scenarios_dir / "musig.yaml").write_text("""
name: musig-case
suite: musig2-sp
network: regtest
signers:
  - {name: jade-a, backend: jade, seed_id: test-a}
  - {name: jade-b, backend: jade, seed_id: test-b}
""")
    config_path = tmp_path / "interop.yaml"
    config_path.write_text(f"artifact_root: {tmp_path / 'artifacts'}\n")

    exit_code = main([
        "--config", str(config_path),
        "check", "--project", "harness", "--scenarios-dir", str(scenarios_dir),
    ])

    assert exit_code == 1
    assert "required" in capsys.readouterr().out


def test_run_summary_prints_scope_beside_the_artifact(tmp_path: Path, capsys, monkeypatch):
    scenario = tmp_path / "case.yaml"
    scenario.write_text("""
name: case
suite: bip375
network: regtest
verification: structural
signers:
  - {name: a, backend: coldcard, seed_id: test-a}
inputs:
  - {owner: a, type: p2wpkh, amount_sat: 10000}
outputs:
  - {type: silent-payment, recipient_id: recipient-a, amount_sat: 9000}
""")
    config = tmp_path / "interop.yaml"
    config.write_text(f"artifact_root: {tmp_path / 'artifacts'}\n")
    final = tmp_path / "final.psbt"
    final.write_bytes(b"psbt")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"verification_scope": "not-evidence", "reason": "structural"}))

    def fake_run(_config, _scenario, signer_order=None):
        return final, manifest, 4

    monkeypatch.setattr("bip375_interop.cli._run_generated_scenario", fake_run)
    exit_code = main(["--config", str(config), "run-generated", str(scenario)])

    assert exit_code == 0
    text = capsys.readouterr().out
    assert text.index('"verification_scope"') > text.index('"final_psbt"')
    assert text.index('"manifest"') > text.index('"verification_scope"')
    assert "not-evidence" in text
    assert str(final) in text
    assert str(manifest) in text
