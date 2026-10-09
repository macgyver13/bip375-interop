"""One up-front check of everything a batch depends on, reported together."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

from .adapters.spdk import spdk_cli_binary
from .checkouts import CheckoutState, inspect_checkout
from .errors import CheckoutError, InteropError
from .models import Checkout, HarnessConfig, Scenario


def backend_checkout(backend: str) -> str:
    return "bitsaga-seedsigner" if backend in {"bitsaga", "bitsaga-seedsigner"} else backend


def _required_checkouts(scenarios: Iterable[Scenario]) -> list[str]:
    names: dict[str, None] = {}
    for scenario in scenarios:
        for signer in scenario.signers:
            names[backend_checkout(signer.backend)] = None
        for validator in scenario.validators:
            names[validator] = None
    return list(names)


def use_checkout_embit(config: HarnessConfig) -> None:
    """Import embit from the profile's checkout rather than whatever ``.venv`` has installed.

    Profiles pin different embit commits but share one venv, so an installed embit
    can only ever match one of them. Prepending the checkout's ``src`` to the import
    path, and to PYTHONPATH for the signer workers (every worker adapter keeps the
    inherited PYTHONPATH), makes the selected profile decide. Every embit import in
    the harness is inside a function, so this runs before any of them.
    """

    checkout = config.checkouts.get("embit")
    if checkout is None:
        return
    src = str((checkout.path / "src").resolve())
    sys.path.insert(0, src)
    inherited = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = os.pathsep.join([src, inherited] if inherited else [src])


def _embit_problem(checkout: Checkout | None) -> str | None:
    """The embit being imported must be the pinned checkout and have the API shape in use."""

    try:
        import embit
        from embit.silent_payments import SilentPaymentsPSBT  # noqa: F401
        from embit.silent_payments.sp import (  # noqa: F401
            derive_sp_outputs,
            get_eligible_inputs,
            group_sp_outputs_by_scan_key,
        )
    except ImportError as exc:
        return f"embit: Silent Payment API does not match the harness ({exc})"
    imported = Path(embit.__file__).resolve()
    if checkout is not None and not imported.is_relative_to(checkout.path.resolve()):
        return f"embit: imported from {imported.parent}, not the configured checkout {checkout.path}"
    grouped = group_sp_outputs_by_scan_key([])
    if not (isinstance(grouped, tuple) and len(grouped) == 2):
        return "embit: group_sp_outputs_by_scan_key does not return (groups, output indices)"
    return None


def default_spdk_binary() -> Path:
    """``spdk-cli`` release binary built in this repo, not in the spdk checkout."""

    return spdk_cli_binary()


def validator_build_problems(
    config: HarnessConfig,
    scenarios: Iterable[Scenario],
    *,
    spdk_binary: Path | None = None,
) -> list[str]:
    """Missing Caravan dist, SPDK binary or btclib venv. A missing build is not a skip."""

    needed: dict[str, None] = {}
    for scenario in scenarios:
        for name in scenario.validators:
            needed[name] = None
    problems: list[str] = []
    if "caravan" in needed:
        checkout = config.checkouts.get("caravan")
        if checkout is not None:
            dist = checkout.path / "packages" / "caravan-psbt" / "dist" / "index.js"
            if not dist.is_file():
                problems.append(
                    f"caravan: {dist} is not built (run npm ci and "
                    "npx turbo build --filter=@caravan/psbt... in the checkout)"
                )
    if "spdk" in needed:
        binary = default_spdk_binary() if spdk_binary is None else spdk_binary
        if not binary.is_file():
            problems.append(
                f"spdk: {binary} is not built (run cargo build --release in {binary.parents[2]})"
            )
    if "btclib" in needed:
        checkout = config.checkouts.get("btclib")
        if checkout is not None:
            python = checkout.path / ".venv" / "bin" / "python"
            if not python.is_file():
                problems.append(f"btclib: {python} is not built (run bip375-interop build btclib)")
    return problems


def run_preflight(config: HarnessConfig, scenarios: list[Scenario]) -> list[CheckoutState]:
    """Inspect every checkout the scenarios need and raise once with all problems."""

    if not scenarios:
        return []
    problems: list[str] = []
    states: list[CheckoutState] = []
    for name in _required_checkouts(scenarios):
        checkout = config.checkouts.get(name)
        if checkout is None:
            problems.append(f"{name}: no checkout configured")
            continue
        try:
            states.append(inspect_checkout(checkout, config.allow_dirty))
        except CheckoutError as exc:
            problems.append(str(exc))
    embit_problem = _embit_problem(config.checkouts.get("embit"))
    if embit_problem:
        problems.append(embit_problem)
    problems.extend(validator_build_problems(config, scenarios))
    if problems:
        raise InteropError("preflight failed:\n" + "\n".join(f"  - {problem}" for problem in problems))
    return states
