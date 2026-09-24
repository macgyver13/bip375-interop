"""Shared process-planning primitives for device adapters."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from bip375_interop.suites import KeyArchitecture, SuiteName


@dataclass(frozen=True)
class AdapterCapabilities:
    """Suites an adapter can execute without overstating device support."""

    plain_bip375: bool
    musig2_sp: bool
    musig2_key_architectures: frozenset[KeyArchitecture] = frozenset()

    def supports(self, suite: SuiteName) -> bool:
        if suite is SuiteName.BIP375:
            return self.plain_bip375
        if suite is SuiteName.MUSIG2_SP:
            return self.musig2_sp
        return False


@dataclass(frozen=True)
class SignerIdentity:
    """Stable logical identity assigned to one signer in a harness scenario."""

    name: str
    backend: str
    fingerprint: str | None = None


class SignerAdapter(Protocol):
    """Structural interface shared by harness signer backends."""

    backend: str
    capabilities: AdapterCapabilities

    def plan_build(self): ...

    def plan_test(self, *args, **kwargs): ...


@dataclass(frozen=True)
class CommandPlan:
    """A command that can be inspected before the harness executes it."""

    name: str
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandResult:
    """Stable subset of ``subprocess.CompletedProcess`` returned by adapters."""

    plan: CommandPlan
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., subprocess.CompletedProcess[str]]


def execute_plan(
    plan: CommandPlan,
    runner: Runner,
    *,
    check: bool = True,
    extra_env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Execute a plan with explicit environment overlays and captured output."""

    env = os.environ.copy()
    # A child started with cwd= keeps the parent's PWD; tools that read it (make's $(PWD))
    # need it to name the plan's directory.
    env["PWD"] = str(plan.cwd)
    env.update(plan.env)
    env.update(extra_env or {})
    completed = runner(
        list(plan.argv),
        cwd=plan.cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
    )
    return CommandResult(
        plan=plan,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def require_checkout(path: Path, markers: Sequence[str]) -> None:
    """Fail early when a configured checkout is not the expected project."""

    missing = [marker for marker in markers if not (path / marker).exists()]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"invalid checkout {path}: missing {joined}")
