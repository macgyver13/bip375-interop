"""Single-command emulator smoke lanes backed by vendor-owned fixtures."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict
from pathlib import Path

from .adapters import ColdcardAdapter, JadeAdapter
from .artifacts import ArtifactRun
from .checkouts import inspect_checkout
from .engine import Round, run_rounds
from .errors import ConfigurationError
from .fixtures import build_bip375_fixture
from .models import HarnessConfig
from .models import Scenario
from .worker import WorkerClient


def jade_fixture(checkout: Path) -> tuple[str, bytes]:
    """Load Jade's maintained resolve-and-sign BIP-375 fixture."""

    path = checkout / "test_data" / "psbt_sp_resolve_sign.json"
    try:
        value = json.loads(path.read_text())
        network = value["input"]["network"]
        psbt = base64.b64decode(value["input"]["psbt"], validate=True)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"could not load Jade BIP-375 fixture {path}: {exc}") from exc
    if not isinstance(network, str):
        raise ConfigurationError(f"Jade BIP-375 fixture {path} has an invalid network")
    return network, psbt


def run_jade_smoke(config: HarnessConfig) -> tuple[Path, Path, int]:
    """Build/start one Jade QEMU and strictly validate its vendor fixture result."""

    checkout = config.checkouts.get("jade")
    if checkout is None:
        raise ConfigurationError("missing checkout configuration for jade")
    state = inspect_checkout(checkout, config.allow_dirty)
    network, initial = jade_fixture(checkout.path)
    artifacts = ArtifactRun(config.artifact_root, "jade-bip375-smoke")
    adapter = JadeAdapter(checkout.path)
    plan = adapter.plan_worker()
    worker = WorkerClient(plan.argv, cwd=plan.cwd, env=plan.env)
    try:
        worker.start("bip375", "jade-single", artifacts.path / "jade-a", network)
        final = run_rounds(initial, (Round("resolve-sign", ("jade-a",)),), {"jade-a": worker}, artifacts)
        manifest = artifacts.finalize({
            "scope": "single-device Jade QEMU BIP-375 transport",
            "mixed_device_psbt_interoperability": "not exercised",
            "checkouts": [asdict(state)],
        })
        return artifacts.path / "final.psbt", manifest, len(final)
    finally:
        worker.stop()


def _coldcard_scenario() -> Scenario:
    return Scenario.from_dict({
        "name": "coldcard-bip375-smoke",
        "suite": "bip375",
        "network": "regtest",
        "signers": [{"name": "coldcard-a", "backend": "coldcard", "seed_id": "test-a"}],
        "suite_config": {"contribution_mode": "per-input"},
        "inputs": [{"owner": "coldcard-a", "type": "p2wpkh", "amount_sat": 100000}],
        "outputs": [{"type": "silent-payment", "recipient_id": "recipient-a", "amount_sat": 95000}],
    })


def run_coldcard_smoke(config: HarnessConfig) -> tuple[Path, Path, int]:
    """Process a harness-built BIP-375 PSBT through a native Coldcard simulator."""

    checkout = config.checkouts.get("coldcard")
    if checkout is None:
        raise ConfigurationError("missing checkout configuration for coldcard")
    state = inspect_checkout(checkout, config.allow_dirty)
    scenario = _coldcard_scenario()
    initial = build_bip375_fixture(scenario)
    artifacts = ArtifactRun(config.artifact_root, scenario.name)
    adapter = ColdcardAdapter(checkout.path)
    plan = adapter.plan_worker()
    worker = WorkerClient(plan.argv, cwd=plan.cwd, env=plan.env)
    try:
        worker.start("bip375", "test-a", artifacts.path / "coldcard-a", "regtest")
        final = run_rounds(
            initial,
            (Round("resolve-sign", ("coldcard-a",)),),
            {"coldcard-a": worker},
            artifacts,
        )
        manifest = artifacts.finalize({
            "scope": "single-device Coldcard simulator BIP-375 transport",
            "mixed_device_psbt_interoperability": "not exercised",
            "checkouts": [asdict(state)],
        })
        return artifacts.path / "final.psbt", manifest, len(final)
    finally:
        worker.stop()
