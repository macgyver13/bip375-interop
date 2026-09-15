"""Round orchestration independent of any signing protocol implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .artifacts import ArtifactRun
from .psbt_maps import merge_psbts
from .worker import WorkerClient


@dataclass(frozen=True)
class Round:
    name: str
    signers: tuple[str, ...]


def run_rounds(
    initial_psbt: bytes,
    rounds: Sequence[Round],
    workers: Mapping[str, WorkerClient],
    artifacts: ArtifactRun,
) -> bytes:
    """Pass the cumulatively merged PSBT through explicitly ordered rounds."""
    current = initial_psbt
    artifacts.write("00-initial.psbt", current)
    step = 0
    for round_spec in rounds:
        for signer in round_spec.signers:
            step += 1
            returned = workers[signer].process_psbt(current, round_spec.name)
            artifacts.write(f"{step:02d}-{round_spec.name}-{signer}-returned.psbt", returned)
            current = merge_psbts(current, returned)
            artifacts.write(f"{step:02d}-{round_spec.name}-{signer}-merged.psbt", current)
    artifacts.write("final.psbt", current)
    return current
