"""Explicit evidence checks for completed interoperability runs.

These checks read the PSBT itself for outpoints, recipient keys, and
derivation paths, and match each input's BIP32 fingerprint against the
harness's own published test seeds, rather than trusting that a signer or
resolver merely touched every required PSBT field, or leaning on a fixture
generator's own generation-time conventions.
"""

from __future__ import annotations

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
_MUSIG2_PUB_NONCE = 0x1B
_MUSIG2_PARTIAL_SIG = 0x1C


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
    from embit.silent_payments.sp import (
        derive_sp_outputs,
        get_eligible_inputs,
        group_sp_outputs_by_scan_key,
    )

    embit_psbt = SilentPaymentsPSBT.parse(psbt)
    if output_index >= len(embit_psbt.outputs) or embit_psbt.outputs[output_index].sp_data is None:
        raise VerificationError(
            f"output {output_index} has no Silent Payment recipient information"
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

    outpoints = [inp.vin for inp in embit_psbt.inputs]
    derivation = derive_sp_outputs(priv_keys, outpoints, scan_spend_groups)
    if derivation is None:
        raise VerificationError("scenario input private keys sum to zero")
    _, _, results = derivation

    target_scan = embit_psbt.outputs[output_index].sp_data.scan_key.sec()
    _, outputs = results[target_scan]
    position = output_order[target_scan].index(output_index)
    return b"\x51\x20" + outputs[position]


def _verify_sp_input_evidence(scenario: Scenario, embit_psbt, parsed: PsbtV2) -> None:
    """Require a per-input ECDH share and DLEQ proof for every eligible input.

    Only applies to a multi-owner, per-input contribution scenario: a
    single-owner run resolves via a global share instead (embit's own
    single-signer path), which is out of scope here.
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
        return

    entry = next((entry for entry in entries if entry.key_type == _TAP_KEY_SIGNATURE), None)
    if entry is None:
        raise VerificationError(f"input {index} is missing a taproot key signature")
    sig = entry.value
    if len(sig) == 65:
        if sig[64] != 1:
            raise VerificationError(f"input {index} taproot signature has an unexpected sighash byte")
        sig = sig[:64]
    elif len(sig) != 64:
        raise VerificationError(f"input {index} taproot signature has an invalid length")

    utxo = inp.utxo
    if utxo is None:
        raise VerificationError(f"input {index} is missing UTXO information")
    output_xonly = utxo.script_pubkey.data[2:34]
    pubkey = ec.PublicKey.parse(b"\x02" + output_xonly)
    msg_hash = embit_psbt.sighash(index, sighash=1)
    if not pubkey.schnorr_verify(ec.SchnorrSig(sig), msg_hash):
        raise VerificationError(
            f"input {index} taproot signature does not verify against the owner's output key"
        )


def _verify_bip375_structural(scenario: Scenario, psbt: bytes) -> None:
    """Weak fallback check: every output has a script, every input a signature key type.

    Does not recompute anything or verify any cryptography. Opt in with the
    scenario's ``verification: structural`` field only for the rare case
    that the full check in :func:`verify_bip375_completion` cannot apply.
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


def verify_bip375_completion(scenario: Scenario, psbt: bytes) -> None:
    """Independently recompute and check every generated input and output.

    Recomputes each Silent Payment output script from the PSBT's own
    recipient information and each input's own derivation fields, and
    verifies every signature and DLEQ proof cryptographically, so a resolver
    that returns a wrong script or a signature from the wrong key fails here
    rather than resting entirely on Coldcard/Jade's own validation of each
    other. A scenario may opt into :func:`_verify_bip375_structural` instead
    via ``verification: structural`` for the rare case this cannot apply.
    """

    if scenario.verification == "structural":
        _verify_bip375_structural(scenario, psbt)
        return

    from embit.silent_payments import SilentPaymentsPSBT

    parsed = parse_psbt(psbt)
    if len(parsed.inputs) != len(scenario.inputs):
        raise VerificationError("final PSBT input count does not match the scenario")

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
