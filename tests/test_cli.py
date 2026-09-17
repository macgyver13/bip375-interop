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
