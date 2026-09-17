"""Scenario discovery and conservative project-based regression selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from .config import load_scenario
from .errors import ConfigurationError
from .models import Scenario


@dataclass(frozen=True)
class ScenarioEntry:
    path: Path
    scenario: Scenario
    runnable_generated: bool
    reason: str | None


def discover(root: Path) -> tuple[ScenarioEntry, ...]:
    entries = []
    for path in sorted(root.glob("*.yaml")):
        scenario = load_scenario(path)
        runnable = scenario.suite == "bip375" and bool(scenario.inputs) and bool(scenario.outputs)
        reason = None if runnable else (
            f"no initial PSBT: store {path.with_suffix('.psbt').name} next to the scenario or pass --psbt"
            if scenario.suite == "musig2-sp"
            else "suite is not supported"
        )
        entries.append(ScenarioEntry(path, scenario, runnable, reason))
    return tuple(entries)


def select(entries: tuple[ScenarioEntry, ...], project: str) -> tuple[ScenarioEntry, ...]:
    # Feature mapping is intentionally conservative until explicit path rules
    # are versioned in the catalog: a project change exercises every scenario
    # that names that backend.
    if project == "harness":
        return entries
    return tuple(
        entry for entry in entries
        if any(signer.backend == project for signer in entry.scenario.signers)
        or project in entry.scenario.validators
    )


def changed_files(root: Path, since: str) -> tuple[str, ...]:
    """Return tracked and untracked local changes relative to a Git revision."""

    try:
        tracked = subprocess.check_output(
            ["git", "-C", str(root), "diff", "--name-only", since], text=True
        )
        untracked = subprocess.check_output(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"], text=True
        )
    except subprocess.CalledProcessError as exc:
        raise ConfigurationError(f"could not inspect changes since {since!r} in {root}: {exc}") from exc
    return tuple(sorted({*tracked.splitlines(), *untracked.splitlines()}))
