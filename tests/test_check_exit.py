import json
import subprocess
from pathlib import Path

from bip375_interop.cli import main

MUSIG = """
name: musig-a
suite: musig2-sp
network: regtest
signers:
  - {name: jade-a, backend: jade, seed_id: test-a}
  - {name: jade-b, backend: jade, seed_id: test-b}
"""


def _setup(tmp_path: Path, status: str | None, dirty: bool = False) -> tuple[Path, list[str]]:
    jade = tmp_path / "jade"
    jade.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(jade), *args], check=True)
    (jade / "f").write_text("x")
    subprocess.run(["git", "-C", str(jade), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(jade), "commit", "-q", "-m", "c"], check=True)
    if dirty:
        (jade / "f").write_text("changed")
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "musig-a.yaml").write_text(MUSIG)
    config = tmp_path / "interop.yaml"
    config.write_text(f"artifact_root: {tmp_path / 'artifacts'}\ncheckouts:\n  jade: {{path: {jade}}}\n")
    if status is not None:
        (tmp_path / "expectations.yaml").write_text(
            f"scenarios:\n  musig-a: {{status: {status}, reason: test}}\n"
        )
    # Binding a missing PSBT makes the case fail without starting any worker.
    args = ["--config", str(config), "check", "--project", "harness",
            "--scenarios-dir", str(scenarios), "--psbt", f"musig-a={tmp_path / 'missing.psbt'}"]
    return config, args


def test_an_expected_failure_does_not_fail_the_run(tmp_path: Path, capsys):
    _, args = _setup(tmp_path, "finding")

    assert main(args) == 0

    assert json.loads(capsys.readouterr().out)["labels"] == {"STEADY": 1}


def test_a_regression_fails_the_run(tmp_path: Path, capsys):
    _, args = _setup(tmp_path, "supported")

    assert main(args) == 1

    assert json.loads(capsys.readouterr().out)["variances"] == {"musig-a": "REGRESSION"}


def test_without_expectations_any_failure_still_fails_the_run(tmp_path: Path):
    _, args = _setup(tmp_path, None)

    assert main(args) == 1


def test_a_dirty_checkout_aborts_before_any_batch_is_written(tmp_path: Path, capsys):
    _, args = _setup(tmp_path, "finding", dirty=True)

    assert main(args) == 2

    assert "preflight failed" in capsys.readouterr().err
    assert not (tmp_path / "artifacts" / "batches").exists()


def test_batch_report_records_the_checkout_states(tmp_path: Path, capsys):
    _, args = _setup(tmp_path, "finding")

    main(args)

    report = json.loads(Path(json.loads(capsys.readouterr().out)["manifest"]).read_text())
    assert [item["name"] for item in report["checkouts"]] == ["jade"]
    assert report["checkouts"][0]["vcs"] == "git"



def _dry_run_case(tmp_path: Path, capsys, extra: list[str], stored: bool) -> dict:
    config, _ = _setup(tmp_path, "supported")
    if stored:
        (tmp_path / "scenarios" / "musig-a.psbt").write_bytes(b"psbt")
    args = ["--config", str(config), "check", "--project", "harness",
            "--scenarios-dir", str(tmp_path / "scenarios"), "--dry-run", *extra]
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)["cases"][0]


def test_a_psbt_stored_next_to_the_scenario_is_bound(tmp_path: Path, capsys):
    case = _dry_run_case(tmp_path, capsys, [], stored=True)

    assert case["status"] == "selected"
    assert case["psbt"] == str(tmp_path / "scenarios" / "musig-a.psbt")


def test_an_explicit_psbt_overrides_the_stored_one(tmp_path: Path, capsys):
    override = tmp_path / "other.psbt"

    case = _dry_run_case(tmp_path, capsys, ["--psbt", f"musig-a={override}"], stored=True)

    assert case["psbt"] == str(override)


def test_a_musig2_scenario_without_a_stored_psbt_stays_blocked(tmp_path: Path, capsys):
    case = _dry_run_case(tmp_path, capsys, [], stored=False)

    assert case["status"] == "blocked"
    assert case["psbt"] is None
