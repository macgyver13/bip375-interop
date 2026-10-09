"""Adapter for btclib-wallet as a BIP-375 software signer and an independent validator.

btclib-wallet is a pure-Python wallet library with its own PSBTv2 parser,
Finalizer and Extractor. Its Extractor runs BIP-375's checks and recomputes
every silent payment output script from the ECDH shares, so like spdk it
needs a fully signed PSBT and only checks ``final.psbt``.

btclib-wallet is installed editable into a venv inside its own checkout
(``.venv``, which its .gitignore covers), so its dependencies stay out of
the harness's venv. ``btclib_validate.py`` (in this package) runs in that
venv, and so does the signer worker (``signer_worker.BtclibWorker``), which
signs plain BIP-375 sends only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from bip375_interop.adapters.base import (
    AdapterCapabilities, CommandPlan, Runner, execute_plan, require_checkout,
)
from bip375_interop.errors import InteropError

_SCRIPT = Path(__file__).resolve().parents[1] / "btclib_validate.py"


class BtclibValidationError(InteropError):
    """btclib-wallet rejected at least one PSBT snapshot."""


class BtclibAdapter:
    backend = "btclib"
    capabilities = AdapterCapabilities(plain_bip375=True, musig2_sp=False)
    snapshot_glob = "final.psbt"

    def __init__(self, checkout_dir: str | Path, *, runner: Runner = subprocess.run) -> None:
        self.checkout_dir = Path(checkout_dir).resolve()
        require_checkout(self.checkout_dir, ("pyproject.toml", "src/btclib_wallet/psbt/psbt.py"))
        self.python = self.checkout_dir / ".venv/bin/python"
        self._runner = runner

    def plan_build(self) -> tuple[CommandPlan, ...]:
        return (
            CommandPlan("btclib-venv", (sys.executable, "-m", "venv", ".venv"), self.checkout_dir),
            CommandPlan(
                "btclib-install",
                (str(self.python), "-m", "pip", "install", "-q", "-e", ".[secp256k1]"),
                self.checkout_dir,
            ),
        )

    def plan_worker(self) -> CommandPlan:
        """A persistent JSON-lines signer worker in btclib-wallet's venv."""

        harness_src = Path(__file__).resolve().parents[2]
        return CommandPlan(
            "btclib-jsonl-worker",
            (str(self.python), "-u", "-m", "bip375_interop.signer_worker", "--backend", "btclib"),
            self.checkout_dir,
            {"PYTHONPATH": str(harness_src)},
        )

    def plan_validate(self, psbt_paths: Sequence[Path]) -> CommandPlan:
        return CommandPlan(
            "btclib-validate",
            (str(self.python), "-I", str(_SCRIPT), *(str(path) for path in psbt_paths)),
            self.checkout_dir,
        )

    def validate(self, psbt_paths: Sequence[Path]) -> list[dict]:
        """Validate snapshots; return per-file results or raise on any rejection."""

        if not self.python.is_file():
            raise BtclibValidationError(
                f"btclib: {self.python} is not built (run bip375-interop build btclib)"
            )
        result = execute_plan(self.plan_validate(psbt_paths), self._runner, check=False)
        if result.returncode != 0:
            raise BtclibValidationError(f"btclib validator crashed: {result.stderr.strip()}")
        results = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        failures = [item for item in results if not item["ok"]]
        if failures:
            detail = "; ".join(f"{Path(item['file']).name}: {item['error']}" for item in failures)
            raise BtclibValidationError(f"btclib rejected {len(failures)} PSBT(s): {detail}")
        return results
