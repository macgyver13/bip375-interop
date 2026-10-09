"""Adapter for spdk's rust-psbt-based finalizer as an independent validator.

spdk has no signer, so it never joins a round. It re-parses every PSBT
snapshot a run wrote through rust-psbt's own Finalizer, miniscript
interpreter (real Schnorr/ECDSA signature verification), and Extractor --
no embit involved anywhere in this path. Unlike Caravan's structural-only
check, this requires a fully signed PSBT, so it is only meaningful against
a run's later, signed snapshots (in particular ``final.psbt``).

The wrapper binary (``spdk-cli/`` at the repo root) is part of this harness,
same split as ``caravan_validate.cjs`` (an in-repo script) vs. Caravan's own
checkout -- the cryptography lives externally, the glue code lives here.
``checkout_dir`` names the *external* spdk checkout (see ``interop.yaml``'s
``spdk`` entry): it is tracked for dirty-checkout purposes, but the binary
is built from ``spdk-cli/Cargo.toml``'s git dependency, pinned by ``rev`` to
the same commit, and run from this repo's own ``spdk-cli/``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Sequence

from bip375_interop.adapters.base import CommandPlan, Runner, execute_plan, require_checkout
from bip375_interop.errors import InteropError

_CRATE_DIR = Path(__file__).resolve().parents[3] / "spdk-cli"


def spdk_cli_binary(crate_dir: Path = _CRATE_DIR) -> Path:
    """Where ``cargo build --release`` in ``crate_dir`` puts spdk-cli."""

    target_dir = os.environ.get("CARGO_TARGET_DIR")
    return (Path(target_dir) if target_dir else crate_dir / "target") / "release/spdk-cli"


class SpdkValidationError(InteropError):
    """spdk-cli rejected at least one PSBT snapshot."""


class SpdkAdapter:
    backend = "spdk"
    # finalize() requires a fully signed PSBT; unlike Caravan's structural-only
    # check, running this against an unresolved intermediate snapshot would
    # legitimately (and uninformatively) fail every time.
    snapshot_glob = "final.psbt"

    def __init__(
        self,
        checkout_dir: str | Path,
        *,
        crate_dir: str | Path | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        # This is the external spdk *library* checkout, not where the CLI
        # binary lives -- see the module docstring.
        self.checkout_dir = Path(checkout_dir).resolve()
        require_checkout(self.checkout_dir, ("psbt/Cargo.toml",))
        self.crate_dir = Path(crate_dir).resolve() if crate_dir is not None else _CRATE_DIR
        self.binary = spdk_cli_binary(self.crate_dir)
        self._runner = runner

    def plan_build(self) -> tuple[CommandPlan, ...]:
        return (
            CommandPlan("spdk-cli-build", ("cargo", "build", "--release"), self.crate_dir),
        )

    def plan_validate(self, psbt_paths: Sequence[Path]) -> CommandPlan:
        return CommandPlan(
            "spdk-validate",
            (str(self.binary), *(str(path) for path in psbt_paths)),
            self.crate_dir,
        )

    def validate(self, psbt_paths: Sequence[Path]) -> list[dict]:
        """Validate snapshots; return per-file results or raise on any rejection."""

        if not self.binary.is_file():
            raise SpdkValidationError(
                f"spdk: {self.binary} is not built (run cargo build --release "
                f"in {self.crate_dir})"
            )
        result = execute_plan(self.plan_validate(psbt_paths), self._runner, check=False)
        if result.returncode != 0:
            raise SpdkValidationError(f"spdk validator crashed: {result.stderr.strip()}")
        results = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        failures = [item for item in results if not item["ok"]]
        if failures:
            detail = "; ".join(f"{Path(item['file']).name}: {item['error']}" for item in failures)
            raise SpdkValidationError(f"spdk rejected {len(failures)} PSBT(s): {detail}")
        return results
