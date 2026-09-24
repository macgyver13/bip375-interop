from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bip375_interop.adapters import BitSagaAdapter, ColdcardAdapter, JadeAdapter, SeedSignerAdapter
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
    _touch(root, "build/flash_image.bin")
    _touch(root, "build/qemu_efuse.bin")
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


def _coldcard_checkout(root: Path) -> Path:
    _touch(root, "unix/Makefile")
    _touch(root, "testing/run_sim_tests.py")
    _touch(root, "testing/test_silentpayments.py")
    _touch(root, "testing/test_musig2_silentpayments.py")
    _touch(root, "testing/test_bip375_vectors.py")
    _touch(root, "testing/test_bip352_vectors.py")
    _touch(root, "testing/test_musig2_sp_signers.py")
    _touch(root, "ENV/bin/python")
    (root / "external" / "ckcc-protocol").mkdir(parents=True)
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


def test_jade_plans_native_qemu_worker_and_tcp_test(tmp_path: Path) -> None:
    adapter = JadeAdapter(
        _jade_checkout(tmp_path / "jade"),
        python_executable="python-test",
    )

    test = adapter.plan_test(tmp_path / "artifacts")
    worker = adapter.plan_worker()

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
    assert "BIP375_JADE_DOCKER" not in worker.env


def test_coldcard_plans_persistent_native_simulator_worker(tmp_path: Path) -> None:
    adapter = ColdcardAdapter(_coldcard_checkout(tmp_path / "coldcard"))

    worker = adapter.plan_worker()

    assert worker.argv == (
        str(adapter.firmware_dir / "ENV" / "bin" / "python"),
        "-u",
        "-m",
        "bip375_interop.coldcard_psbt_worker",
    )
    assert worker.env["BIP375_COLDCARD_CHECKOUT"] == str(adapter.firmware_dir)
    assert worker.env["BIP375_COLDCARD_PYTHON"] == str(adapter.firmware_dir / "ENV" / "bin" / "python")


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


def test_jade_plans_its_build_before_the_flash_image_exists(tmp_path: Path) -> None:
    root = tmp_path / "jade"
    _touch(root, "test_jade.py")
    (root / "jadepy").mkdir()
    with pytest.raises(ValueError, match="flash_image.bin"):
        JadeAdapter(root)

    plans = JadeAdapter(root, require_build=False).plan_build()

    assert [plan.name for plan in plans] == ["jade-switch-to-qemu", "jade-build", "jade-flash-image"]
    # switch_to.sh calls idf.py too, so every step runs in the ESP-IDF environment.
    assert plans[0].argv[2].endswith("./tools/switch_to.sh qemu --dev --ci")
    assert all('"$IDF_PATH/export.sh"' in plan.argv[2] for plan in plans)
    assert plans[2].argv[2].endswith("make_flash_img.sh build/flash_image.bin build/qemu_efuse.bin")


def test_seedsigner_install_keeps_the_harness_embit(tmp_path: Path) -> None:
    adapter = SeedSignerAdapter(_seedsigner_checkout(tmp_path / "ss", musig2=False))

    assert "--no-deps" in adapter.plan_build()[0].argv
