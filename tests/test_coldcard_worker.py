from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bip375_interop.coldcard_worker import MUSIG2_FIXTURES, _secp256k1_library, run_worker
from bip375_interop.suites import SuiteName


def make_source_checkout(root: Path) -> Path:
    checkout = root / "source"
    (checkout / "testing" / "data").mkdir(parents=True)
    (checkout / "testing" / "run_sim_tests.py").write_text(
        'tmp_dir = "/tmp/cc-simulators"\nsim = ColdcardSimulator(sim_args, segregate=True)'
    )
    (checkout / "testing" / "test_musig2_silentpayments.py").write_text("source test")
    (checkout / "testing" / "test_musig2_sp_signers.py").write_text("source test")
    (checkout / "testing" / "test_silentpayments.py").write_text("source plain test")
    (checkout / "testing" / "test_bip375_vectors.py").write_text("source plain test")
    (checkout / "testing" / "test_bip352_vectors.py").write_text("source plain test")
    for name in MUSIG2_FIXTURES:
        (checkout / "testing" / "data" / name).write_text("source fixture")
    (checkout / "unix" / "work").mkdir(parents=True)
    (checkout / "unix" / "simulator.py").write_text('tmp_dir = "/tmp/cc-simulators"')
    (checkout / "unix" / "work" / "state").write_text("source state")
    return checkout


def test_worker_overlays_fixtures_only_in_disposable_copy(tmp_path: Path):
    source = make_source_checkout(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    for name in MUSIG2_FIXTURES:
        (fixtures / name).write_bytes(f"external {name}".encode())
    artifacts = tmp_path / "artifacts"
    observed = {"argv": []}

    def runner(argv, **kwargs):
        copied_testing = kwargs["cwd"]
        observed["argv"].append(argv)
        observed["source"] = (source / "testing" / "data" / MUSIG2_FIXTURES[0]).read_text()
        observed["fixture"] = (copied_testing / "data" / MUSIG2_FIXTURES[0]).read_text()
        observed["artifact_dir"] = kwargs["env"]["CC_SP_OUT"]
        observed["path"] = kwargs["env"]["PATH"]
        observed["runner_source"] = (copied_testing / "run_sim_tests.py").read_text()
        observed["simulator_source"] = (copied_testing.parent / "unix" / "simulator.py").read_text()
        (copied_testing.parent / "unix" / "work" / "state").write_text("mutated copy")
        return subprocess.CompletedProcess(argv, 0)

    result = run_worker(
        source,
        fixtures,
        artifacts,
        SuiteName.MUSIG2_SP,
        "/firmware/python",
        runner=runner,
    )

    assert result.returncode == 0
    assert [command[command.index("--module") + 1] for command in observed["argv"]] == [
        "test_musig2_silentpayments.py",
        "test_musig2_sp_signers.py",
    ]
    assert observed["source"] == "source fixture"
    assert observed["fixture"].startswith("external ")
    assert observed["artifact_dir"] == str(artifacts.resolve())
    assert observed["path"].split(":")[0] == "/firmware"
    assert '"/tmp/cc-simulators"' not in observed["runner_source"]
    assert "simulators" in observed["runner_source"]
    assert "ColdcardSimulator(sim_args, headless=args.headless, segregate=True)" in observed["runner_source"]
    assert '"/tmp/cc-simulators"' not in observed["simulator_source"]
    assert (source / "unix" / "work" / "state").read_text() == "source state"


def test_worker_rejects_vendor_failure_reported_with_zero_exit(tmp_path: Path):
    source = make_source_checkout(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "\nFAILED test_silentpayments.py\n", "")

    result = run_worker(
        source,
        fixtures,
        tmp_path / "artifacts",
        SuiteName.BIP375,
        "/firmware/python",
        runner=runner,
    )

    assert result.returncode == 1


def test_worker_selects_plain_native_test_without_overlay(tmp_path: Path):
    source = make_source_checkout(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    observed = {"argv": []}

    def runner(argv, **kwargs):
        observed["argv"].append(argv)
        return subprocess.CompletedProcess(argv, 0)

    run_worker(
        source,
        fixtures,
        tmp_path / "artifacts",
        SuiteName.BIP375,
        "/firmware/python",
        runner=runner,
    )

    assert [command[command.index("--module") + 1] for command in observed["argv"]] == [
        "test_bip375_vectors.py",
        "test_silentpayments.py",
        "test_bip352_vectors.py",
    ]
    assert all(command[-3:] == ["--multiproc", "--num-proc", "1"] for command in observed["argv"])


def test_worker_rejects_artifacts_inside_source_checkout(tmp_path: Path):
    source = make_source_checkout(tmp_path)

    with pytest.raises(ValueError, match="outside the source checkout"):
        run_worker(
            source,
            source / "testing" / "data",
            source / "artifacts",
            SuiteName.BIP375,
            "/firmware/python",
        )


def test_worker_preserves_explicit_secp256k1_library(monkeypatch):
    monkeypatch.setenv("PYSECP_SO", "/custom/libsecp256k1.dylib")

    assert _secp256k1_library() == "/custom/libsecp256k1.dylib"
