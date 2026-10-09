"""Deterministic, unsigned BIP-375 fixtures for emulator interoperability."""

from __future__ import annotations

from .errors import ConfigurationError
from .models import Scenario
from .test_seeds import mnemonic_for
from .verification import expected_plain_output_script, known_recipient_ids, recipient_keys

_BIP84_TEST_PATH = (0x80000054, 0x80000001, 0x80000000)
_BIP86_TEST_PATH = (0x80000056, 0x80000001, 0x80000000)
# BIP-376 spend key: one fixed key per account, per both Coldcard's
# (silentpayments.py's validate_silent_payment_inputs) and Jade's
# (wallet.c's wallet_is_expected_sp_spend_path) required shape --
# 352h/coin_type'/account'/0h/0, not varied per UTXO like a receive path.
# Uniqueness across payments to the same spend key comes from each input's
# own sp_tweak, not from deriving a different base key per input.
_BIP352_SPEND_PATH = (0x80000160, 0x80000001, 0x80000000, 0x80000000, 0)
_OUTPUT_TYPES = {"silent-payment", "p2wpkh", "p2tr"}
_SIGHASH_TYPES = {"all": 1, "default": 0}


def _sighash_type(item: dict) -> int:
    sighash = item.get("sighash", "all")
    if sighash not in _SIGHASH_TYPES:
        raise ConfigurationError(f"input sighash must be one of {sorted(_SIGHASH_TYPES)}, got {sighash!r}")
    return _SIGHASH_TYPES[sighash]


def build_bip375_fixture(scenario: Scenario) -> bytes:
    """Create an unresolved, per-input BIP-375 PSBTv2 from scenario intent.

    This deliberately covers the narrow first mixed-device lane: one P2WPKH,
    P2TR (key-path), or BIP-376 sp-spend input per configured owner, and one
    output of type silent-payment, p2wpkh, or p2tr. Every input is derived
    from a published emulator mnemonic and has SIGHASH_ALL (P2WPKH, P2TR, and
    sp-spend) or SIGHASH_DEFAULT set before any device sees it.
    """

    if scenario.suite != "bip375":
        raise ConfigurationError("generated fixtures currently support BIP-375 only")
    if scenario.network not in {"regtest", "localtest"}:
        raise ConfigurationError("generated BIP-375 fixtures currently support regtest only")
    if not scenario.inputs:
        raise ConfigurationError("generated BIP-375 fixture requires at least one input")
    if len(scenario.outputs) != 1 or scenario.outputs[0].get("type") not in _OUTPUT_TYPES:
        raise ConfigurationError(
            "generated BIP-375 fixture requires exactly one output of type "
            "silent-payment, p2wpkh, or p2tr"
        )
    try:
        from embit import bip32, bip39, ec, script
        from embit.hashes import tagged_hash
        from embit.psbt import DerivationPath
        from embit.silent_payments import SilentPaymentData, SilentPaymentsPSBT
        from embit.silent_payments.psbt import SPInputScope, SPOutputScope
        from embit.transaction import TransactionOutput
    except ImportError as exc:
        raise ConfigurationError(
            "BIP-375 fixture generation needs SeedSigner's pinned embit dependency"
        ) from exc

    signers = {item.name: item for item in scenario.signers}
    psbt = SilentPaymentsPSBT.create_v2()
    total = 0
    for index, item in enumerate(scenario.inputs):
        owner = item.get("owner")
        if owner not in signers:
            raise ConfigurationError(f"input {index} names unknown owner {owner!r}")
        input_type = item.get("type")
        if input_type not in {"p2wpkh", "p2tr", "sp-spend"}:
            raise ConfigurationError(
                "generated BIP-375 fixtures currently support P2WPKH, P2TR, and "
                "sp-spend (BIP-376 Silent Payment spend) inputs only"
            )
        amount = item.get("amount_sat")
        if not isinstance(amount, int) or amount <= 0:
            raise ConfigurationError(f"input {index} must have a positive integer amount_sat")
        mnemonic = mnemonic_for(signers[owner].seed_id)
        root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic))
        scope = SPInputScope()
        scope.txid = bytes([index + 1]) * 32
        scope.vout = index
        scope.sequence = 0xFFFFFFFE
        if input_type == "p2wpkh":
            path = (*_BIP84_TEST_PATH, 0, index)
            pubkey = root.derive(path).get_public_key()
            scope.witness_utxo = TransactionOutput(amount, script.p2wpkh(pubkey))
            scope.bip32_derivations[pubkey] = DerivationPath(root.my_fingerprint, path)
            scope.sighash_type = 1
        elif input_type == "p2tr":
            path = (*_BIP86_TEST_PATH, 0, index)
            pubkey = root.derive(path).get_public_key()
            scope.witness_utxo = TransactionOutput(amount, script.p2tr(pubkey))
            scope.taproot_internal_key = pubkey
            scope.taproot_bip32_derivations[pubkey] = (
                [],
                DerivationPath(root.my_fingerprint, path),
            )
            # Default SIGHASH_ALL: real Jade firmware normalizes a taproot
            # input's sighash type to explicit SIGHASH_ALL while signing,
            # which conflicts with a pre-set SIGHASH_DEFAULT under strict
            # merge. A scenario can opt into `sighash: default` anyway, since
            # BIP-375 requires a signer to reject SIGHASH_DEFAULT when SP
            # outputs are present, and that rejection is itself the thing
            # under test.
            scope.sighash_type = _sighash_type(item)
        else:
            # sp-spend (BIP-376): this input is itself a previously-received
            # Silent Payment output. Its output key is the owner's own spend
            # key plus a per-input scalar (sp_tweak) -- not a BIP-341
            # internal-key tweak, so build the script directly rather than via
            # script.p2tr(). The Updater records sp_tweak plus a BIP-32 hint to
            # the untweaked spend key so the owner can recompute and sign it.
            path = _BIP352_SPEND_PATH
            spend_hd = root.derive(path)
            spend_pubkey = spend_hd.get_public_key()
            tweak = tagged_hash("bip375-interop/sp-spend-test-tweak", bytes([index]))
            output_key = spend_hd.key.sp_spend_tweak(tweak)
            scope.witness_utxo = TransactionOutput(
                amount, script.Script(b"\x51\x20" + output_key.xonly())
            )
            scope.sp_tweak = tweak
            scope.sp_spend_bip32_derivations[spend_pubkey.sec()] = DerivationPath(
                root.my_fingerprint, path
            )
            scope.sighash_type = _sighash_type(item)
        psbt.add_input(scope)
        total += amount

    output = scenario.outputs[0]
    output_type = output["type"]
    amount = output.get("amount_sat")
    if not isinstance(amount, int) or amount <= 0 or amount >= total:
        raise ConfigurationError("output amount must be positive and below input total")
    if output_type == "silent-payment":
        recipient_id = output.get("recipient_id")
        try:
            scan, spend = recipient_keys(recipient_id)
        except KeyError:
            raise ConfigurationError(
                "silent-payment output recipient_id must be one of "
                f"{list(known_recipient_ids())}, got {recipient_id!r}"
            ) from None
        destination = SPOutputScope()
        destination.value = amount
        destination.sp_data = SilentPaymentData(
            ec.PublicKey.parse(scan),
            ec.PublicKey.parse(spend),
        )
    else:
        dest_script = script.Script(expected_plain_output_script(output_type))
        destination = SPOutputScope(vout=TransactionOutput(amount, dest_script))
    psbt.add_output(destination)
    return psbt.serialize()
