"""Round orchestration independent of any signing protocol implementation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

from .artifacts import ArtifactRun
from .models import Scenario
from .psbt_maps import DiffSummary, PsbtMergeError, _encode_compact_size, merge_psbts, parse_psbt, semantic_diff
from .verification import VerificationError
from .worker import WorkerClient, WorkerStepResult


@dataclass(frozen=True)
class Round:
    name: str
    signers: tuple[str, ...]


_SIGNATURE_FIELDS = {"partial_signature", "tap_key_signature"}
_UTXO_FIELDS = {"witness_utxo", "non_witness_utxo"}
_SECURITY_FIELDS = {
    "input": _SIGNATURE_FIELDS | _UTXO_FIELDS | {
        "sighash_type", "final_scriptsig", "final_scriptwitness",
        "sp_ecdh_share", "sp_dleq",
    },
    "output": {"script", "sp_v0_info"},
    "global": {"sp_ecdh_share", "sp_dleq", "tx_modifiable"},
}


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
    repairs the merge made (always empty under ``strict``). A merge rejection
    names the step, phase, and signer and keeps the field text.

    When ``scenario`` is given, each step's diff is checked against what its
    phase requires of that signer: a share and DLEQ, and no signature, output
    script, or witness UTXO, while contributing; a signature only on an owned
    input while signing. The phase name passed to ``process_psbt`` is purely
    local bookkeeping for this, never part of what reaches the device under
    test. ``on_round_complete``, when given, is called with the round's name
    and the merged PSBT once every signer in that round has contributed.
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
            try:
                current, step_repairs = merge_psbts(current, returned, merge_policy)
            except PsbtMergeError as exc:
                raise PsbtMergeError(
                    f"step {step} phase {round_spec.name} signer {signer!r}: {exc}"
                ) from exc
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


def _global_share_phase(scenario: Scenario) -> bool:
    """Single-owner and explicit global runs write the global share."""

    owners = {item.get("owner") for item in scenario.inputs}
    mode = scenario.suite_config.get("contribution_mode", "per-input")
    return mode == "global" or len(owners) <= 1


def _witness(*items: bytes) -> bytes:
    return _encode_compact_size(len(items)) + b"".join(
        _encode_compact_size(len(item)) + item for item in items
    )


def _signature_witnesses(diff: DiffSummary, index: int) -> set[bytes]:
    """Final witnesses made of exactly a signature added on this input in this step."""

    stacks = set()
    for change in diff.added:
        if change.scope != "input" or change.index != index:
            continue
        if change.field == "tap_key_signature":
            stacks.add(_witness(bytes.fromhex(change.after_hex)))
        elif change.field == "partial_signature":
            pubkey = bytes.fromhex(change.key_hex)[1:]
            stacks.add(_witness(bytes.fromhex(change.after_hex), pubkey))
    return stacks


def _addition_allowed(
    scenario: Scenario, phase: str, owned: set[int], change, diff: DiffSummary
) -> bool:
    """Security-relevant additions this phase is supposed to make.

    Proprietary and unknown fields are not in this set and stay additive.
    """

    if change.scope == "input" and change.field in _SIGNATURE_FIELDS:
        return phase in {"resolve-sign", "sign"} and change.index in owned
    # A signer that also finalizes (SeedSigner's embit, on taproot inputs) may
    # add a final witness, but only one carrying the signature it just added,
    # since verification checks the signature and the witness is what is broadcast.
    if change.scope == "input" and change.field == "final_scriptwitness":
        return (
            phase in {"resolve-sign", "sign"}
            and change.index in owned
            and bytes.fromhex(change.after_hex) in _signature_witnesses(diff, change.index)
        )
    if change.scope == "input" and change.field in {"sp_ecdh_share", "sp_dleq"}:
        if phase == "contribute" and change.index in owned:
            return True
        # The resolving signer has no contribute round, so their share lands here.
        return (
            phase == "resolve-sign"
            and change.index in owned
            and not _global_share_phase(scenario)
        )
    if change.scope == "output" and change.field == "script":
        return phase == "resolve-sign"
    if change.scope == "global" and change.field in {"sp_ecdh_share", "sp_dleq"}:
        return phase == "resolve-sign" and _global_share_phase(scenario)
    return False


def _reject_security_addition(signer: str, phase: str, change) -> None:
    if change.scope == "input" and change.field in _SIGNATURE_FIELDS:
        raise VerificationError(
            f"signer {signer!r} added a signature on unowned input {change.index} "
            f"during the {phase} phase"
        )
    if change.field in _UTXO_FIELDS:
        raise VerificationError(
            f"signer {signer!r} added a witness UTXO during the {phase} phase"
        )
    where = change.scope if change.index is None else f"{change.scope} {change.index}"
    raise VerificationError(
        f"signer {signer!r} added {where} {change.field} during the {phase} phase"
    )


def _assert_phase_contract(
    scenario: Scenario, phase: str, signer: str, diff: DiffSummary
) -> None:
    if phase not in {"contribute", "resolve-sign", "sign"}:
        return
    owned = {
        index for index, item in enumerate(scenario.inputs) if item.get("owner") == signer
    }
    for change in diff.added:
        fields = _SECURITY_FIELDS.get(change.scope)
        if fields is None or change.field not in fields:
            continue
        if _addition_allowed(scenario, phase, owned, change, diff):
            continue
        if change.scope == "input" and change.field in _SIGNATURE_FIELDS and phase == "contribute":
            if change.index not in owned:
                raise VerificationError(
                    f"signer {signer!r} added a signature on unowned input {change.index} "
                    "during the contribute phase"
                )
            raise VerificationError(
                f"signer {signer!r} added a signature during the contribute phase"
            )
        _reject_security_addition(signer, phase, change)
    if phase == "contribute":
        if not owned:
            return
        for index in sorted(owned):
            added = {
                change.field
                for change in diff.added
                if change.scope == "input" and change.index == index
            }
            if "sp_ecdh_share" not in added:
                raise VerificationError(
                    f"signer {signer!r} did not add a Silent Payment ECDH share during "
                    "the contribute phase"
                )
            if "sp_dleq" not in added:
                raise VerificationError(
                    f"signer {signer!r} did not add a Silent Payment DLEQ proof during "
                    "the contribute phase"
                )
        return
    if not owned:
        return
    if not any(
        change.scope == "input" and change.index in owned and change.field in _SIGNATURE_FIELDS
        for change in diff.added
    ):
        raise VerificationError(
            f"signer {signer!r} did not add a signature during the {phase} phase"
        )


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
