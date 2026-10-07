import base64
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from bip375_interop.cli import _run_validators, case_result_for_run, main
from bip375_interop.checkouts import CheckoutState
from bip375_interop.errors import InteropError
from bip375_interop.interop_lab import (
    KNOWN_FINDINGS, _known_roundtrip_change, validate_snapshots, write_reports,
)
from bip375_interop.psbt_maps import PsbtEntry, PsbtMap, PsbtV2


def _snapshot() -> bytes:
    return PsbtV2(
        PsbtMap((
            PsbtEntry(b"\xfb", (2).to_bytes(4, "little")),
            PsbtEntry(b"\x02", (2).to_bytes(4, "little")),
            PsbtEntry(b"\x04", b"\x01"),
            PsbtEntry(b"\x05", b"\x01"),
            PsbtEntry(b"\x06", b"\x03"),
        )),
        (PsbtMap((PsbtEntry(b"\x0e", b"\x11" * 32),
                  PsbtEntry(b"\x0f", (0).to_bytes(4, "little")))),),
        (PsbtMap((PsbtEntry(b"\x03", (1000).to_bytes(8, "little")),
                  PsbtEntry(b"\x09", b"\x02" + b"\x22" * 32 + b"\x03" + b"\x33" * 32))),),
    ).serialize()


def test_known_roundtrip_exceptions_match_only_the_observed_field_changes():
    raw = _snapshot()
    parsed = PsbtV2(*_parts(raw))
    empty_script = replace(parsed, outputs=(PsbtMap(parsed.outputs[0].entries +
                                                 (PsbtEntry(b"\x04", b""),)),)).serialize()
    assert _known_roundtrip_change("rust-psbt-v2", raw, empty_script) == \
        "rust-psbt-v2-adds-empty-script"

    changed_amount = replace(parsed, outputs=(PsbtMap((
        PsbtEntry(b"\x03", (1001).to_bytes(8, "little")),
        parsed.outputs[0].entries[1],
    )),)).serialize()
    assert _known_roundtrip_change("rust-psbt-v2", raw, changed_amount) == \
        "unexpected-roundtrip-change"


def _parts(raw: bytes):
    from bip375_interop.psbt_maps import parse_psbt

    parsed = parse_psbt(raw)
    return parsed.globals, parsed.inputs, parsed.outputs


def test_snapshot_stage_allows_documented_findings_and_rejects_tampering(tmp_path, monkeypatch):
    raw = _snapshot()
    path = tmp_path / "00-initial.psbt"
    path.write_bytes(raw)
    parsed = PsbtV2(*_parts(raw))
    empty_script = replace(parsed, outputs=(PsbtMap(parsed.outputs[0].entries +
                                                 (PsbtEntry(b"\x04", b""),)),)).serialize()
    monkeypatch.setattr("bip375_interop.interop_lab._availability", lambda runner: None)
    monkeypatch.setattr("bip375_interop.interop_lab._run_parser_matrix", lambda paths, runner: {
        "snapshot-00": {"lab": "accepted", "bundled-js": "rejected"},
    })

    def adapters(name, paths, runner):
        if name == "rust-psbt-v2":
            return {"snapshot-00": {"status": "ok"},
                    "roundtrip-00": {"status": "ok", "output": {
                        "psbt": base64.b64encode(empty_script).decode()}}}
        return {"snapshot-00": {"status": "rejected"},
                "roundtrip-00": {"status": "rejected"}}

    monkeypatch.setattr("bip375_interop.interop_lab._run_adapter", adapters)
    clean = validate_snapshots([path], set(KNOWN_FINDINGS))
    assert clean["status"] == "passed"
    assert clean["validated"] == 1

    altered = replace(parsed, outputs=(PsbtMap((
        PsbtEntry(b"\x03", (1001).to_bytes(8, "little")),
        parsed.outputs[0].entries[1],
    )),)).serialize()
    def tampered(name, paths, runner):
        response = adapters(name, paths, runner)
        if name == "rust-psbt-v2":
            response["roundtrip-00"]["output"]["psbt"] = base64.b64encode(altered).decode()
        return response

    monkeypatch.setattr("bip375_interop.interop_lab._run_adapter", tampered)
    bad = validate_snapshots([path], set(KNOWN_FINDINGS))
    assert bad["status"] == "failed"
    assert "changed protected PSBT fields" in bad["files"][0]["failures"][0]


def test_file_command_prints_skipped_docker_stage(tmp_path: Path, capsys, monkeypatch):
    psbt = tmp_path / "test.psbt"
    psbt.write_bytes(_snapshot())
    config = tmp_path / "interop.yaml"
    config.write_text("artifact_root: artifacts\n")
    monkeypatch.setattr("bip375_interop.cli.validate_snapshots", lambda paths, allowed: {
        "status": "not-run", "reason": "Docker is missing", "files": [],
    })
    assert main(["--config", str(config), "validate-psbt", str(psbt)]) == 0
    output = capsys.readouterr().out
    assert "parser: passed" in output
    assert "spdk: not run" in output
    assert "interop-lab: not run (Docker is missing)" in output
    assert "verdict: partial" in output


def test_junit_and_sarif_are_written_beside_batch_report(tmp_path: Path):
    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps({"interop_lab": {"files": [
        {"file": "00-initial.psbt", "status": "failed", "failures": ["changed"],
         "findings": []},
    ]}}))
    case = type("Case", (), {"name": "scenario", "artifact": str(manifest), "reason": None})()
    paths = write_reports(tmp_path, [case])
    assert Path(paths["junit"]).exists()
    assert json.loads(Path(paths["sarif"]).read_text())["runs"][0]["results"][0]["level"] == "error"


def test_validator_failure_is_recorded_and_does_not_stop_later_stages(tmp_path, monkeypatch):
    (tmp_path / "final.psbt").write_bytes(_snapshot())
    config = SimpleNamespace(checkouts={name: SimpleNamespace(path=tmp_path)
                                        for name in ("caravan", "spdk")}, allow_dirty=False)
    scenario = SimpleNamespace(validators=("caravan", "spdk"))
    monkeypatch.setattr("bip375_interop.cli.inspect_checkout",
                        lambda checkout, allow_dirty: CheckoutState(
                            "validator", str(tmp_path), "pinned", False, None))

    class Rejecting:
        snapshot_glob = "*.psbt"

        def __init__(self, path):
            pass

        def validate(self, paths):
            raise InteropError("Caravan rejected final.psbt")

    class Accepting(Rejecting):
        snapshot_glob = "final.psbt"

        def validate(self, paths):
            return ["accepted"]

    monkeypatch.setattr("bip375_interop.cli._VALIDATOR_ADAPTERS",
                        {"caravan": Rejecting, "spdk": Accepting})
    records = _run_validators(config, scenario, SimpleNamespace(path=tmp_path))
    assert [item["status"] for item in records] == ["failed", "passed"]


def test_release_case_fails_when_lab_is_not_run(tmp_path):
    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps({
        "verification_scope": "not-evidence", "reason": "interop-lab-skipped",
        "validators": [], "interop_lab": {"status": "not-run", "reason": "Docker is missing"},
    }))
    scenario = SimpleNamespace(name="plain", suite="bip375", verification="full",
                               merge_policy="strict", inputs=({"owner": "a"},),
                               outputs=({"type": "silent-payment"},))
    result = case_result_for_run(scenario, manifest, generated=True, release=True)
    assert result.status == "failed"
    assert "Docker is missing" in result.reason


def test_release_case_fails_when_independent_validator_is_missing(tmp_path):
    manifest = tmp_path / "run.json"
    manifest.write_text(json.dumps({
        "verification_scope": "not-evidence", "reason": "validator-skipped",
        "validators": [{"name": "caravan", "status": "passed", "validated": 1}],
        "interop_lab": {"status": "passed", "snapshots": 1, "validated": 1},
    }))
    scenario = SimpleNamespace(name="plain", suite="bip375", verification="full",
                               merge_policy="strict", inputs=({"owner": "a"},),
                               outputs=({"type": "silent-payment"},))
    result = case_result_for_run(scenario, manifest, generated=True, release=True)
    assert result.status == "failed"
    assert "release validators" in result.reason
