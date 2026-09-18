import json
from pathlib import Path

from bip375_interop.cli import main

MUSIG = """
name: {name}
suite: musig2-sp
network: regtest
signers:
  - {{name: jade-a, backend: jade, seed_id: test-a}}
  - {{name: jade-b, backend: jade, seed_id: test-b}}
"""


def _setup(tmp_path: Path, expectations: str | None) -> Path:
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    for name in ("musig-a", "musig-b"):
        (scenarios / f"{name}.yaml").write_text(MUSIG.format(name=name))
    config = tmp_path / "interop.yaml"
    config.write_text(f"artifact_root: {tmp_path / 'artifacts'}\n")
    if expectations is not None:
        (tmp_path / "expectations.yaml").write_text(expectations)
    return config


def _check(config: Path, tmp_path: Path) -> int:
    return main(["--config", str(config), "check", "--project", "harness",
                 "--scenarios-dir", str(tmp_path / "scenarios")])


def test_check_labels_cases_against_expectations(tmp_path: Path, capsys):
    config = _setup(tmp_path, """
scenarios:
  musig-a: {status: needs-external-psbt, reason: needs psbt}
  musig-b: {status: supported, reason: passes given a psbt}
""")

    _check(config, tmp_path)

    summary = json.loads(capsys.readouterr().out)
    assert summary["labels"] == {"NOT-RUN": 1, "STEADY": 1}
    assert summary["variances"] == {"musig-b": "NOT-RUN"}


def test_check_without_expectations_file_reports_no_labels(tmp_path: Path, capsys):
    config = _setup(tmp_path, None)

    _check(config, tmp_path)

    summary = json.loads(capsys.readouterr().out)
    assert "labels" not in summary and "variances" not in summary
