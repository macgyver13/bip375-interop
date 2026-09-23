"""One up-front check of everything a batch depends on, reported together."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

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

    return Path(__file__).resolve().parents[2] / "spdk-cli" / "target" / "release" / "spdk-cli"


def validator_build_problems(
    config: HarnessConfig,
    scenarios: Iterable[Scenario],
    *,
    spdk_binary: Path | None = None,
) -> list[str]:
    """Missing Caravan dist or SPDK binary. A missing build is not a skip."""

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
