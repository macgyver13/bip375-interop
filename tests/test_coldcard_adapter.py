from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bip375_interop.adapters.coldcard import ColdcardAdapter
from bip375_interop.suites import KeyArchitecture, SuiteName


def make_checkout(root: Path) -> Path:
    checkout = root / "coldcard-firmware"
    testing = checkout / "testing"
    data = testing / "data"
    data.mkdir(parents=True)
    (checkout / "unix").mkdir()
    (checkout / "unix" / "Makefile").touch()
    (checkout / "ENV" / "bin").mkdir(parents=True)
    (checkout / "ENV" / "bin" / "python").touch()
    (testing / "run_sim_tests.py").touch()
    (testing / "test_silentpayments.py").touch()
    (testing / "test_musig2_silentpayments.py").touch()
    (testing / "test_bip375_vectors.py").touch()
    (testing / "test_bip352_vectors.py").touch()
    (testing / "test_musig2_sp_signers.py").touch()
    for fixture in ColdcardAdapter._FIXTURES:
        (data / fixture).touch()
    return checkout


def test_capabilities_and_build_plan_are_explicit(tmp_path: Path):
    adapter = ColdcardAdapter(make_checkout(tmp_path))

    assert adapter.capabilities.plain_bip375
    assert adapter.capabilities.musig2_sp
    assert adapter.capabilities.musig2_key_architectures == frozenset(
        {KeyArchitecture.AGGREGATE_THEN_DERIVE}
    )
    # unix/Makefile derives VARIANT_DIR from $(PWD), so its steps run inside unix/;
    # `make -C unix` from the top fails with "Invalid VARIANT specified".
    unix = adapter.firmware_dir / "unix"
    plans = adapter.plan_build()
    # First the ENV venv the worker runs from, then the README's make steps.
    assert plans[0].argv[1:] == ("-m", "venv", "ENV")
    assert plans[1].argv == (adapter.python_executable, "-m", "pip", "install", "-q", "-r", "requirements.txt")
    makes = [plan for plan in plans if plan.argv[0] == "make"]
    assert [(plan.argv, plan.cwd) for plan in makes] == [
        (("make", "-C", "external/micropython/mpy-cross"), adapter.firmware_dir),
        (("make", "setup"), unix),
        (("make", "ngu-setup"), unix),
        (("make",), unix),
    ]


def test_run_suite_uses_firmware_runner_and_external_artifacts(tmp_path: Path):
    checkout = make_checkout(tmp_path)
    calls = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "passed", "")

    adapter = ColdcardAdapter(checkout, runner=runner)
    fixtures = checkout / "testing" / "data"
    artifacts = tmp_path / "artifacts" / "coldcard"
    result = adapter.run_suite(
        fixtures,
        artifacts,
        pytest_filter="full_flow",
        extra_env={"HARNESS_SENTINEL": "present", "CC_SP_OUT": "ignored"},
    )

    assert result.returncode == 0
    assert result.plan.name == "coldcard-musig2-sp-tests"
    argv, kwargs = calls.pop()
    assert argv[:3] == [adapter.worker_python, "-m", "bip375_interop.coldcard_worker"]
    assert argv[argv.index("--source-checkout") + 1] == str(checkout)
    assert argv[argv.index("--fixture-dir") + 1] == str(fixtures)
    assert argv[argv.index("--artifact-dir") + 1] == str(artifacts.resolve())
    assert argv[argv.index("--suite") + 1] == "musig2-sp"
    assert argv[-2:] == [
        "--pytest-filter",
        "full_flow",
    ]
    assert kwargs["cwd"] == checkout
    assert kwargs["env"]["HARNESS_SENTINEL"] == "present"
    assert str(Path(__file__).parents[1] / "src") in kwargs["env"]["PYTHONPATH"]
    assert kwargs["check"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_plain_bip375_plan_selects_the_existing_plain_test_module(tmp_path: Path):
    adapter = ColdcardAdapter(make_checkout(tmp_path))
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    plan = adapter.plan_test(fixtures, tmp_path / "artifacts", suite=SuiteName.BIP375)

    assert plan.name == "coldcard-bip375-tests"
    assert plan.argv[plan.argv.index("--suite") + 1] == "bip375"


def test_invalid_checkout_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="invalid checkout"):
        ColdcardAdapter(tmp_path / "missing")


def test_doctor_reports_missing_python_and_fixtures(tmp_path: Path):
    checkout = make_checkout(tmp_path)
    (checkout / "ENV" / "bin" / "python").unlink()

    problems = ColdcardAdapter(checkout).doctor()

    assert len(problems) == 1
    assert "Python environment is missing" in problems[0]


def test_musig2_plan_requires_external_fixtures(tmp_path: Path):
    adapter = ColdcardAdapter(make_checkout(tmp_path))
    fixtures = tmp_path / "external-fixtures"
    fixtures.mkdir()

    with pytest.raises(ValueError, match=ColdcardAdapter._FIXTURES[0]):
        adapter.plan_test(fixtures, tmp_path / "artifacts")


def test_plan_rejects_artifacts_inside_source_checkout(tmp_path: Path):
    checkout = make_checkout(tmp_path)
    adapter = ColdcardAdapter(checkout)

    with pytest.raises(ValueError, match="outside the source checkout"):
        adapter.plan_test(
            checkout / "testing" / "data",
            checkout / "generated-artifacts",
        )


def test_bip375_only_checkout_is_accepted_but_cannot_plan_musig2(tmp_path: Path):
    checkout = make_checkout(tmp_path)
    (checkout / "testing" / "test_musig2_silentpayments.py").unlink()
    (checkout / "testing" / "test_musig2_sp_signers.py").unlink()
    adapter = ColdcardAdapter(checkout)
    fixtures = checkout.parent / "fixtures"
    fixtures.mkdir()

    adapter.plan_test(fixtures, tmp_path / "artifacts", suite=SuiteName.BIP375)
    with pytest.raises(ValueError, match="test_musig2_silentpayments.py"):
        adapter.plan_test(checkout / "testing" / "data", tmp_path / "artifacts")
