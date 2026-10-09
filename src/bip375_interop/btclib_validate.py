"""Finalize and extract PSBT snapshots with btclib-wallet.

Runs in btclib-wallet's own venv (see adapters/btclib.py), not the harness's,
so it imports nothing from this package. ``extract_tx`` verifies the scripts
and, for a PSBT with a silent payment output, runs BIP-375's Extractor
checks: share coverage, DLEQ proofs, input eligibility, and every output
script recomputed from the ECDH shares.

usage: python btclib_validate.py <psbt>...
Prints one JSON line per file: {"file", "ok", "error"}.
"""

import json
import sys
from pathlib import Path

from btclib_wallet.psbt.psbt import Psbt, extract_tx, finalize


def validate_one(path: str) -> str | None:
    stage = "read"
    try:
        raw = Path(path).read_bytes()
        stage = "parse"
        psbt = Psbt.parse(raw)
        stage = "finalize"
        finalized = finalize(psbt)
        stage = "extract_tx"
        extract_tx(finalized)
    except Exception as exc:  # any rejection is a verdict, not a crash
        return f"{stage}: {exc}"
    return None


def main() -> None:
    # Exit 0 regardless of per-file outcome, as spdk-cli and caravan_validate.cjs do.
    for path in sys.argv[1:]:
        error = validate_one(path)
        print(json.dumps({"file": path, "ok": error is None, "error": error}))


if __name__ == "__main__":
    main()
