"""Adapter for upstream SeedSigner's in-process BIP-375 tests."""

from __future__ import annotations

import os
import subprocess
import sys
import os
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


class SeedSignerAdapter:
    """Run upstream SeedSigner's plain BIP-375 software signer tests."""

    backend = "seedsigner"
    capabilities = AdapterCapabilities(plain_bip375=True, musig2_sp=False)

    def __init__(
        self,
        checkout_dir: str | Path,
        *,
        python_executable: str = sys.executable,
        runner: Runner = subprocess.run,
    ) -> None:
        self.checkout_dir = Path(checkout_dir).resolve()
        self.checkout = self.checkout_dir
        require_checkout(
            self.checkout_dir,
            ("pyproject.toml", "tests/test_silent_payments.py", "src/seedsigner"),
        )
        self.python_executable = python_executable
        self._runner = runner

    def plan_build(self) -> tuple[CommandPlan, ...]:
        return (
            CommandPlan(
                name="seedsigner-install",
                # --no-deps: its requirements pin a different embit than the harness's.
                argv=(self.python_executable, "-m", "pip", "install", "--no-deps", "-e", "."),
                cwd=self.checkout_dir,
            ),
        )

    def plan_test(
        self,
        artifact_dir: str | Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> CommandPlan:
        env = {"BIP375_ARTIFACT_DIR": str(Path(artifact_dir).resolve())}
        env.update(extra_env or {})
        return CommandPlan(
            name="seedsigner-bip375-tests",
            argv=(
                self.python_executable,
                "-m",
                "pytest",
                "tests/test_silent_payments.py",
            ),
            cwd=self.checkout_dir,
            env=env,
        )

    def plan_worker(self) -> CommandPlan:
        python_path = str(self.checkout_dir / "src")
        if os.environ.get("PYTHONPATH"):
            python_path += os.pathsep + os.environ["PYTHONPATH"]
        return CommandPlan(
            name="seedsigner-external-worker",
            argv=(
                self.python_executable, "-m", "bip375_interop.signer_worker",
                "--backend", "seedsigner",
            ),
            cwd=self.checkout_dir,
            env={"PYTHONPATH": python_path},
        )

    def plan_worker(
        self, extra_env: Mapping[str, str] | None = None
    ) -> CommandPlan:
        """Launch a persistent JSON-lines worker in the checkout's dependency environment."""

        harness_src = Path(__file__).resolve().parents[2]
        python_paths = [str(self.checkout_dir / "src"), str(harness_src)]
        inherited = os.environ.get("PYTHONPATH")
        if inherited:
            python_paths.append(inherited)
        env = {"PYTHONPATH": os.pathsep.join(python_paths)}
        env.update(extra_env or {})
        return CommandPlan(
            name="seedsigner-jsonl-worker",
            argv=(
                self.python_executable,
                "-u",
                "-m",
                "bip375_interop.signer_worker",
                "--backend",
                self.backend,
            ),
            cwd=self.checkout_dir,
            env=env,
        )

    def run(self, plan: CommandPlan, *, check: bool = True) -> CommandResult:
        return execute_plan(plan, self._runner, check=check)
