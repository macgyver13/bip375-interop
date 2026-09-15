"""Single-command emulator smoke lanes backed by vendor-owned fixtures."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict
from pathlib import Path

from .adapters import JadeAdapter
from .artifacts import ArtifactRun
from .checkouts import inspect_checkout
from .engine import Round, run_rounds
from .errors import ConfigurationError
from .models import HarnessConfig
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
