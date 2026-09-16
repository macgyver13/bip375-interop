"""Deterministic, unsigned BIP-375 fixtures for emulator interoperability."""

from __future__ import annotations

from binascii import unhexlify

from .errors import ConfigurationError
from .models import Scenario
from .test_seeds import mnemonic_for


_SCAN_HEX = "027a487fc19fb769877b8742d6ea18118f3c4e72b1ea8c6de602a7ad4a41dbe068"
_SPEND_HEX = "0361e1b1e9de5e42cb2007f7ca54b9e0d57ed13938fad56d3f19e57513a8fce039"
_BIP84_TEST_PATH = (0x80000054, 0x80000001, 0x80000000)
_BIP86_TEST_PATH = (0x80000056, 0x80000001, 0x80000000)


def build_bip375_fixture(scenario: Scenario) -> bytes:
    """Create an unresolved, per-input BIP-375 PSBTv2 from scenario intent.

    This deliberately covers the narrow first mixed-device lane: one P2WPKH or
    P2TR (key-path) input per configured owner and one Silent Payment output.
    Every input is derived from a published emulator mnemonic and has
    SIGHASH_ALL (P2WPKH) or SIGHASH_DEFAULT (P2TR) set before any device sees
    it.
    """

    if scenario.suite != "bip375":
        raise ConfigurationError("generated fixtures currently support BIP-375 only")
    if scenario.network not in {"regtest", "localtest"}:
        raise ConfigurationError("generated BIP-375 fixtures currently support regtest only")
    if not scenario.inputs:
        raise ConfigurationError("generated BIP-375 fixture requires at least one input")
    silent_outputs = [item for item in scenario.outputs if item.get("type") == "silent-payment"]
    if len(silent_outputs) != 1 or len(silent_outputs) != len(scenario.outputs):
        raise ConfigurationError("generated BIP-375 fixture requires exactly one Silent Payment output")
    try:
        from embit import bip32, bip39, ec, script
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
        if input_type not in {"p2wpkh", "p2tr"}:
            raise ConfigurationError(
                "generated BIP-375 fixtures currently support P2WPKH and P2TR inputs only"
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
        else:
            path = (*_BIP86_TEST_PATH, 0, index)
            pubkey = root.derive(path).get_public_key()
            scope.witness_utxo = TransactionOutput(amount, script.p2tr(pubkey))
            scope.taproot_internal_key = pubkey
            scope.taproot_bip32_derivations[pubkey] = (
                [],
                DerivationPath(root.my_fingerprint, path),
            )
            # SIGHASH_ALL, not SIGHASH_DEFAULT: real Jade firmware normalizes a
            # taproot input's sighash type to explicit SIGHASH_ALL while signing,
            # which conflicts with a pre-set SIGHASH_DEFAULT under strict merge.
            scope.sighash_type = 1
        psbt.add_input(scope)
        total += amount

    output = silent_outputs[0]
    amount = output.get("amount_sat")
    if not isinstance(amount, int) or amount <= 0 or amount >= total:
        raise ConfigurationError("Silent Payment output amount must be positive and below input total")
    destination = SPOutputScope()
    destination.value = amount
    destination.sp_data = SilentPaymentData(
        ec.PublicKey.parse(unhexlify(_SCAN_HEX)),
        ec.PublicKey.parse(unhexlify(_SPEND_HEX)),
    )
    psbt.add_output(destination)
    return psbt.serialize()
