from binascii import unhexlify

import pytest

from bip375_interop.fixtures import build_bip375_fixture
from bip375_interop.models import Scenario
from bip375_interop.psbt_maps import PsbtEntry, PsbtMap, PsbtV2, parse_psbt
from bip375_interop.test_seeds import mnemonic_for
from bip375_interop.verification import (
    VerificationError,
    check_case_reason,
    check_case_status,
    expected_sp_output_script,
    musig2_run_claim,
    verify_bip375_completion,
    verify_musig2_sp_completion,
)


def _single_owner_scenario(*, verification: str = "full") -> Scenario:
    return Scenario.from_dict({
        "name": "case", "suite": "bip375", "network": "regtest",
        "signers": [{"name": "a", "backend": "coldcard", "seed_id": "test-a"}],
        "inputs": [
            {"owner": "a", "type": "p2wpkh", "amount_sat": 10_000},
            {"owner": "a", "type": "p2tr", "amount_sat": 20_000},
            {"owner": "a", "type": "sp-spend", "amount_sat": 30_000},
        ],
        "outputs": [{"type": "silent-payment", "amount_sat": 55_000}],
        "verification": verification,
    })


def _fully_signed_single_owner(scenario: Scenario) -> bytes:
    from embit import bip32, bip39
    from embit.psbt import SIGHASH, derive_hdkey
    from embit.silent_payments import SilentPaymentsPSBT
    from embit.silent_payments.signing import match_sp_spend_base

    raw = build_bip375_fixture(scenario)
    psbt = SilentPaymentsPSBT.parse(raw)
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-a")))
    psbt.sign_with(root, sighash=SIGHASH.ALL)
    base = match_sp_spend_base(psbt.inputs[2], root, root.my_fingerprint, derive_hdkey)
    psbt.sign_input_with_sp_tweak(base, 2, sighash=SIGHASH.ALL)
    return psbt.serialize()


def test_completion_check_rejects_unresolved_psbt():
    scenario = _single_owner_scenario()
    with pytest.raises(VerificationError, match="unexpected Silent Payment script"):
        verify_bip375_completion(scenario, build_bip375_fixture(scenario))


def test_completion_check_accepts_fully_signed_psbt():
    scenario = _single_owner_scenario()
    verify_bip375_completion(scenario, _fully_signed_single_owner(scenario))


def test_completion_check_accepts_sp_spend_signed_with_sighash_default():
    """SIGHASH_DEFAULT and SIGHASH_ALL are functionally identical for taproot.

    embit's own sp-spend signing path always uses SIGHASH_DEFAULT regardless
    of the PSBT's declared sighash_type (a real gap found while running
    bip376-seedsigner-* scenarios); the completion check must not treat that
    as an invalid signature.
    """
    from embit import bip32, bip39
    from embit.psbt import SIGHASH, derive_hdkey
    from embit.silent_payments import SilentPaymentsPSBT
    from embit.silent_payments.signing import match_sp_spend_base

    scenario = _single_owner_scenario()
    raw = build_bip375_fixture(scenario)
    psbt = SilentPaymentsPSBT.parse(raw)
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-a")))
    psbt.sign_with(root, sighash=SIGHASH.ALL)
    base = match_sp_spend_base(psbt.inputs[2], root, root.my_fingerprint, derive_hdkey)
    psbt.sign_input_with_sp_tweak(base, 2)  # defaults to SIGHASH.DEFAULT, no trailing byte

    verify_bip375_completion(scenario, psbt.serialize())


def test_completion_check_rejects_leftover_inputs_modifiable():
    scenario = _single_owner_scenario()
    final_raw = _fully_signed_single_owner(scenario)
    parsed = parse_psbt(final_raw)
    globals_map = PsbtMap(tuple(
        entry if entry.key_type != 0x06 else PsbtEntry(entry.key, b"\x01")
        for entry in parsed.globals.entries
    ))
    tampered = PsbtV2(globals_map, parsed.inputs, parsed.outputs).serialize()
    with pytest.raises(VerificationError, match="inputs-modifiable"):
        verify_bip375_completion(scenario, tampered)


def test_completion_check_rejects_leftover_outputs_modifiable():
    scenario = _single_owner_scenario()
    final_raw = _fully_signed_single_owner(scenario)
    parsed = parse_psbt(final_raw)
    globals_map = PsbtMap(tuple(
        entry if entry.key_type != 0x06 else PsbtEntry(entry.key, b"\x02")
        for entry in parsed.globals.entries
    ))
    tampered = PsbtV2(globals_map, parsed.inputs, parsed.outputs).serialize()
    with pytest.raises(VerificationError, match="outputs-modifiable"):
        verify_bip375_completion(scenario, tampered)


def test_completion_check_rejects_tampered_sighash_byte():
    """A trailing sighash byte that doesn't match what was actually signed must fail."""
    scenario = _single_owner_scenario()
    final_raw = _fully_signed_single_owner(scenario)
    parsed = parse_psbt(final_raw)
    inputs = list(parsed.inputs)
    inputs[2] = PsbtMap(tuple(
        entry if entry.key_type != 0x13 else PsbtEntry(entry.key, entry.value[:64] + b"\x02")
        for entry in inputs[2].entries
    ))
    tampered = PsbtV2(parsed.globals, tuple(inputs), parsed.outputs).serialize()
    with pytest.raises(VerificationError, match="does not verify against the owner's output key"):
        verify_bip375_completion(scenario, tampered)


def test_completion_check_rejects_wrong_resolved_script():
    scenario = _single_owner_scenario()
    final_raw = _fully_signed_single_owner(scenario)
    parsed = parse_psbt(final_raw)
    tampered_output = PsbtMap(tuple(
        entry if entry.key_type != 0x04 else PsbtEntry(entry.key, b"\x51\x20" + b"\x00" * 32)
        for entry in parsed.outputs[0].entries
    ))
    tampered = PsbtV2(parsed.globals, parsed.inputs, (tampered_output,)).serialize()
    with pytest.raises(VerificationError, match="output 0 resolved to an unexpected Silent Payment script"):
        verify_bip375_completion(scenario, tampered)


def test_completion_check_rejects_signature_from_wrong_key():
    from embit import ec

    scenario = _single_owner_scenario()
    final_raw = _fully_signed_single_owner(scenario)
    parsed = parse_psbt(final_raw)
    inputs = list(parsed.inputs)
    # A taproot signature that verifies fine, just against the wrong input's key.
    foreign_sig = next(entry.value for entry in inputs[2].entries if entry.key_type == 0x13)
    inputs[1] = PsbtMap(tuple(
        entry if entry.key_type != 0x13 else PsbtEntry(entry.key, foreign_sig)
        for entry in inputs[1].entries
    ))
    tampered = PsbtV2(parsed.globals, tuple(inputs), parsed.outputs).serialize()
    with pytest.raises(VerificationError, match="does not verify against the owner's output key"):
        verify_bip375_completion(scenario, tampered)


def test_completion_check_rejects_tampered_p2wpkh_signature():
    scenario = _single_owner_scenario()
    final_raw = _fully_signed_single_owner(scenario)
    parsed = parse_psbt(final_raw)
    inputs = list(parsed.inputs)
    sig = next(entry for entry in inputs[0].entries if entry.key_type == 0x02)
    flipped = sig.value[:-2] + bytes([sig.value[-2] ^ 0x01]) + sig.value[-1:]
    inputs[0] = PsbtMap(tuple(
        entry if entry.key_type != 0x02 else PsbtEntry(entry.key, flipped)
        for entry in inputs[0].entries
    ))
    tampered = PsbtV2(parsed.globals, tuple(inputs), parsed.outputs).serialize()
    with pytest.raises(VerificationError, match="partial signature does not verify"):
        verify_bip375_completion(scenario, tampered)


def _replace_global(parsed: PsbtV2, key_type: int, value: bytes | None) -> bytes:
    entries = []
    for entry in parsed.globals.entries:
        if entry.key_type != key_type:
            entries.append(entry)
        elif value is not None:
            entries.append(PsbtEntry(entry.key, value))
    return PsbtV2(PsbtMap(tuple(entries)), parsed.inputs, parsed.outputs).serialize()


def test_completion_check_rejects_missing_global_dleq():
    scenario = _single_owner_scenario()
    parsed = parse_psbt(_fully_signed_single_owner(scenario))
    with pytest.raises(VerificationError, match="missing a global Silent Payment DLEQ proof"):
        verify_bip375_completion(scenario, _replace_global(parsed, 0x08, None))


def test_completion_check_rejects_wrong_global_share():
    scenario = _single_owner_scenario()
    parsed = parse_psbt(_fully_signed_single_owner(scenario))
    share = next(entry.value for entry in parsed.globals.entries if entry.key_type == 0x07)
    flipped = bytes([share[0] ^ 0x01]) + share[1:]
    with pytest.raises(VerificationError, match="does not match the derived share"):
        verify_bip375_completion(scenario, _replace_global(parsed, 0x07, flipped))


def test_completion_check_rejects_wrong_global_dleq():
    scenario = _single_owner_scenario()
    parsed = parse_psbt(_fully_signed_single_owner(scenario))
    proof = next(entry.value for entry in parsed.globals.entries if entry.key_type == 0x08)
    flipped = bytes([proof[0] ^ 0x01]) + proof[1:]
    with pytest.raises(VerificationError, match="DLEQ proof does not verify"):
        verify_bip375_completion(scenario, _replace_global(parsed, 0x08, flipped))


def test_plain_output_does_not_require_a_global_share():
    """No Silent Payment output is the mode that legitimately has no share record."""
    from embit import bip32, bip39
    from embit.psbt import SIGHASH
    from embit.silent_payments import SilentPaymentsPSBT

    scenario = Scenario.from_dict({
        "name": "plain",
        "suite": "bip375",
        "network": "regtest",
        "signers": [{"name": "a", "backend": "coldcard", "seed_id": "test-a"}],
        "inputs": [{"owner": "a", "type": "p2wpkh", "amount_sat": 10_000}],
        "outputs": [{"type": "p2wpkh", "amount_sat": 9_000}],
    })
    psbt = SilentPaymentsPSBT.parse(build_bip375_fixture(scenario))
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-a")))
    psbt.sign_with(root, sighash=SIGHASH.ALL)
    verify_bip375_completion(scenario, psbt.serialize())


def test_completion_check_rejects_noop_signer():
    """A resolver that resolves the output but a signer that adds nothing must fail."""
    scenario = _single_owner_scenario()
    raw = build_bip375_fixture(scenario)

    from embit import bip32, bip39
    from embit.silent_payments import SilentPaymentsPSBT

    psbt = SilentPaymentsPSBT.parse(raw)
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-a")))
    psbt.derive_sp_outputs(root)  # resolves the output; adds no signatures at all
    final_raw = psbt.serialize()
    with pytest.raises(VerificationError, match="input 0 is missing a partial signature"):
        verify_bip375_completion(scenario, final_raw)


def test_structural_verification_skips_cryptographic_checks():
    scenario = _single_owner_scenario(verification="structural")
    raw = build_bip375_fixture(scenario)
    parsed = parse_psbt(raw)
    inputs = list(parsed.inputs)
    # Present-but-bogus signature fields: the full check would reject this,
    # the structural opt-out only requires the key types to be present.
    inputs[0] = PsbtMap(inputs[0].entries + (PsbtEntry(bytes([0x02]) + b"\x02" * 33, b"bogus"),))
    inputs[1] = PsbtMap(inputs[1].entries + (PsbtEntry(bytes([0x13]), b"\x00" * 64),))
    inputs[2] = PsbtMap(inputs[2].entries + (PsbtEntry(bytes([0x13]), b"\x00" * 64),))
    outputs = (PsbtMap(tuple(
        entry if entry.key_type != 0x09 else entry for entry in parsed.outputs[0].entries
    ) + (PsbtEntry(bytes([0x04]), b"\x51\x20" + b"\x00" * 32),)),)
    bogus = PsbtV2(_with_tx_modifiable_cleared(parsed.globals), tuple(inputs), outputs).serialize()
    verify_bip375_completion(scenario, bogus)


def test_expected_sp_output_script_matches_independent_derivation():
    from embit import ec
    from embit.psbt import derive_hdkey
    from embit.silent_payments import SilentPaymentsPSBT
    from embit.silent_payments.sp import derive_sp_outputs
    from embit.silent_payments.signing import resolve_input_privkey

    scenario = Scenario.from_dict({
        "name": "case", "suite": "bip375", "network": "regtest",
        "signers": [
            {"name": "a", "backend": "coldcard", "seed_id": "test-a"},
            {"name": "b", "backend": "jade", "seed_id": "test-b"},
        ],
        "inputs": [
            {"owner": "a", "type": "p2wpkh", "amount_sat": 10_000},
            {"owner": "b", "type": "p2tr", "amount_sat": 20_000},
        ],
        "outputs": [{"type": "silent-payment", "amount_sat": 25_000}],
    })
    raw = build_bip375_fixture(scenario)
    epsbt = SilentPaymentsPSBT.parse(raw)

    from embit import bip32, bip39
    root_a = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-a")))
    root_b = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-b")))
    priv_a = resolve_input_privkey(epsbt.inputs[0], root_a, root_a.my_fingerprint, derive_hdkey)
    priv_b = resolve_input_privkey(epsbt.inputs[1], root_b, root_b.my_fingerprint, derive_hdkey)
    scan_key = epsbt.outputs[0].sp_data.scan_key
    spend_key = epsbt.outputs[0].sp_data.spend_key
    outpoints = [inp.vin for inp in epsbt.inputs]
    _, _, results = derive_sp_outputs(
        [priv_a, priv_b], outpoints, {scan_key.sec(): (scan_key, [spend_key])}
    )
    _, outputs = results[scan_key.sec()]
    assert expected_sp_output_script(scenario, raw, 0) == b"\x51\x20" + outputs[0]


def _with_tx_modifiable_cleared(globals_map: PsbtMap) -> PsbtMap:
    """A real resolver clears inputs/outputs-modifiable once a PSBT is complete."""
    return PsbtMap(tuple(
        entry if entry.key_type != 0x06 else PsbtEntry(entry.key, b"\x00")
        for entry in globals_map.entries
    ))


def _per_input_scenario() -> Scenario:
    return Scenario.from_dict({
        "name": "case", "suite": "bip375", "network": "regtest",
        "signers": [
            {"name": "a", "backend": "coldcard", "seed_id": "test-a"},
            {"name": "b", "backend": "jade", "seed_id": "test-b"},
        ],
        "inputs": [
            {"owner": "a", "type": "p2wpkh", "amount_sat": 10_000},
            {"owner": "b", "type": "p2tr", "amount_sat": 20_000},
        ],
        "outputs": [{"type": "silent-payment", "amount_sat": 25_000}],
    })


def _build_per_input_flow(scenario: Scenario) -> tuple[bytes, bytes]:
    """Return (contributed-only, fully-resolved-and-signed) PSBTs.

    Mirrors what a contribute/resolve-sign/sign round dance produces, since
    embit itself only implements the single-signer path natively.
    """
    from embit import bip32, bip39, ec
    from embit.psbt import derive_hdkey
    from embit.silent_payments import SilentPaymentsPSBT
    from embit.silent_payments.dleq import generate_dleq_proof
    from embit.silent_payments.signing import resolve_input_privkey
    from embit.silent_payments.sp import _tweak_mul
    from embit.misc import urandom

    raw = build_bip375_fixture(scenario)
    root_a = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-a")))
    root_b = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic_for("test-b")))
    epsbt = SilentPaymentsPSBT.parse(raw)
    priv_a = resolve_input_privkey(epsbt.inputs[0], root_a, root_a.my_fingerprint, derive_hdkey)
    priv_b = resolve_input_privkey(epsbt.inputs[1], root_b, root_b.my_fingerprint, derive_hdkey)
    scan_key = epsbt.outputs[0].sp_data.scan_key
    scan_key_data = scan_key.sec()
    share_a = _tweak_mul(scan_key.sec(), priv_a)
    share_b = _tweak_mul(scan_key.sec(), priv_b)
    proof_a = generate_dleq_proof(priv_a, scan_key_data, r=urandom(32))
    proof_b = generate_dleq_proof(priv_b, scan_key_data, r=urandom(32))

    parsed = parse_psbt(raw)
    inputs = list(parsed.inputs)
    inputs[0] = PsbtMap(inputs[0].entries + (
        PsbtEntry(bytes([0x1D]) + scan_key_data, share_a),
        PsbtEntry(bytes([0x1E]) + scan_key_data, proof_a),
    ))
    inputs[1] = PsbtMap(inputs[1].entries + (
        PsbtEntry(bytes([0x1D]) + scan_key_data, share_b),
        PsbtEntry(bytes([0x1E]) + scan_key_data, proof_b),
    ))
    contributed = PsbtV2(parsed.globals, tuple(inputs), parsed.outputs).serialize()

    expected_script = expected_sp_output_script(scenario, contributed, 0)
    parsed2 = parse_psbt(contributed)
    resolved_output = PsbtMap(parsed2.outputs[0].entries + (PsbtEntry(bytes([0x04]), expected_script),))
    resolved = PsbtV2(parsed2.globals, parsed2.inputs, (resolved_output,)).serialize()

    eresolved = SilentPaymentsPSBT.parse(resolved)
    sig_b = ec.PrivateKey(priv_b).schnorr_sign(eresolved.sighash(1, sighash=1)).serialize() + b"\x01"
    parsed3 = parse_psbt(resolved)
    inputs3 = list(parsed3.inputs)
    inputs3[1] = PsbtMap(inputs3[1].entries + (PsbtEntry(bytes([0x13]), sig_b),))
    after_b = PsbtV2(parsed3.globals, tuple(inputs3), parsed3.outputs).serialize()

    expected_pub_a = ec.PrivateKey(priv_a).get_public_key().sec()
    signed_a = SilentPaymentsPSBT.parse(after_b)
    sig_a = ec.PrivateKey(priv_a).sign(signed_a.sighash(0, sighash=1)).serialize() + b"\x01"
    parsed4 = parse_psbt(after_b)
    inputs4 = list(parsed4.inputs)
    inputs4[0] = PsbtMap(inputs4[0].entries + (PsbtEntry(bytes([0x02]) + expected_pub_a, sig_a),))
    final_raw = PsbtV2(
        _with_tx_modifiable_cleared(parsed4.globals), tuple(inputs4), parsed4.outputs
    ).serialize()
    return contributed, final_raw


def test_multi_owner_per_input_flow_verifies():
    scenario = _per_input_scenario()
    _, final_raw = _build_per_input_flow(scenario)
    verify_bip375_completion(scenario, final_raw)


def test_multi_owner_rejects_global_share():
    scenario = _per_input_scenario()
    contributed, final_raw = _build_per_input_flow(scenario)
    epsbt = __import__(
        "embit.silent_payments", fromlist=["SilentPaymentsPSBT"]
    ).SilentPaymentsPSBT.parse(contributed)
    scan_key_data = epsbt.outputs[0].sp_data.scan_key.sec()
    parsed = parse_psbt(final_raw)
    globals_with_global_share = PsbtMap(parsed.globals.entries + (
        PsbtEntry(bytes([0x07]) + scan_key_data, b"\x02" + b"\x00" * 32),
    ))
    tampered = PsbtV2(globals_with_global_share, parsed.inputs, parsed.outputs).serialize()
    with pytest.raises(VerificationError, match="global Silent Payment ECDH share"):
        verify_bip375_completion(scenario, tampered)


def test_multi_owner_rejects_bad_dleq_proof():
    scenario = _per_input_scenario()
    contributed, final_raw = _build_per_input_flow(scenario)
    parsed = parse_psbt(final_raw)
    inputs = list(parsed.inputs)
    bad_proof = b"\x00" * 64
    inputs[0] = PsbtMap(tuple(
        entry if entry.key_type != 0x1E else PsbtEntry(entry.key, bad_proof)
        for entry in inputs[0].entries
    ))
    tampered = PsbtV2(parsed.globals, tuple(inputs), parsed.outputs).serialize()
    with pytest.raises(VerificationError, match="DLEQ proof does not verify"):
        verify_bip375_completion(scenario, tampered)


def _musig2_scenario() -> Scenario:
    return Scenario.from_dict({
        "name": "case", "suite": "musig2-sp", "network": "regtest",
        "signers": [
            {"name": "a", "backend": "coldcard", "seed_id": "test-a"},
            {"name": "b", "backend": "jade", "seed_id": "test-b"},
        ],
        "inputs": [{"owner": "a", "type": "p2tr", "amount_sat": 50_000}],
        "outputs": [{"type": "silent-payment", "amount_sat": 49_000}],
    })


def _musig2_psbt(input_entries: list[tuple[bytes, bytes]], resolved: bool) -> PsbtV2:
    globals_map = PsbtMap((
        PsbtEntry(b"\xfb", (2).to_bytes(4, "little")),
        PsbtEntry(b"\x02", (2).to_bytes(4, "little")),
        PsbtEntry(b"\x04", b"\x01"),
        PsbtEntry(b"\x05", b"\x01"),
    ))
    input_map = PsbtMap((
        PsbtEntry(b"\x0e", b"\x11" * 32), PsbtEntry(b"\x0f", b"\x00" * 4),
        *(PsbtEntry(key, value) for key, value in input_entries),
    ))
    output_entries = [PsbtEntry(b"\x03", (49_000).to_bytes(8, "little"))]
    if resolved:
        output_entries.append(PsbtEntry(b"\x04", b"\x51\x20" + b"\x22" * 32))
    else:
        output_entries.append(PsbtEntry(b"\x09", b"\x00" * 66))
    output_map = PsbtMap(tuple(output_entries))
    return PsbtV2(globals_map, (input_map,), (output_map,))


def test_musig2_completion_requires_one_nonce_per_participant():
    scenario = _musig2_scenario()
    psbt = _musig2_psbt([(b"\x1b" + b"\x00" * 33, b"nonce-a")], resolved=False)
    with pytest.raises(VerificationError, match="1 MuSig2 pub_nonce record"):
        verify_musig2_sp_completion(scenario, "round1", psbt.serialize())


def test_musig2_completion_accepts_one_nonce_per_participant():
    scenario = _musig2_scenario()
    psbt = _musig2_psbt(
        [(b"\x1b" + b"\x00" * 33, b"nonce-a"), (b"\x1b" + b"\x01" * 33, b"nonce-b")],
        resolved=False,
    )
    verify_musig2_sp_completion(scenario, "round1", psbt.serialize())


def test_musig2_completion_requires_resolved_sp_output_after_round2():
    scenario = _musig2_scenario()
    psbt = _musig2_psbt(
        [(b"\x1c" + b"\x00" * 33, b"sig-a"), (b"\x1c" + b"\x01" * 33, b"sig-b")],
        resolved=False,
    )
    with pytest.raises(VerificationError, match="unresolved Silent Payment script"):
        verify_musig2_sp_completion(scenario, "round2", psbt.serialize())


def test_musig2_completion_accepts_resolved_round2_without_tx_modifiable():
    scenario = _musig2_scenario()
    psbt = _musig2_psbt(
        [(b"\x1c" + b"\x00" * 33, b"sig-a"), (b"\x1c" + b"\x01" * 33, b"sig-b")],
        resolved=True,
    )
    verify_musig2_sp_completion(scenario, "round2", psbt.serialize())


def test_musig2_completion_rejects_leftover_outputs_modifiable_after_round2():
    scenario = _musig2_scenario()
    psbt = _musig2_psbt(
        [(b"\x1c" + b"\x00" * 33, b"sig-a"), (b"\x1c" + b"\x01" * 33, b"sig-b")],
        resolved=True,
    )
    globals_map = PsbtMap(psbt.globals.entries + (PsbtEntry(b"\x06", b"\x02"),))
    tampered = PsbtV2(globals_map, psbt.inputs, psbt.outputs)
    with pytest.raises(VerificationError, match="outputs-modifiable"):
        verify_musig2_sp_completion(scenario, "round2", tampered.serialize())


def test_musig2_record_match_is_not_a_pass():
    from bip375_interop.cli import _verification_fields

    scenario = _musig2_scenario()
    psbt = _musig2_psbt(
        [(b"\x1c" + b"\x00" * 33, b"sig-a"), (b"\x1c" + b"\x01" * 33, b"sig-b")],
        resolved=True,
    )
    verify_musig2_sp_completion(scenario, "round2", psbt.serialize())
    claim = musig2_run_claim(scenario)
    assert claim == {
        "verification_scope": "interop-only",
        "reason": "structural-musig2",
        "status": "completed",
    }
    assert claim["verification_scope"] != "evidence"
    assert check_case_status(scenario, generated=True) == "completed"
    assert check_case_status(scenario, generated=False) == "completed"
    assert check_case_reason(scenario, generated=False) == "structural-musig2"
    assert check_case_reason(scenario, generated=True) == "structural-musig2"
    assert _verification_fields(scenario) == {
        "verification_scope": "interop-only",
        "reason": "structural-musig2",
    }
