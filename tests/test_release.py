import json
import subprocess
from pathlib import Path

import pytest

from bip375_interop.catalog import ScenarioEntry
from bip375_interop.cli import (
    attach_bip375_validators,
    attach_musig2_legs,
    invoke_musig2_release,
    main,
    musig2_leg_result,
    release_legs_ok,
)
from bip375_interop.errors import InteropError
from bip375_interop.models import Checkout, HarnessConfig, KNOWN_VALIDATORS, Scenario
from bip375_interop.preflight import run_preflight, validator_build_problems
from bip375_interop.verification import check_case_status, manifest_claim_fields


def _git_repo(path: Path) -> Path:
    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "f").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "c"], check=True)
    return path


def _scenario(**extra) -> Scenario:
    payload = {
        "name": "plain",
        "suite": "bip375",
        "network": "regtest",
        "signers": [{"name": "a", "backend": "coldcard", "seed_id": "test-a"}],
        "inputs": [{"owner": "a", "type": "p2wpkh", "amount_sat": 10000}],
        "outputs": [{"type": "silent-payment", "recipient_id": "recipient-a", "amount_sat": 9000}],
    }
    payload.update(extra)
    return Scenario.from_dict(payload)


def _entry(scenario: Scenario, path: Path) -> ScenarioEntry:
    return ScenarioEntry(path, scenario, scenario.suite == "bip375", None)


def _both_records(snapshots: int = 2) -> list[dict]:
    return [
        {"name": "caravan", "snapshots": snapshots, "validated": snapshots},
        {"name": "spdk", "snapshots": 1, "validated": 1},
    ]


def test_release_profile_attaches_both_validators_without_rewriting_weak_modes(tmp_path: Path):
    plain = _entry(_scenario(), tmp_path / "plain.yaml")
    structural = _entry(_scenario(name="weak", verification="structural"), tmp_path / "weak.yaml")
    combiner = _entry(_scenario(name="combo", merge_policy="combiner"), tmp_path / "combo.yaml")
    musig = _entry(Scenario.from_dict({
        "name": "musig",
        "suite": "musig2-sp",
        "network": "regtest",
        "signers": [
            {"name": "a", "backend": "jade", "seed_id": "test-a"},
            {"name": "b", "backend": "jade", "seed_id": "test-b"},
        ],
    }), tmp_path / "musig.yaml")

    attached = {item.scenario.name: item.scenario for item in attach_bip375_validators((plain, structural, combiner, musig))}

    assert attached["plain"].validators == KNOWN_VALIDATORS
    assert attached["weak"].validators == KNOWN_VALIDATORS
    assert attached["weak"].verification == "structural"
    assert attached["combo"].merge_policy == "combiner"
    assert attached["musig"].validators == ()
    assert check_case_status(attached["weak"], generated=True) != "passed"
    assert check_case_status(attached["combo"], generated=True) != "passed"
    assert check_case_status(attached["musig"], generated=True) != "passed"
    assert check_case_status(attached["musig"], generated=False) == "completed"


def test_evidence_requires_both_validators_and_is_not_claimed_when_skipped(tmp_path: Path):
    scenario = _scenario()
    evidence = manifest_claim_fields(scenario, (), _both_records())
    assert evidence == {"verification_scope": "evidence", "independent_check": "ran"}

    skipped = manifest_claim_fields(scenario, (), ())
    assert skipped == {
        "verification_scope": "not-evidence",
        "reason": "validator-skipped",
        "independent_check": "not-run",
    }
    structural_skipped = manifest_claim_fields(_scenario(verification="structural"), (), ())
    assert structural_skipped["reason"] == "structural"
    assert structural_skipped["verification_scope"] == "not-evidence"

    from bip375_interop.cli import case_result_for_run

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"verification_scope": "not-evidence", "reason": "validator-skipped", "repairs": []}))
    result = case_result_for_run(scenario, manifest, generated=True)
    assert result.status != "passed"
    assert result.status == "completed"
    assert result.reason == "validator-skipped"

    one = manifest_claim_fields(scenario, (), [_both_records()[0]])
    assert one.get("verification_scope") != "evidence"
    assert one["independent_check"] == "ran"

    empty = manifest_claim_fields(scenario, (), [{"name": "caravan", "snapshots": 0}, {"name": "spdk", "snapshots": 1}])
    assert empty.get("verification_scope") != "evidence"

    structural = manifest_claim_fields(_scenario(verification="structural"), (), _both_records())
    assert structural["verification_scope"] == "not-evidence"
    assert structural["verification_scope"] != "evidence"


def test_missing_validator_builds_are_preflight_errors(tmp_path: Path):
    caravan = _git_repo(tmp_path / "caravan")
    spdk = _git_repo(tmp_path / "spdk")
    coldcard = _git_repo(tmp_path / "coldcard")
    config = HarnessConfig(tmp_path / "artifacts", {
        "caravan": Checkout("caravan", caravan),
        "spdk": Checkout("spdk", spdk),
        "coldcard": Checkout("coldcard", coldcard),
    })
    missing_binary = tmp_path / "spdk-cli" / "target" / "release" / "spdk-cli"
    scenario = replace_validators(_scenario(), KNOWN_VALIDATORS)

    problems = validator_build_problems(config, [scenario], spdk_binary=missing_binary)
    assert any("caravan:" in problem and "not built" in problem for problem in problems)
    assert any("spdk:" in problem and "not built" in problem for problem in problems)

    with pytest.raises(InteropError, match="not built") as raised:
        run_preflight(config, [scenario])
    assert "not built" in str(raised.value)
    assert "skip" not in str(raised.value).lower()


def replace_validators(scenario: Scenario, validators: tuple[str, ...]) -> Scenario:
    from dataclasses import replace
    return replace(scenario, validators=validators)


def test_a_leg_counts_only_when_the_script_prints_pass():
    assert musig2_leg_result("aggregate-then-derive", "done\n", 0)["passed"] is False
    counted = musig2_leg_result(
        "aggregate-then-derive",
        "PASS musig2-sp-coldcard-jade-two-way aggregate-then-derive txid=abc\n",
        0,
    )
    assert counted["passed"] is True
    assert counted["line"].startswith("PASS ")
    assert musig2_leg_result("aggregate-then-derive", "PASS nope\n", 1)["passed"] is False


def test_release_summary_without_pass_lines_is_a_failure():
    legs = [
        musig2_leg_result("aggregate-then-derive", "", 1),
        musig2_leg_result("derive-then-aggregate", "broadcast failed\n", 1),
    ]
    summary = attach_musig2_legs({"passed": 0, "required": 0}, legs)
    assert release_legs_ok(summary["musig2_legs"]) is False
    assert [leg["architecture"] for leg in summary["musig2_legs"]] == [
        "aggregate-then-derive", "derive-then-aggregate",
    ]
    assert all(leg["line"] is None for leg in summary["musig2_legs"])


def test_release_summary_is_refused_when_legs_were_not_run():
    with pytest.raises(InteropError, match="were not run"):
        attach_musig2_legs({"passed": 1}, None)
    with pytest.raises(InteropError, match="were not run"):
        attach_musig2_legs({"passed": 1}, [])


def test_release_invokes_both_architectures_with_the_selected_config(tmp_path: Path):
    script = tmp_path / "musig2-regtest.sh"
    script.write_text("#!/bin/sh\n")
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs.get("env", {}).get("CONFIG")))
        arch = argv[-1]
        return subprocess.CompletedProcess(argv, 0, f"PASS scenario {arch} txid=1\n", "")

    legs = invoke_musig2_release(script, runner, config=tmp_path / "baseline" / "interop.yaml")

    assert [call[0][-1] for call in calls] == ["aggregate-then-derive", "derive-then-aggregate"]
    assert all(call[1] == str(tmp_path / "baseline" / "interop.yaml") for call in calls)
    assert release_legs_ok(legs)


def _write_plain(scenarios: Path, name: str = "plain", extra: str = "") -> None:
    scenarios.mkdir(exist_ok=True)
    (scenarios / f"{name}.yaml").write_text(f"""
name: {name}
suite: bip375
network: regtest
signers:
  - {{name: a, backend: coldcard, seed_id: test-a}}
inputs:
  - {{owner: a, type: p2wpkh, amount_sat: 10000}}
outputs:
  - {{type: silent-payment, recipient_id: recipient-a, amount_sat: 9000}}
{extra}
""")


def test_release_dry_run_attaches_caravan_and_spdk_without_a_validators_key(tmp_path: Path, capsys):
    scenarios = tmp_path / "scenarios"
    _write_plain(scenarios)
    config = tmp_path / "interop.yaml"
    config.write_text(f"artifact_root: {tmp_path / 'artifacts'}\n")

    code = main([
        "--config", str(config), "check", "--release", "--dry-run",
        "--scenarios-dir", str(scenarios),
    ])

    assert code == 0
    case = json.loads(capsys.readouterr().out)["cases"][0]
    assert case["validators"] == ["caravan", "spdk"]
    assert case["scenario"] == "plain"


def test_release_preflight_rejects_a_missing_build_instead_of_skipping(tmp_path: Path, capsys, monkeypatch):
    scenarios = tmp_path / "scenarios"
    _write_plain(scenarios)
    coldcard = _git_repo(tmp_path / "coldcard")
    caravan = _git_repo(tmp_path / "caravan")
    spdk = _git_repo(tmp_path / "spdk")
    config = tmp_path / "interop.yaml"
    config.write_text(
        f"artifact_root: {tmp_path / 'artifacts'}\n"
        "checkouts:\n"
        f"  coldcard: {{path: {coldcard}}}\n"
        f"  caravan: {{path: {caravan}}}\n"
        f"  spdk: {{path: {spdk}}}\n"
    )
    monkeypatch.setattr(
        "bip375_interop.preflight.default_spdk_binary",
        lambda: tmp_path / "missing-spdk-cli",
    )

    code = main([
        "--config", str(config), "check", "--release",
        "--scenarios-dir", str(scenarios),
    ])

    err = capsys.readouterr().err
    assert code == 2
    assert "not built" in err
    assert "preflight failed" in err
    assert not (tmp_path / "artifacts" / "batches").exists()


def test_release_caravan_rejection_fails_the_case(tmp_path: Path, capsys, monkeypatch):
    from bip375_interop.adapters.caravan import CaravanValidationError

    scenarios = tmp_path / "scenarios"
    _write_plain(scenarios)
    config = tmp_path / "interop.yaml"
    config.write_text(f"artifact_root: {tmp_path / 'artifacts'}\n")
    seen = {}

    def boom(_config, scenario):
        seen["validators"] = scenario.validators
        raise CaravanValidationError("caravan rejected 1 PSBT(s): final.psbt: bad script")

    monkeypatch.setattr("bip375_interop.cli.run_preflight", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("bip375_interop.cli._run_generated_scenario", boom)
    monkeypatch.setattr(
        "bip375_interop.cli.invoke_musig2_release",
        lambda *args, **kwargs: [
            {"architecture": "aggregate-then-derive", "passed": True, "line": "PASS a aggregate-then-derive txid=1"},
            {"architecture": "derive-then-aggregate", "passed": True, "line": "PASS b derive-then-aggregate txid=2"},
        ],
    )

    code = main([
        "--config", str(config), "check", "--release",
        "--scenarios-dir", str(scenarios),
    ])

    summary = json.loads(capsys.readouterr().out)
    report = json.loads(Path(summary["manifest"]).read_text())
    assert seen["validators"] == KNOWN_VALIDATORS
    assert code == 1
    assert report["results"][0]["status"] == "failed"
    assert "caravan rejected" in report["results"][0]["reason"]
    assert report["counts"]["passed"] == 0


def test_release_summary_with_no_pass_line_fails(tmp_path: Path, capsys, monkeypatch):
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "musig.yaml").write_text("""
name: musig-case
suite: musig2-sp
network: regtest
signers:
  - {name: jade-a, backend: jade, seed_id: test-a}
  - {name: jade-b, backend: jade, seed_id: test-b}
""")
    config = tmp_path / "interop.yaml"
    config.write_text(f"artifact_root: {tmp_path / 'artifacts'}\n")
    monkeypatch.setattr(
        "bip375_interop.cli.invoke_musig2_release",
        lambda *args, **kwargs: [
            musig2_leg_result("aggregate-then-derive", "no txid\n", 1),
            musig2_leg_result("derive-then-aggregate", "", 1),
        ],
    )

    code = main([
        "--config", str(config), "check", "--release",
        "--scenarios-dir", str(scenarios),
    ])

    summary = json.loads(capsys.readouterr().out)
    assert code == 1
    assert summary["passed"] == 0
    assert [leg["passed"] for leg in summary["musig2_legs"]] == [False, False]
    assert all(leg["line"] is None for leg in summary["musig2_legs"])
