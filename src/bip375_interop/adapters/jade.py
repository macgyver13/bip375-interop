"""Adapter for Jade's QEMU build and existing BIP-375 test worker."""

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
from bip375_interop.suites import KeyArchitecture


class JadeAdapter:
    """Plan and run Jade commands without modifying the Jade checkout."""

    backend = "jade"
    capabilities = AdapterCapabilities(
        plain_bip375=True,
        musig2_sp=True,
        musig2_key_architectures=frozenset(
            {KeyArchitecture.AGGREGATE_THEN_DERIVE}
        ),
    )

    def __init__(
        self,
        firmware_dir: str | Path,
        *,
        python_executable: str = sys.executable,
        docker_executable: str = "docker",
        image: str = "bip375-interop-jade",
        device: str = "tcp:localhost:30121",
        runner: Runner = subprocess.run,
    ) -> None:
        self.firmware_dir = Path(firmware_dir).resolve()
        self.checkout = self.firmware_dir
        require_checkout(
            self.firmware_dir, ("Dockerfile.qemu", "test_jade.py", "jadepy")
        )
        self.python_executable = python_executable
        self.docker_executable = docker_executable
        self.image = image
        self.device = device
        self._runner = runner

    def plan_build(self) -> tuple[CommandPlan, ...]:
        return (
            CommandPlan(
                name="jade-build-qemu",
                argv=(
                    self.docker_executable,
                    "build",
                    "-t",
                    self.image,
                    "-f",
                    "Dockerfile.qemu",
                    ".",
                    "--build-arg",
                    "QEMU_CONFIG_ARGS=--dev --ci --psram",
                ),
                cwd=self.firmware_dir,
            ),
        )

    def plan_emulator(self) -> CommandPlan:
        return CommandPlan(
            name="jade-run-qemu",
            argv=(
                self.docker_executable,
                "run",
                "--rm",
                "-p",
                "30121:30121",
                self.image,
            ),
            cwd=self.firmware_dir,
        )

    def plan_worker(
        self, extra_env: Mapping[str, str] | None = None
    ) -> CommandPlan:
        """Launch a worker that owns one isolated Jade QEMU container."""

        harness_src = Path(__file__).resolve().parents[2]
        python_paths = [str(self.firmware_dir), str(harness_src)]
        inherited = os.environ.get("PYTHONPATH")
        if inherited:
            python_paths.append(inherited)
        env = {
            "PYTHONPATH": os.pathsep.join(python_paths),
            "BIP375_JADE_CHECKOUT": str(self.firmware_dir),
            "BIP375_JADE_IMAGE": self.image,
            "BIP375_JADE_DOCKER": self.docker_executable,
        }
        env.update(extra_env or {})
        return CommandPlan(
            name="jade-jsonl-worker",
            argv=(self.python_executable, "-u", "-m", "bip375_interop.jade_worker"),
            cwd=self.firmware_dir,
            env=env,
        )

    def plan_test(
        self,
        artifact_dir: str | Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> CommandPlan:
        env = {"BIP375_ARTIFACT_DIR": str(Path(artifact_dir).resolve())}
        env.update(extra_env or {})
        return CommandPlan(
            name="jade-bip375-tests",
            argv=(
                self.python_executable,
                "test_jade.py",
                "--serialport",
                self.device,
            ),
            cwd=self.firmware_dir,
            env=env,
        )

    def run(self, plan: CommandPlan, *, check: bool = True) -> CommandResult:
        return execute_plan(plan, self._runner, check=check)
