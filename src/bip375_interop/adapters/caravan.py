"""Adapter for Caravan's TypeScript PSBTv2 library as an independent validator.

Caravan has no signer, so it never joins a round. It re-parses every PSBT
snapshot a run wrote; its ``PsbtV2`` constructor rejects malformed silent
payment fields, bad DLEQ proofs, and output scripts that do not match its own
BIP-352 derivation.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Sequence

from bip375_interop.adapters.base import CommandPlan, Runner, execute_plan, require_checkout
from bip375_interop.errors import InteropError

_SCRIPT = Path(__file__).resolve().parents[1] / "caravan_validate.cjs"


class CaravanValidationError(InteropError):
    """Caravan rejected at least one PSBT snapshot."""


class CaravanAdapter:
    backend = "caravan"
    snapshot_glob = "*.psbt"

    def __init__(self, checkout_dir: str | Path, *, runner: Runner = subprocess.run) -> None:
        self.checkout_dir = Path(checkout_dir).resolve()
        require_checkout(self.checkout_dir, ("turbo.json", "packages/caravan-psbt/package.json"))
        self.dist = self.checkout_dir / "packages/caravan-psbt/dist/index.js"
        self._runner = runner

    def plan_build(self) -> tuple[CommandPlan, ...]:
        return (
            CommandPlan("caravan-install", ("npm", "ci"), self.checkout_dir),
            CommandPlan(
                "caravan-psbt-build",
                ("npx", "turbo", "build", "--filter=@caravan/psbt..."),
                self.checkout_dir,
            ),
        )

    def plan_validate(self, psbt_paths: Sequence[Path]) -> CommandPlan:
        return CommandPlan(
            "caravan-validate",
            ("node", str(_SCRIPT), str(self.dist), *(str(path) for path in psbt_paths)),
            self.checkout_dir,
        )

    def validate(self, psbt_paths: Sequence[Path]) -> list[dict]:
        """Validate snapshots; return per-file results or raise on any rejection."""

        if not self.dist.is_file():
            raise CaravanValidationError(
                f"caravan: {self.dist} is not built (run npm ci and "
                "npx turbo build --filter=@caravan/psbt... in the checkout)"
            )
        result = execute_plan(self.plan_validate(psbt_paths), self._runner, check=False)
        if result.returncode != 0:
            raise CaravanValidationError(f"caravan validator crashed: {result.stderr.strip()}")
        results = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        failures = [item for item in results if not item["ok"]]
        if failures:
            detail = "; ".join(f"{Path(item['file']).name}: {item['error']}" for item in failures)
            raise CaravanValidationError(f"caravan rejected {len(failures)} PSBT(s): {detail}")
        return results
