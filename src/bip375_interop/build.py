"""Build a profile's checkouts by running each one's plan_build."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .adapters import CaravanAdapter, ColdcardAdapter, JadeAdapter, SeedSignerAdapter, SpdkAdapter
from .adapters.base import CommandPlan, Runner, execute_plan
from .errors import InteropError
from .models import HarnessConfig

# The binaries scripts/musig2-regtest.sh runs (see SP_DEMO_BIN).
SP_DEMO_BINS = ("build_round1", "finalize", "broadcast_final", "verify_onchain")


class BuildError(InteropError):
    pass


def _silent_pay(path: Path) -> tuple[CommandPlan, ...]:
    bins = [arg for name in SP_DEMO_BINS for arg in ("--bin", name)]
    return (CommandPlan("sp-demo-build", ("cargo", "build", "--release", "--locked", "-p", "sp-demo", *bins), path),)


def _embit(path: Path) -> tuple[CommandPlan, ...]:
    # Editable, so preflight sees embit imported from its checkout.
    return (CommandPlan("embit-install", (sys.executable, "-m", "pip", "install", "-q", "-e", "."), path),)


_PLANS = {
    "coldcard": lambda path: ColdcardAdapter(path).plan_build(),
    "jade": lambda path: JadeAdapter(path, require_build=False).plan_build(),
    "seedsigner": lambda path: SeedSignerAdapter(path).plan_build(),
    "caravan": lambda path: CaravanAdapter(path).plan_build(),
    "spdk": lambda path: SpdkAdapter(path).plan_build(),
    "silent-pay": _silent_pay,
    "embit": _embit,
}


def plan_builds(config: HarnessConfig, only: Iterable[str] = ()) -> list[tuple[str, tuple[CommandPlan, ...]]]:
    names = list(only) or list(config.checkouts)
    unknown = [name for name in names if name not in config.checkouts]
    if unknown:
        raise BuildError(f"not in this profile: {', '.join(unknown)}")
    if "jade" in names and not os.environ.get("IDF_PATH"):
        raise BuildError("jade: IDF_PATH must name an ESP-IDF checkout (its export.sh sets up the build)")
    return [(name, _PLANS[name](config.checkouts[name].path)) for name in names if name in _PLANS]


def build(config: HarnessConfig, only: Iterable[str] = (), runner: Runner = subprocess.run) -> list[str]:
    """Run every step in order; stop at the first failure with its output."""

    steps = []
    for name, plans in plan_builds(config, only):
        for plan in plans:
            print(f"== {name}: {plan.name}", file=sys.stderr, flush=True)
            result = execute_plan(plan, runner, check=False)
            if result.returncode != 0:
                tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-20:])
                raise BuildError(f"{name}: {plan.name} failed ({result.returncode}):\n{tail}")
            steps.append(plan.name)
    return steps
