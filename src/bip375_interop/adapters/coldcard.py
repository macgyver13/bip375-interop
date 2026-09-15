"""Adapter for Coldcard's existing headless simulator test entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from bip375_interop.adapters.base import (
    AdapterCapabilities,
    CommandPlan,
    CommandResult,
    Runner,
    execute_plan,
    require_checkout,
)
from bip375_interop.suites import KeyArchitecture, SuiteName


class ColdcardAdapter:
    """Plan and run Coldcard tests without modifying the firmware checkout.

    The upstream tests hard-code ``testing/data`` and mutate checkout-relative
    simulator state. A harness worker therefore runs them in a disposable copy
    with externally supplied fixtures overlaid into that copy.
    """

    backend = "coldcard"
    capabilities = AdapterCapabilities(
        plain_bip375=True,
        musig2_sp=True,
        musig2_key_architectures=frozenset(
            {KeyArchitecture.AGGREGATE_THEN_DERIVE}
        ),
    )

    _TEST_MODULE = "test_musig2_silentpayments.py"
    _PLAIN_TEST_MODULE = "test_silentpayments.py"
    _FIXTURES = (
        "desc-musig-sp-demo.txt",
        "musig2-sp-round1-in.psbt",
        "musig2-sp-cosigner-contrib.psbt",
    )

    def __init__(
        self,
        firmware_dir: str | Path,
        *,
        python_executable: str | Path | None = None,
        worker_python: str | Path = sys.executable,
        runner: Runner = subprocess.run,
    ) -> None:
        self.firmware_dir = Path(firmware_dir).expanduser().resolve()
        require_checkout(
            self.firmware_dir,
            (
                "unix/Makefile",
                "testing/run_sim_tests.py",
                f"testing/{self._PLAIN_TEST_MODULE}",
                f"testing/{self._TEST_MODULE}",
                "testing/test_bip375_vectors.py",
                "testing/test_bip352_vectors.py",
                "testing/test_musig2_sp_signers.py",
            ),
        )
        self.testing_dir = self.firmware_dir / "testing"
        self.python_executable = str(
            python_executable or self.firmware_dir / "ENV" / "bin" / "python"
        )
        self.worker_python = str(worker_python)
        self._runner = runner

    def doctor(self) -> list[str]:
        problems: list[str] = []
        if not Path(self.python_executable).is_file():
            problems.append(f"Coldcard Python environment is missing: {self.python_executable}")
        return problems

    def plan_worker(
        self, extra_env: Mapping[str, str] | None = None
    ) -> CommandPlan:
        """Launch one persistent, isolated Coldcard simulator worker."""

        harness_src = Path(__file__).resolve().parents[2]
        protocol_src = self.firmware_dir / "external" / "ckcc-protocol"
        python_paths = [str(harness_src), str(protocol_src)]
        inherited = os.environ.get("PYTHONPATH")
        if inherited:
            python_paths.append(inherited)
        env = {
            "PYTHONPATH": os.pathsep.join(python_paths),
            "BIP375_COLDCARD_CHECKOUT": str(self.firmware_dir),
            "BIP375_COLDCARD_PYTHON": self.python_executable,
        }
        env.update(extra_env or {})
        return CommandPlan(
            name="coldcard-jsonl-worker",
            argv=(self.python_executable, "-u", "-m", "bip375_interop.coldcard_psbt_worker"),
            cwd=self.firmware_dir,
            env=env,
        )

    def plan_build(self) -> tuple[CommandPlan, ...]:
        """Return Coldcard's documented simulator build sequence."""
        commands = (
            ("coldcard-build-mpy-cross", ("make", "-C", "external/micropython/mpy-cross")),
            ("coldcard-setup-simulator", ("make", "-C", "unix", "setup")),
            ("coldcard-setup-libngu", ("make", "-C", "unix", "ngu-setup")),
            ("coldcard-build-simulator", ("make", "-C", "unix")),
        )
        return tuple(CommandPlan(name, argv, self.firmware_dir) for name, argv in commands)

    def plan_test(
        self,
        fixture_dir: str | Path,
        artifact_dir: str | Path,
        extra_env: Mapping[str, str] | None = None,
        *,
        suite: SuiteName = SuiteName.MUSIG2_SP,
        pytest_filter: str | None = None,
    ) -> CommandPlan:
        if not self.capabilities.supports(suite):
            raise ValueError(f"Coldcard does not support suite {suite.value}")
        fixture_dir = Path(fixture_dir).expanduser().resolve()
        artifact_dir = Path(artifact_dir).expanduser().resolve()
        if not fixture_dir.is_dir():
            raise ValueError(f"Coldcard fixture directory does not exist: {fixture_dir}")
        if artifact_dir.is_relative_to(self.firmware_dir):
            raise ValueError(
                f"Coldcard artifact directory must be outside the source checkout: {artifact_dir}"
            )
        if suite is SuiteName.MUSIG2_SP:
            for name in self._FIXTURES:
                if not (fixture_dir / name).is_file():
                    raise ValueError(f"Coldcard fixture is missing: {fixture_dir / name}")

        command = [
            self.worker_python,
            "-m",
            "bip375_interop.coldcard_worker",
            "--source-checkout",
            str(self.firmware_dir),
            "--fixture-dir",
            str(fixture_dir),
            "--artifact-dir",
            str(artifact_dir),
            "--suite",
            suite.value,
            "--python-executable",
            self.python_executable,
        ]
        if pytest_filter:
            command.extend(("--pytest-filter", pytest_filter))

        harness_src = str(Path(__file__).resolve().parents[2])
        python_paths = [harness_src]
        inherited = os.environ.get("PYTHONPATH")
        if inherited:
            python_paths.append(inherited)
        env = {"PYTHONPATH": os.pathsep.join(python_paths)}
        env.update(extra_env or {})
        return CommandPlan(
            name=f"coldcard-{suite.value}-tests",
            argv=tuple(command),
            cwd=self.firmware_dir,
            env=env,
        )

    def run(self, plan: CommandPlan, *, check: bool = True) -> CommandResult:
        return execute_plan(plan, self._runner, check=check)

    def run_suite(
        self,
        fixture_dir: str | Path,
        artifact_dir: str | Path,
        *,
        suite: SuiteName = SuiteName.MUSIG2_SP,
        pytest_filter: str | None = None,
        extra_env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> CommandResult:
        """Validate readiness and execute the existing simulator test suite."""
        problems = self.doctor()
        if problems:
            raise ValueError("; ".join(problems))
        output_dir = Path(artifact_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        plan = self.plan_test(
            fixture_dir,
            output_dir,
            extra_env,
            suite=suite,
            pytest_filter=pytest_filter,
        )
        return self.run(plan, check=check)
