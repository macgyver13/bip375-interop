from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bip375_interop.adapters import BitSagaAdapter, JadeAdapter, SeedSignerAdapter
from bip375_interop.suites import KeyArchitecture, SuiteName


class RecordingRunner:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="passed\n", stderr="")


def _touch(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _jade_checkout(root: Path) -> Path:
    _touch(root, "Dockerfile.qemu")
    _touch(root, "test_jade.py")
    (root / "jadepy").mkdir()
    return root


def _seedsigner_checkout(root: Path, *, musig2: bool) -> Path:
    _touch(root, "pyproject.toml")
    _touch(root, "tests/test_silent_payments.py")
    (root / "src/seedsigner").mkdir(parents=True)
    if musig2:
        _touch(root, "tests/test_musig2_sp.py")
        _touch(root, "tests/test_flows_musig2.py")
    return root


def test_capabilities_distinguish_plain_bip375_from_musig2_sp(tmp_path: Path) -> None:
    jade = JadeAdapter(_jade_checkout(tmp_path / "jade"))
    upstream = SeedSignerAdapter(
        _seedsigner_checkout(tmp_path / "seedsigner", musig2=False)
    )
    bitsaga = BitSagaAdapter(
        _seedsigner_checkout(tmp_path / "bitsaga", musig2=True)
    )

    assert jade.capabilities.supports(SuiteName.BIP375)
    assert jade.capabilities.supports(SuiteName.MUSIG2_SP)
    assert upstream.capabilities.supports(SuiteName.BIP375)
    assert not upstream.capabilities.supports(SuiteName.MUSIG2_SP)
    assert bitsaga.capabilities.supports(SuiteName.BIP375)
    assert bitsaga.capabilities.supports(SuiteName.MUSIG2_SP)
    assert jade.capabilities.musig2_key_architectures == {
        KeyArchitecture.AGGREGATE_THEN_DERIVE
    }
    assert bitsaga.capabilities.musig2_key_architectures == {
        KeyArchitecture.AGGREGATE_THEN_DERIVE
    }


def test_jade_plans_qemu_build_run_and_tcp_test(tmp_path: Path) -> None:
    adapter = JadeAdapter(
        _jade_checkout(tmp_path / "jade"),
        python_executable="python-test",
        docker_executable="docker-test",
        image="jade-test-image",
    )

    (build,) = adapter.plan_build()
    emulator = adapter.plan_emulator()
    test = adapter.plan_test(tmp_path / "artifacts")
    worker = adapter.plan_worker()

    assert build.argv[:4] == ("docker-test", "build", "-t", "jade-test-image")
    assert "QEMU_CONFIG_ARGS=--dev --ci --psram" in build.argv
    assert emulator.argv == (
        "docker-test",
        "run",
        "--rm",
        "-p",
        "30121:30121",
        "jade-test-image",
    )
    assert test.argv == (
        "python-test",
        "test_jade.py",
        "--serialport",
        "tcp:localhost:30121",
    )
    assert worker.argv == (
        "python-test",
        "-u",
        "-m",
        "bip375_interop.jade_worker",
    )
    assert worker.env["BIP375_JADE_CHECKOUT"] == str(adapter.firmware_dir)


def test_seedsigner_plans_only_upstream_plain_bip375_worker(tmp_path: Path) -> None:
    adapter = SeedSignerAdapter(
        _seedsigner_checkout(tmp_path / "seedsigner", musig2=False),
        python_executable="python-test",
    )

    plan = adapter.plan_test(tmp_path / "artifacts", {"CASE": "plain"})

    assert plan.argv == (
        "python-test",
        "-m",
        "pytest",
        "tests/test_silent_payments.py",
    )
    assert plan.env["CASE"] == "plain"
    worker = adapter.plan_worker()
    assert worker.argv[-2:] == ("--backend", "seedsigner")
    assert str(adapter.checkout_dir / "src") in worker.env["PYTHONPATH"]

    worker = adapter.plan_worker({"WORKER_CASE": "plain"})
    assert worker.argv == (
        "python-test",
        "-u",
        "-m",
        "bip375_interop.signer_worker",
        "--backend",
        "seedsigner",
    )
    assert worker.env["PYTHONPATH"].split(":")[0] == str(adapter.checkout / "src")
    assert worker.env["WORKER_CASE"] == "plain"


def test_bitsaga_plans_plain_and_musig2_workers(tmp_path: Path) -> None:
    adapter = BitSagaAdapter(
        _seedsigner_checkout(tmp_path / "bitsaga", musig2=True),
        python_executable="python-test",
    )

    plan = adapter.plan_test(tmp_path / "artifacts")

    assert plan.argv[-3:] == (
        "tests/test_silent_payments.py",
        "tests/test_musig2_sp.py",
        "tests/test_flows_musig2.py",
    )
    assert adapter.plan_worker().argv[-2:] == ("--backend", "bitsaga")
    assert adapter.plan_worker().argv[-2:] == ("--backend", "bitsaga")


def test_runner_is_injected_and_receives_checkout_and_environment(tmp_path: Path) -> None:
    runner = RecordingRunner()
    checkout = _seedsigner_checkout(tmp_path / "seedsigner", musig2=False)
    adapter = SeedSignerAdapter(checkout, runner=runner)
    plan = adapter.plan_test(tmp_path / "artifacts", {"HARNESS_RUN": "unit"})

    result = adapter.run(plan)

    assert result.returncode == 0
    assert result.stdout == "passed\n"
    argv, kwargs = runner.calls[0]
    assert argv == list(plan.argv)
    assert kwargs["cwd"] == checkout.resolve()
    assert kwargs["env"]["HARNESS_RUN"] == "unit"
    assert kwargs["check"] is True


def test_invalid_checkout_fails_before_any_subprocess_runs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid checkout"):
        JadeAdapter(tmp_path)
