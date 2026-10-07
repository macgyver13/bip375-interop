"""Pinned PSBT Interop Lab parser and native-adapter checks for our PSBTs."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence

from .errors import InteropError
from .psbt_maps import parse_psbt, semantic_diff

LAB_VERSION = "0.11.0"
_IMAGES = {
    "rust-psbt-v2": "psbt-interop-lab/rust-psbt-v2:0.1.0",
    "libwally": "psbt-interop-lab/libwally:1.5.4",
}
_IDENTITIES = {
    "rust-psbt-v2": (
        "rust-psbt-v2",
        "rust-psbt/psbt-v2-0.3.0@8ca657c333b6b391f2501e8b31627ccbb6a67f66",
        "sha256:67746b354266dc6c85cf08a5880cc0aa1e9cc78ce41b9681a50a78aba816913d",
    ),
    "libwally": (
        "libwally-core",
        "libwally-core-release_1.5.4@c5591834b3ae4ee4c7db9e537a9c19104ab4bf0c",
        "sha256:ab7482f0be2e7076617d7802646766714ca6180ee73ef7ebd016fe3731bc3558",
    ),
}
KNOWN_FINDINGS = (
    "bundled-js-rejects-unresolved",
    "libwally-rejects-unresolved",
    "rust-psbt-v2-adds-empty-script",
    "libwally-drops-zero-tx-modifiable",
)


def _unresolved(psbt: bytes) -> bool:
    return any(
        output.get(b"\x09") is not None and output.get(b"\x04") is None
        for output in parse_psbt(psbt).outputs
    )


def _has_silent_payment(psbt: bytes) -> bool:
    return any(output.get(b"\x09") is not None for output in parse_psbt(psbt).outputs)


def _known_roundtrip_change(adapter: str, before: bytes, after: bytes) -> str | None:
    diff = semantic_diff(before, after)
    if not (diff.added or diff.removed or diff.modified):
        return None
    if adapter == "rust-psbt-v2" and _unresolved(before) and not (diff.removed or diff.modified):
        missing = {
            index for index, output in enumerate(parse_psbt(before).outputs)
            if output.get(b"\x09") is not None and output.get(b"\x04") is None
        }
        if {item.index for item in diff.added} == missing and all(
            item.scope == "output" and item.key_hex == "04" and item.after_hex == ""
            for item in diff.added
        ):
            return "rust-psbt-v2-adds-empty-script"
    if (adapter == "libwally" and _has_silent_payment(before) and not _unresolved(before)
            and not (diff.added or diff.modified)):
        if len(diff.removed) == 1 and all(
            item.scope == "global" and item.key_hex == "06" and item.before_hex == "00"
            for item in diff.removed
        ):
            return "libwally-drops-zero-tx-modifiable"
    return "unexpected-roundtrip-change"


def _manifest(paths: Sequence[Path]) -> dict:
    fixtures = []
    scenarios = []
    for index, path in enumerate(paths):
        raw = path.read_bytes()
        fixture_id = f"snapshot-{index:02d}"
        fixtures.append({
            "id": fixture_id,
            "psbt": base64.b64encode(raw).decode("ascii"),
            "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        })
        scenarios.append({
            "id": fixture_id,
            "title": path.name,
            "fixture": fixture_id,
            "steps": [{
                "id": "compare", "operation": "compare-parsers", "input": "fixture",
                "adapters": ["bundled-js"],
                "expected": {"lab": "accepted", "bundled-js": "accepted"},
            }],
        })
    return {"schema": "psbt-lab.suite/0.2", "fixtures": [],
            "parserFixtures": fixtures, "scenarios": scenarios}


def _run_parser_matrix(paths: Sequence[Path], runner) -> dict[str, dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="bip375-psbt-lab-") as directory:
        manifest = Path(directory) / "suite.json"
        manifest.write_text(json.dumps(_manifest(paths)))
        result = runner(
            ["npx", "--yes", f"psbt-interop-lab@{LAB_VERSION}", "parse-matrix",
             "--runtime", "local", "--suite-manifest", str(manifest), "--json"],
            capture_output=True, text=True, check=False,
        )
    try:
        report = json.loads(result.stdout)
        scenarios = report["scenarios"]
        return {
            item["id"]: {assertion["name"].removeprefix("compare-"): assertion.get("actual", "error")
                         for assertion in item["assertions"]}
            for item in scenarios
        }
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"psbt-lab parser matrix failed: {result.stderr.strip()}") from exc


def _run_adapter(adapter: str, paths: Sequence[Path], runner) -> dict[str, dict]:
    requests = [
        {"protocol": "psbt-lab.adapter/0.2", "id": f"snapshot-{index:02d}",
         "operation": "native-parse", "payload": {"psbt": base64.b64encode(path.read_bytes()).decode()}}
        for index, path in enumerate(paths)
    ] + [
        {"protocol": "psbt-lab.adapter/0.2", "id": f"roundtrip-{index:02d}",
         "operation": "roundtrip", "payload": {"psbt": base64.b64encode(path.read_bytes()).decode()}}
        for index, path in enumerate(paths)
    ]
    result = runner(
        ["docker", "run", "--rm", "-i", "--network", "none", "--read-only",
         "--cap-drop", "ALL", _IMAGES[adapter]],
        input="\n".join(json.dumps(item, separators=(",", ":")) for item in requests) + "\n",
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{adapter} adapter failed: {result.stderr.strip()}")
    responses = {item["id"]: item for item in map(json.loads, result.stdout.splitlines())}
    if len(responses) != len(requests):
        raise RuntimeError(f"{adapter} adapter returned {len(responses)} of {len(requests)} responses")
    expected = _IDENTITIES[adapter]
    for response in responses.values():
        identity = response.get("implementation", {})
        actual = (identity.get("name"), identity.get("sourceRevision"),
                  identity.get("artifactDigest"))
        if actual != expected:
            raise RuntimeError(f"{adapter} adapter identity differs from pinned {LAB_VERSION}")
    return responses


def _availability(runner) -> str | None:
    if shutil.which("docker") is None:
        return "Docker is missing"
    if shutil.which("npx") is None:
        return "npx is missing"
    docker = runner(["docker", "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True, text=True, check=False)
    if docker.returncode:
        return "Docker daemon is unavailable"
    for image in _IMAGES.values():
        inspect = runner(["docker", "image", "inspect", image],
                         capture_output=True, text=True, check=False)
        if inspect.returncode:
            return f"{image} is missing; run the pinned psbt-lab matrix to build it"
    return None


def validate_snapshots(
    paths: Sequence[Path], allowed_findings: set[str], *, runner=subprocess.run,
) -> dict:
    """Report every parser/roundtrip result; known exceptions need an explicit allowlist."""

    if not paths:
        return {"name": "interop-lab", "version": LAB_VERSION, "status": "failed",
                "reason": "no PSBT snapshots were written", "snapshots": 0,
                "validated": 0, "files": []}
    unavailable = _availability(runner)
    if unavailable:
        return {"name": "interop-lab", "version": LAB_VERSION, "status": "not-run",
                "reason": unavailable, "snapshots": len(paths), "validated": 0, "files": []}
    files = []
    for start in range(0, len(paths), 32):
        chunk = paths[start:start + 32]
        try:
            parser = _run_parser_matrix(chunk, runner)
            adapters = {name: _run_adapter(name, chunk, runner) for name in _IMAGES}
        except (RuntimeError, ValueError, KeyError) as exc:
            return {"name": "interop-lab", "version": LAB_VERSION, "status": "failed",
                    "reason": str(exc), "snapshots": len(paths), "validated": 0, "files": files}
        for index, path in enumerate(chunk):
            raw = path.read_bytes()
            ident = f"snapshot-{index:02d}"
            findings = []
            failures = []
            outcomes = parser.get(ident, {})
            if outcomes.get("lab") != "accepted":
                failures.append("lab parser did not accept the PSBT")
            if outcomes.get("bundled-js") != "accepted":
                if _unresolved(raw) and outcomes.get("bundled-js") == "rejected":
                    findings.append("bundled-js-rejects-unresolved")
                else:
                    failures.append("bundled-js parser did not accept the PSBT")
            for adapter, responses in adapters.items():
                parsed = responses[f"snapshot-{index:02d}"]
                if parsed["status"] != "ok":
                    if adapter == "libwally" and _unresolved(raw) and parsed["status"] == "rejected":
                        findings.append("libwally-rejects-unresolved")
                    else:
                        failures.append(f"{adapter} native parse: {parsed['status']}")
                rounded = responses[f"roundtrip-{index:02d}"]
                if rounded["status"] != "ok":
                    if not (adapter == "libwally" and _unresolved(raw)
                            and rounded["status"] == "rejected"):
                        failures.append(f"{adapter} roundtrip: {rounded['status']}")
                    continue
                try:
                    returned = base64.b64decode(rounded["output"]["psbt"], validate=True)
                    finding = _known_roundtrip_change(adapter, raw, returned)
                except (ValueError, KeyError, TypeError, InteropError) as exc:
                    failures.append(f"{adapter} returned invalid roundtrip data: {exc}")
                    continue
                if finding == "unexpected-roundtrip-change":
                    failures.append(f"{adapter} changed protected PSBT fields")
                elif finding:
                    findings.append(finding)
            unexpected = sorted(set(findings) - allowed_findings)
            if unexpected:
                failures.append("unapproved findings: " + ", ".join(unexpected))
            files.append({"file": path.name, "status": "failed" if failures else "passed",
                          "findings": sorted(set(findings)), "failures": failures})
    passed = sum(item["status"] == "passed" for item in files)
    return {"name": "interop-lab", "version": LAB_VERSION,
            "status": "passed" if passed == len(paths) else "failed",
            "snapshots": len(paths), "validated": passed, "files": files}


def write_reports(directory: Path, cases: Sequence) -> dict[str, str]:
    """Write batch JUnit and SARIF summaries beside report.json."""

    rows = []
    sarif_results = []
    for case in cases:
        lab = {}
        if case.artifact and Path(case.artifact).is_file():
            lab = json.loads(Path(case.artifact).read_text()).get("interop_lab", {})
        files = lab.get("files") or [{
            "file": case.name,
            "status": lab.get("status", "not-run"),
            "failures": [lab.get("reason") or case.reason or "run did not reach Interop Lab"],
            "findings": [],
        }]
        for item in files:
            rows.append((case.name, item))
            if item["status"] == "failed":
                sarif_results.append({
                    "ruleId": "interop-lab.snapshot",
                    "level": "error",
                    "message": {"text": "; ".join(item.get("failures", []))},
                    "locations": [{"physicalLocation": {"artifactLocation": {
                        "uri": item["file"]}}}],
                })
    suite = ET.Element("testsuite", name="interop-lab", tests=str(len(rows)),
                       failures=str(sum(item["status"] == "failed" for _, item in rows)),
                       skipped=str(sum(item["status"] == "not-run" for _, item in rows)))
    for scenario, item in rows:
        cell = ET.SubElement(suite, "testcase", classname=scenario, name=item["file"])
        if item["status"] == "failed":
            ET.SubElement(cell, "failure", message="; ".join(item.get("failures", [])))
        elif item["status"] == "not-run":
            ET.SubElement(cell, "skipped", message="; ".join(item.get("failures", [])))
    junit = directory / "interop-lab.junit.xml"
    ET.ElementTree(suite).write(junit, encoding="unicode", xml_declaration=True)
    sarif = directory / "interop-lab.sarif"
    sarif.write_text(json.dumps({
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{"tool": {"driver": {"name": "bip375-interop Interop Lab stage",
                                       "version": LAB_VERSION,
                                       "rules": [{"id": "interop-lab.snapshot"}]}},
                  "results": sarif_results}],
    }, indent=2) + "\n")
    return {"junit": str(junit), "sarif": str(sarif)}
