"""Explicit evidence checks for completed interoperability runs.

These checks read the PSBT itself for outpoints, recipient keys, and
derivation paths, and match each input's BIP32 fingerprint against the
harness's own published test seeds, rather than trusting that a signer or
resolver merely touched every required PSBT field, or leaning on a fixture
generator's own generation-time conventions.
"""

from __future__ import annotations

from binascii import unhexlify
from collections.abc import Sequence

from .errors import InteropError
from .models import Scenario
from .psbt_maps import PsbtV2, parse_psbt
from .test_seeds import mnemonic_for


class VerificationError(InteropError):
    """A run completed transport but did not produce required PSBT evidence."""


_PARTIAL_SIGNATURE = 0x02
_TAP_KEY_SIGNATURE = 0x13
_SP_ECDH_SHARE = 0x1D
_SP_DLEQ = 0x1E
_GLOBAL_SP_ECDH_SHARE = 0x07
_GLOBAL_SP_DLEQ = 0x08
_MUSIG2_PUB_NONCE = 0x1B
_MUSIG2_PARTIAL_SIG = 0x1C


_INPUTS_MODIFIABLE = 0x01
_OUTPUTS_MODIFIABLE = 0x02
_RECIPIENTS = {
    "recipient-a": (
        unhexlify("027a487fc19fb769877b8742d6ea18118f3c4e72b1ea8c6de602a7ad4a41dbe068"),
        unhexlify("0361e1b1e9de5e42cb2007f7ca54b9e0d57ed13938fad56d3f19e57513a8fce039"),
    ),
    "recipient-b": (
        unhexlify("034f355bdcb7cc0af728ef3cceb9615d90684bb5b2ca5f859ab0f0b704075871aa"),
        unhexlify("02466d7fcae563e5cb09a0d1870bb580344804617879a14949cf22285f1bae3f27"),
    ),
}
_PLAIN_DEST_PUBKEY = unhexlify(
    "0230282f721a0742d05986818c9cbd71757394d4c6602cd6814d5647e50b57e28c"
)




def _check_tx_modifiable(parsed: PsbtV2) -> None:
    """After resolution, inputs/outputs modifiable must be clear or absent.

    Both bits are legal to clear earlier (e.g. Coldcard clears them during
    MuSig2 round 1) or to leave set until later (Coldcard leaves them set
    through a plain BIP-375 ``contribute`` step) -- only their state in the
    final PSBT is a completion requirement.
    """

    value = parsed.globals.get(b"\x06")
    if value is None:
        return
    flags = value[0]
    if flags & _INPUTS_MODIFIABLE:
        raise VerificationError("final PSBT still has inputs-modifiable set")
    if flags & _OUTPUTS_MODIFIABLE:
        raise VerificationError("final PSBT still has outputs-modifiable set")


def _signer_roots(scenario: Scenario) -> list:
    from embit import bip32, bip39

    return [
        bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for(signer.seed_id)))
        for signer in scenario.signers
    ]


def _match_input_privkey(roots: list, inp) -> bytes | None:
    """Resolve an input's private key against every scenario seed's root.

    Tries each seed's BIP32, taproot BIP32, or SP spend derivation field --
    matched internally by fingerprint -- via embit's own resolver, so this
    needs no assumption about which derivation path a device used.
    """

    from embit.psbt import derive_hdkey
    from embit.silent_payments.signing import resolve_input_privkey

    for root in roots:
        priv = resolve_input_privkey(inp, root, root.my_fingerprint, derive_hdkey)
        if priv is not None:
            return priv
    return None


def _derive_sp(scenario: Scenario, embit_psbt):
    """Return ``(A_sum_sec, results, output_order)`` from the scenario seeds.

    ``results`` is ``{scan_key: (ecdh_share, outputs)}``. ``A_sum_sec`` is the
    public sum of the eligible input keys, which is what a global DLEQ proof
    is over. This mirrors ``SilentPaymentsPSBT._verify_declared_sp`` without
    calling that private method.
    """

    from embit.silent_payments.sp import (
        derive_sp_outputs,
        get_eligible_inputs,
        group_sp_outputs_by_scan_key,
    )

    scan_spend_groups, output_order = group_sp_outputs_by_scan_key(embit_psbt.outputs)
    roots = _signer_roots(scenario)
    priv_keys = []
    for index in get_eligible_inputs(embit_psbt.inputs):
        priv = _match_input_privkey(roots, embit_psbt.inputs[index])
        if priv is None:
            raise VerificationError(f"input {index} fingerprint matches no scenario seed")
        priv_keys.append(priv)
    if not priv_keys:
        raise VerificationError("scenario has no BIP-352 eligible inputs")
    derivation = derive_sp_outputs(
        priv_keys, [inp.vin for inp in embit_psbt.inputs], scan_spend_groups,
    )
    if derivation is None:
        raise VerificationError("scenario input private keys sum to zero")
    _, a_sum_sec, results = derivation
    return a_sum_sec, results, output_order


def expected_sp_output_script(scenario: Scenario, psbt: bytes, output_index: int) -> bytes:
    """Independently recompute the BIP-352 output a device is expected to resolve.

    Reads outpoints from every input's previous txid/output index, the
    recipient scan/spend keys from this output's own PSBT_OUT_SP_V0_INFO, and
    each eligible input's derivation path from its BIP32, taproot BIP32, or
    SP spend derivation field -- matched by fingerprint against the
    scenario's own published test seeds -- so this needs no fixture
    generation-time convention.
    """

    from embit.silent_payments import SilentPaymentsPSBT

    embit_psbt = SilentPaymentsPSBT.parse(psbt)
    if output_index >= len(embit_psbt.outputs) or embit_psbt.outputs[output_index].sp_data is None:
        raise VerificationError(
            f"output {output_index} has no Silent Payment recipient information"
        )

    _, results, output_order = _derive_sp(scenario, embit_psbt)
    target_scan = embit_psbt.outputs[output_index].sp_data.scan_key.sec()
    _, outputs = results[target_scan]
    position = output_order[target_scan].index(output_index)
    return b"\x51\x20" + outputs[position]


def _verify_global_sp_evidence(scenario: Scenario, embit_psbt, parsed: PsbtV2) -> None:
    """Require the global ECDH share and DLEQ a single-signer resolution writes.

    The proof is over ``A_sum``, the sum of eligible input keys, and the share
    must equal the share derived from the scenario seeds. A matching output
    script is not a substitute. A run with no Silent Payment output never
    reaches here: that is the mode that legitimately has no share record.
    """

    from embit.silent_payments.dleq import verify_dleq_proof

    a_sum_sec, results, _ = _derive_sp(scenario, embit_psbt)
    for scan_key_data, (expected_share, _) in results.items():
        share = parsed.globals.get(bytes([_GLOBAL_SP_ECDH_SHARE]) + scan_key_data)
        proof = parsed.globals.get(bytes([_GLOBAL_SP_DLEQ]) + scan_key_data)
        if share is None:
            raise VerificationError("missing a global Silent Payment ECDH share")
        if proof is None:
            raise VerificationError("missing a global Silent Payment DLEQ proof")
        if share != expected_share:
            raise VerificationError(
                "global Silent Payment ECDH share does not match the derived share"
            )
        if not verify_dleq_proof(a_sum_sec, scan_key_data, share, proof):
            raise VerificationError("global Silent Payment DLEQ proof does not verify")


def _verify_sp_input_evidence(scenario: Scenario, embit_psbt, parsed: PsbtV2) -> None:
    """Require Silent Payment share and DLEQ evidence for every eligible input.

    A multi-owner per-input run must carry a per-input share and proof, and
    must not carry a global share. A single-owner or ``contribution_mode:
    global`` run must carry the global share and proof instead. No Silent
    Payment output means no share record is required.
    """

    from embit import ec
    from embit.silent_payments.dleq import verify_dleq_proof
    from embit.silent_payments.sp import get_eligible_inputs

    sp_outputs = [output for output in embit_psbt.outputs if output.sp_data is not None]
    if not sp_outputs:
        return
    owners = {item.get("owner") for item in scenario.inputs}
    mode = scenario.suite_config.get("contribution_mode", "per-input")
    if mode == "global" or len(owners) <= 1:
        _verify_global_sp_evidence(scenario, embit_psbt, parsed)
        return

    roots = _signer_roots(scenario)
    eligible = get_eligible_inputs(embit_psbt.inputs)
    scan_keys = {output.sp_data.scan_key.sec() for output in sp_outputs}
    for scan_key_data in scan_keys:
        global_share_key = bytes([_GLOBAL_SP_ECDH_SHARE]) + scan_key_data
        if parsed.globals.get(global_share_key) is not None:
            raise VerificationError(
                "multi-owner run resolved with a global Silent Payment ECDH share "
                "instead of per-input shares"
            )

        for index in eligible:
            priv = _match_input_privkey(roots, embit_psbt.inputs[index])
            if priv is None:
                raise VerificationError(f"input {index} fingerprint matches no scenario seed")
            entries = parsed.inputs[index].entries
            share = next(
                (
                    entry.value for entry in entries
                    if entry.key_type == _SP_ECDH_SHARE and entry.key_data == scan_key_data
                ),
                None,
            )
            proof = next(
                (
                    entry.value for entry in entries
                    if entry.key_type == _SP_DLEQ and entry.key_data == scan_key_data
                ),
                None,
            )
            if share is None:
                raise VerificationError(f"input {index} is missing a Silent Payment ECDH share")
            if proof is None:
                raise VerificationError(f"input {index} is missing a Silent Payment DLEQ proof")
            pubkey_sec = ec.PrivateKey(priv).get_public_key().sec()
            if not verify_dleq_proof(pubkey_sec, scan_key_data, share, proof):
                raise VerificationError(
                    f"input {index} Silent Payment DLEQ proof does not verify against its own key"
                )

def _verify_p2wpkh_signature(embit_psbt, index: int, raw: bytes, pubkey_sec: bytes) -> None:
    """Verify a BIP-174 partial signature: DER bytes plus one sighash byte.

    ``Signature.parse`` rejects trailing bytes, so the sighash byte is split
    off before parsing. A value shorter than DER-plus-sighash, a malformed
    DER body, or a signature that does not verify is a failure.
    """

    from embit import ec

    if len(raw) < 2:
        raise VerificationError(f"input {index} partial signature is missing a sighash byte")
    der, sighash = raw[:-1], raw[-1]
    try:
        sig = ec.Signature.parse(der)
    except Exception as exc:
        raise VerificationError(
            f"input {index} partial signature is not a DER signature plus sighash byte"
        ) from exc
    pubkey = ec.PublicKey.parse(pubkey_sec)
    msg_hash = embit_psbt.sighash(index, sighash=sighash)
    if not pubkey.verify(sig, msg_hash):
        raise VerificationError(
            f"input {index} partial signature does not verify against the owner's public key"
        )



def _verify_input_signature(roots: list, embit_psbt, parsed: PsbtV2, index: int) -> None:
    from embit import ec

    inp = embit_psbt.inputs[index]
    priv = _match_input_privkey(roots, inp)
    if priv is None:
        raise VerificationError(f"input {index} fingerprint matches no scenario seed")
    entries = parsed.inputs[index].entries
    script_type = inp.script_pubkey.script_type() if inp.script_pubkey is not None else None

    if script_type == "p2wpkh":
        entry = next((entry for entry in entries if entry.key_type == _PARTIAL_SIGNATURE), None)
        if entry is None:
            raise VerificationError(f"input {index} is missing a partial signature")
        expected_pubkey = ec.PrivateKey(priv).get_public_key().sec()
        if entry.key_data != expected_pubkey:
            raise VerificationError(
                f"input {index} partial signature key does not match the owner's derived public key"
            )
        _verify_p2wpkh_signature(embit_psbt, index, entry.value, expected_pubkey)
        return

    entry = next((entry for entry in entries if entry.key_type == _TAP_KEY_SIGNATURE), None)
    if entry is None:
        raise VerificationError(f"input {index} is missing a taproot key signature")
    sig = entry.value
    # BIP-341: a 64-byte signature carries no explicit sighash byte, meaning
    # SIGHASH_DEFAULT was used; a 65-byte signature's trailing byte names the
    # sighash explicitly. DEFAULT and ALL are functionally identical for
    # taproot, so either is a genuine signature over the owner's output key.
    if len(sig) == 65:
        sighash = sig[64]
        sig = sig[:64]
    elif len(sig) == 64:
        sighash = 0
    else:
        raise VerificationError(f"input {index} taproot signature has an invalid length")

    utxo = inp.utxo
    if utxo is None:
        raise VerificationError(f"input {index} is missing UTXO information")
    output_xonly = utxo.script_pubkey.data[2:34]
    pubkey = ec.PublicKey.parse(b"\x02" + output_xonly)
    msg_hash = embit_psbt.sighash(index, sighash=sighash)
    if not pubkey.schnorr_verify(ec.SchnorrSig(sig), msg_hash):
        raise VerificationError(
            f"input {index} taproot signature does not verify against the owner's output key"
        )


def _verify_bip375_structural(scenario: Scenario, psbt: bytes) -> None:
    """Weak fallback check: every output has a script, every input a signature key type.

    Does not recompute anything or verify any cryptography. Opt in with the
    scenario's ``verification: structural`` field only for the rare case
    that the full check in :func:`verify_bip375_completion` cannot apply.
    That opt-out is not evidence and must not be reported as a pass.
    """

    parsed = parse_psbt(psbt)
    if len(parsed.inputs) != len(scenario.inputs):
        raise VerificationError("final PSBT input count does not match the scenario")
    if any(output.get(b"\x04") is None for output in parsed.outputs):
        raise VerificationError("final PSBT has an unresolved output script")
    for index, item in enumerate(scenario.inputs):
        key_types = {entry.key_type for entry in parsed.inputs[index].entries}
        required = _TAP_KEY_SIGNATURE if item.get("type") in {"p2tr", "sp-spend"} else _PARTIAL_SIGNATURE
        if required not in key_types:
            name = "taproot key signature" if required == _TAP_KEY_SIGNATURE else "partial signature"
            raise VerificationError(f"input {index} is missing a {name}")
    _check_tx_modifiable(parsed)


def declares_intent(scenario: Scenario) -> bool:
    """True when the scenario names both inputs and outputs."""

    return bool(scenario.inputs) and bool(scenario.outputs)


def recipient_keys(recipient_id: str) -> tuple[bytes, bytes]:
    """Scan and spend keys for a named recipient. Unknown ids raise KeyError."""

    try:
        return _RECIPIENTS[recipient_id]
    except KeyError:
        raise KeyError(recipient_id) from None


def known_recipient_ids() -> tuple[str, ...]:
    return tuple(sorted(_RECIPIENTS))


def recipient_sp_info(recipient_id: str) -> bytes:
    """66-byte scan||spend a Silent Payment output must carry for this recipient."""

    try:
        scan, spend = _RECIPIENTS[recipient_id]
    except KeyError:
        raise VerificationError(f"unknown recipient_id {recipient_id!r}") from None
    return scan + spend


def expected_plain_output_script(output_type: str) -> bytes:
    """Raw script the fixture writes for a plain p2wpkh or p2tr output."""

    from embit import ec, script

    pubkey = ec.PublicKey.parse(_PLAIN_DEST_PUBKEY)
    if output_type == "p2wpkh":
        return script.p2wpkh(pubkey).data
    if output_type == "p2tr":
        return script.p2tr(pubkey).data
    raise VerificationError(f"no plain output script for type {output_type!r}")


def require_input_utxos(psbt: bytes) -> str:
    """Require every input to already carry a UTXO before any signer runs.

    Callers invoke this before ``run_rounds``. An input may carry
    ``witness_utxo``, ``non_witness_utxo``, or both. Both must agree: the
    embedded transaction's txid matches ``previous_txid``, and the output at
    ``output_index`` matches ``witness_utxo`` when that field is also present.
    Returns ``declared`` when any input has only ``witness_utxo``. Returns
    ``non_witness_utxo`` when every input was checked against its embedded
    transaction. Never claims a prevout was fetched from a node.
    """

    parsed = parse_psbt(psbt)
    if not parsed.inputs:
        raise VerificationError("PSBT has no inputs to check for a UTXO")
    declared_only = False
    for index, item in enumerate(parsed.inputs):
        witness = item.get(b"\x01")
        non_witness = item.get(b"\x00")
        if witness is None and non_witness is None:
            raise VerificationError(
                f"input {index} is missing witness_utxo and non_witness_utxo"
            )
        if non_witness is not None:
            _check_non_witness_utxo(index, item, non_witness, witness)
        else:
            declared_only = True
    return "declared" if declared_only else "non_witness_utxo"


def _check_non_witness_utxo(
    index: int, item, raw_tx: bytes, witness: bytes | None
) -> None:
    from embit.transaction import Transaction

    prev = item.get(b"\x0e")
    vout_raw = item.get(b"\x0f")
    if prev is None or vout_raw is None:
        raise VerificationError(
            f"input {index} is missing previous txid or output index"
        )
    try:
        tx = Transaction.parse(raw_tx)
    except Exception as exc:
        raise VerificationError(
            f"input {index} non_witness_utxo is not a transaction"
        ) from exc
    if tx.txid() != bytes(reversed(prev)):
        raise VerificationError(
            f"input {index} non_witness_utxo txid does not match previous_txid"
        )
    vout = int.from_bytes(vout_raw, "little")
    if vout >= len(tx.vout):
        raise VerificationError(
            f"input {index} output index {vout} is past the non_witness_utxo outputs"
        )
    if witness is not None and tx.vout[vout].serialize() != witness:
        raise VerificationError(
            f"input {index} witness_utxo does not match non_witness_utxo output {vout}"
        )


def _input_txout(index: int, item):
    from embit.transaction import Transaction, TransactionOutput

    witness = item.get(b"\x01")
    if witness is not None:
        try:
            return TransactionOutput.parse(witness)
        except Exception as exc:
            raise VerificationError(
                f"input {index} witness_utxo is not a transaction output"
            ) from exc
    non_witness = item.get(b"\x00")
    if non_witness is None:
        raise VerificationError(f"input {index} is missing UTXO information")
    vout_raw = item.get(b"\x0f")
    if vout_raw is None:
        raise VerificationError(f"input {index} is missing an output index")
    try:
        tx = Transaction.parse(non_witness)
    except Exception as exc:
        raise VerificationError(
            f"input {index} non_witness_utxo is not a transaction"
        ) from exc
    vout = int.from_bytes(vout_raw, "little")
    if vout >= len(tx.vout):
        raise VerificationError(
            f"input {index} output index {vout} is past the non_witness_utxo outputs"
        )
    return tx.vout[vout]


def _observed_input_type(item, txout) -> str:
    kind = txout.script_pubkey.script_type() if txout.script_pubkey is not None else None
    if kind == "p2tr" and item.get(b"\x20") is not None:
        return "sp-spend"
    if kind == "p2tr":
        return "p2tr"
    if kind == "p2wpkh":
        return "p2wpkh"
    return kind or "unknown"


def _verify_scenario_intent(scenario: Scenario, parsed: PsbtV2) -> None:
    """Bind the final PSBT to the scenario, not to keys the PSBT already carries."""

    if len(parsed.inputs) != len(scenario.inputs):
        raise VerificationError("final PSBT input count does not match the scenario")
    if len(parsed.outputs) != len(scenario.outputs):
        raise VerificationError("final PSBT output count does not match the scenario")
    for index, item in enumerate(scenario.inputs):
        txout = _input_txout(index, parsed.inputs[index])
        expected_type = item.get("type")
        actual = _observed_input_type(parsed.inputs[index], txout)
        if actual != expected_type:
            raise VerificationError(
                f"input {index} script type is {actual}, scenario expects {expected_type}"
            )
        expected_amount = item.get("amount_sat")
        if txout.value != expected_amount:
            raise VerificationError(
                f"input {index} amount {txout.value} does not match "
                f"scenario amount_sat {expected_amount}"
            )
    for index, item in enumerate(scenario.outputs):
        output = parsed.outputs[index]
        amount = int.from_bytes(output.get(b"\x03"), "little")
        expected_amount = item.get("amount_sat")
        if amount != expected_amount:
            raise VerificationError(
                f"output {index} amount {amount} does not match "
                f"scenario amount_sat {expected_amount}"
            )
        output_type = item.get("type")
        if output_type == "silent-payment":
            recipient_id = item.get("recipient_id")
            if not isinstance(recipient_id, str) or not recipient_id:
                raise VerificationError(
                    f"output {index} silent-payment output has no recipient_id"
                )
            expected = recipient_sp_info(recipient_id)
            actual = output.get(b"\x09")
            if actual != expected:
                raise VerificationError(
                    f"output {index} Silent Payment scan key does not match "
                    f"recipient_id {recipient_id!r}"
                )
        elif output_type in {"p2wpkh", "p2tr"}:
            actual = output.get(b"\x04")
            expected_script = expected_plain_output_script(output_type)
            if actual != expected_script:
                raise VerificationError(
                    f"output {index} script does not match the {output_type} "
                    "script the fixture creates"
                )
        else:
            raise VerificationError(
                f"output {index} has unsupported scenario type {output_type!r}"
            )


def verify_bip375_completion(scenario: Scenario, psbt: bytes) -> None:
    """Check a bip375 PSBT against the scenario.

    When the scenario declares inputs and outputs, those are the intent:
    input and output counts, each input's script type and amount, each
    output amount, Silent Payment recipient keys from ``recipient_id``, and
    plain output scripts. A self-consistent PSBT that pays a different
    recipient does not pass. Then, unless ``verification`` is structural,
    checks each Silent Payment output script, per-input ECDH share and DLEQ
    for a multi-owner per-input run, the global ECDH share and DLEQ against
    ``A_sum`` for a single-owner or global run, every P2WPKH partial
    signature (DER plus sighash byte), every taproot key signature, and that
    inputs-modifiable and outputs-modifiable are clear. A run with no Silent
    Payment output is not required to carry a share. This uses embit. It is
    not the Caravan or SPDK check. ``verification: structural`` skips the
    cryptography and only requires a script, a signature field, and clear
    modifiable flags. Callers record that run as ``not-evidence``.
    """

    if declares_intent(scenario):
        _verify_scenario_intent(scenario, parse_psbt(psbt))
    if scenario.verification == "structural":
        _verify_bip375_structural(scenario, psbt)
        return

    from embit.silent_payments import SilentPaymentsPSBT

    parsed = parse_psbt(psbt)
    embit_psbt = SilentPaymentsPSBT.parse(psbt)



    sp_output_indices = [
        index for index, output in enumerate(embit_psbt.outputs) if output.sp_data is not None
    ]
    for index in sp_output_indices:
        expected_script = expected_sp_output_script(scenario, psbt, index)
        actual = parsed.outputs[index].get(b"\x04")
        if actual != expected_script:
            raise VerificationError(
                f"output {index} resolved to an unexpected Silent Payment script"
            )
    for index, output in enumerate(parsed.outputs):
        if output.get(b"\x04") is None:
            raise VerificationError(f"output {index} has an unresolved output script")

    _verify_sp_input_evidence(scenario, embit_psbt, parsed)

    roots = _signer_roots(scenario)
    for index in range(len(parsed.inputs)):
        _verify_input_signature(roots, embit_psbt, parsed, index)

    _check_tx_modifiable(parsed)


def verify_musig2_sp_completion(scenario: Scenario, phase: str, psbt: bytes) -> None:
    """Require each MuSig2-SP round's structural contribution from every participant.

    This does not verify nonces or partial signatures cryptographically --
    aggregating and checking the final signature is silent-pay's own job at
    ``finalize``. It only proves the PSBT records the round is supposed to
    produce actually exist.
    """

    if phase not in {"round1", "round2"}:
        return
    parsed = parse_psbt(psbt)
    required_key_type = _MUSIG2_PUB_NONCE if phase == "round1" else _MUSIG2_PARTIAL_SIG
    field_name = "pub_nonce" if phase == "round1" else "partial_sig"
    participants = len(scenario.signers)
    for index, item in enumerate(parsed.inputs):
        count = sum(1 for entry in item.entries if entry.key_type == required_key_type)
        if count != participants:
            raise VerificationError(
                f"input {index} has {count} MuSig2 {field_name} record(s) after {phase}, "
                f"expected {participants}"
            )
    if phase == "round2":
        for index, output in enumerate(scenario.outputs):
            if output.get("type") != "silent-payment":
                continue
            if parsed.outputs[index].get(b"\x04") is None:
                raise VerificationError(f"output {index} has an unresolved Silent Payment script")
        _check_tx_modifiable(parsed)


def musig2_run_claim(scenario: Scenario) -> dict[str, str]:
    """What a musig2-sp run may claim. Record counts are not evidence."""

    if scenario.suite != "musig2-sp":
        raise ValueError("musig2_run_claim is only for the musig2-sp suite")
    return {
        "verification_scope": "interop-only",
        "reason": "structural-musig2",
        "status": "completed",
    }


def _not_evidence(scenario: Scenario, repairs: Sequence[object]) -> bool:
    """Structural verification and combiner repairs are not a release result."""

    return (
        scenario.verification == "structural"
        or scenario.merge_policy == "combiner"
        or bool(repairs)
    )


def _not_evidence_reason(scenario: Scenario) -> str:
    if scenario.verification == "structural":
        return "structural"
    return "combiner-repairs"


def run_claim(
    scenario: Scenario, *, repairs: Sequence[object] = (), generated: bool,
) -> dict[str, str]:
    """What a completed run may claim. Never ``evidence``.

    ``verification: structural``, ``merge_policy: combiner``, and any recorded
    repair are ``not-evidence`` and are not a pass. MuSig2 record counts stay
    ``interop-only``. A bip375 run that does not declare inputs and outputs is
    ``interop-only`` with reason ``consistency-only``.
    """

    if _not_evidence(scenario, repairs):
        return {
            "verification_scope": "not-evidence",
            "reason": _not_evidence_reason(scenario),
            "status": "completed",
        }
    if scenario.suite == "musig2-sp":
        return musig2_run_claim(scenario)
    if scenario.suite == "bip375" and not declares_intent(scenario):
        return {
            "verification_scope": "interop-only",
            "reason": "consistency-only",
            "status": "completed",
        }
    claim = {"status": "passed" if generated else "completed"}
    if not generated:
        claim["reason"] = (
            "transport and strict merge completed; finalize and verify on-chain"
        )
    return claim


def check_case_status(
    scenario: Scenario, *, generated: bool, repairs: Sequence[object] = (),
) -> str:
    """Status ``check`` may record. Weak modes and MuSig2 are not a pass."""

    return run_claim(scenario, repairs=repairs, generated=generated)["status"]


def external_run_claim(scenario: Scenario) -> dict[str, str]:
    """Scope fields for a run that did not bind scenario intent.

    Empty when a full strict bip375 scenario declares inputs and outputs and
    no repair was recorded. Never ``evidence``.
    """

    return _scope_fields(run_claim(scenario, generated=True))


def _scope_fields(claim: dict[str, str]) -> dict[str, str]:
    return {key: claim[key] for key in ("verification_scope", "reason") if key in claim}


def check_case_reason(
    scenario: Scenario, *, generated: bool, repairs: Sequence[object] = (),
) -> str | None:
    """Reason recorded beside ``check_case_status``."""

    return run_claim(scenario, repairs=repairs, generated=generated).get("reason")
