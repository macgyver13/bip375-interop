"""Round orchestration independent of any signing protocol implementation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from .artifacts import ArtifactRun
from .models import Scenario
from .psbt_maps import DiffSummary, merge_psbts, parse_psbt, semantic_diff
from .verification import VerificationError
from .worker import WorkerClient, WorkerStepResult


@dataclass(frozen=True)
class Round:
    name: str
    signers: tuple[str, ...]


_SIGNATURE_FIELDS = {"partial_signature", "tap_key_signature"}


def run_rounds(
    initial_psbt: bytes,
    rounds: Sequence[Round],
    workers: Mapping[str, WorkerClient],
    artifacts: ArtifactRun,
    merge_policy: str = "strict",
    scenario: Scenario | None = None,
    on_round_complete: Callable[[str, bytes], None] | None = None,
) -> tuple[bytes, tuple[dict[str, Any], ...]]:
    """Pass the cumulatively merged PSBT through explicitly ordered rounds.

    Every step's semantic diff against the PSBT it received is written
    beside the returned PSBT so a device's contribution is auditable even
    when the merge itself succeeds.  Returns the final PSBT and the list of
    repairs the merge made (always empty under ``strict``).

    When ``scenario`` is given, each step's diff is checked against what its
    phase requires of that signer's own inputs (a share and no signature
    while contributing, a signature while signing) -- the phase name passed
    to ``process_psbt`` is purely local bookkeeping for this, never part of
    what reaches the device under test. ``on_round_complete``, when given, is
    called with the round's name and the merged PSBT once every signer in
    that round has contributed.
    """
    current = initial_psbt
    artifacts.write("00-initial.psbt", current)
    step = 0
    repairs: list[dict[str, Any]] = []
    for round_spec in rounds:
        for signer in round_spec.signers:
            step += 1
            step_result = workers[signer].process_psbt(current, round_spec.name)
            returned = step_result.psbt
            artifacts.write(f"{step:02d}-{round_spec.name}-{signer}-returned.psbt", returned)
            if step_result.story is not None:
                artifacts.write(
                    f"{step:02d}-{round_spec.name}-{signer}-story.txt",
                    _story_text(step_result.story),
                )
                _assert_no_unexpected_warning(scenario, signer, step_result.story)
            diff = semantic_diff(parse_psbt(current), parse_psbt(returned))
            artifacts.write(
                f"{step:02d}-{round_spec.name}-{signer}-diff.json",
                _diff_json(diff, step_result),
            )
            if scenario is not None:
                _assert_phase_contract(scenario, round_spec.name, signer, diff)
            current, step_repairs = merge_psbts(current, returned, merge_policy)
            for repair in step_repairs:
                repairs.append({
                    "step": step, "phase": round_spec.name, "signer": signer,
                    **asdict(repair),
                })
            artifacts.write(f"{step:02d}-{round_spec.name}-{signer}-merged.psbt", current)
        if on_round_complete is not None:
            on_round_complete(round_spec.name, current)
    artifacts.write("final.psbt", current)
    return current, tuple(repairs)


def _assert_phase_contract(
    scenario: Scenario, phase: str, signer: str, diff: DiffSummary
) -> None:
    if phase not in {"contribute", "resolve-sign", "sign"}:
        return
    owned = {
        index for index, item in enumerate(scenario.inputs) if item.get("owner") == signer
    }
    if not owned:
        return
    added_signature = any(
        change.scope == "input" and change.index in owned and change.field in _SIGNATURE_FIELDS
        for change in diff.added
    )
    added_share = any(
        change.scope == "input" and change.index in owned and change.field == "sp_ecdh_share"
        for change in diff.added
    )
    if phase == "contribute":
        if added_signature:
            raise VerificationError(
                f"signer {signer!r} added a signature during the contribute phase"
            )
        if not added_share:
            raise VerificationError(
                f"signer {signer!r} did not add a Silent Payment ECDH share during "
                "the contribute phase"
            )
    elif not added_signature:
        raise VerificationError(f"signer {signer!r} did not add a signature during the {phase} phase")


def _story_text(story: Mapping[str, str]) -> bytes:
    title = story.get("title", "")
    body = story.get("body", "")
    return f"{title}\n\n{body}\n".encode()


def _assert_no_unexpected_warning(
    scenario: Scenario | None, signer: str, story: Mapping[str, str]
) -> None:
    title = story.get("title") or ""
    if "warning" not in title.lower():
        return
    if scenario is not None and scenario.expect_warning:
        return
    raise VerificationError(
        f"signer {signer!r} showed an unexpected Coldcard warning: {title!r}"
    )


def _diff_json(diff, step_result: WorkerStepResult) -> bytes:
    payload = {
        "added": [asdict(change) for change in diff.added],
        "removed": [asdict(change) for change in diff.removed],
        "modified": [asdict(change) for change in diff.modified],
        "story": step_result.story,
        "signatures_added": step_result.signatures_added,
        "stage": step_result.stage,
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"
