import json
import subprocess
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
    assert summary["counts"]["blocked"] == 2
    # musig-a is blocked as its expectation says; musig-b is supported, so its block is not
    assert summary["expected"] == {"failed": 0, "blocked": 1}
    assert summary["variances"] == [{
        "scenario": "musig-b",
        "label": "NOT-RUN",
        "status": "blocked",
        "reason": "no initial PSBT: store musig-b.psbt next to the scenario or pass --psbt",
        "expectation": {"status": "supported", "reason": "passes given a psbt"},
        "previous": None,
    }]


def test_a_changed_outcome_names_the_previous_run(tmp_path: Path, capsys):
    jade = tmp_path / "jade"
    jade.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(jade), *args], check=True)
    (jade / "f").write_text("x")
    subprocess.run(["git", "-C", str(jade), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(jade), "commit", "-q", "-m", "c"], check=True)
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "musig-a.yaml").write_text(MUSIG.format(name="musig-a"))
    config = tmp_path / "interop.yaml"
    config.write_text(
        f"artifact_root: {tmp_path / 'artifacts'}\ncheckouts:\n  jade: {{path: {jade}}}\n"
    )
    (tmp_path / "expectations.yaml").write_text(
        "scenarios:\n  musig-a: {status: needs-external-psbt, reason: needs psbt}\n"
    )
    check = ["--config", str(config), "check", "--project", "harness",
             "--scenarios-dir", str(scenarios)]
    main(check)
    capsys.readouterr()

    assert main([*check, "--psbt", f"musig-a={tmp_path / 'missing.psbt'}"]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["variances"] == [{
        "scenario": "musig-a",
        "label": "CHANGED",
        "status": "failed",
        "reason": f"PSBT is missing: {tmp_path / 'missing.psbt'}",
        "expectation": {"status": "needs-external-psbt", "reason": "needs psbt"},
        "previous": {
            "status": "blocked",
            "reason": "no initial PSBT: store musig-a.psbt next to the scenario or pass --psbt",
        },
    }]


def test_check_without_expectations_file_reports_no_labels(tmp_path: Path, capsys):
    config = _setup(tmp_path, None)

    _check(config, tmp_path)

    summary = json.loads(capsys.readouterr().out)
    assert "labels" not in summary and "variances" not in summary
