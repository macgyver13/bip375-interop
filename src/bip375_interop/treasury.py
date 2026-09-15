"""Derive a silent-pay MuSig2 treasury wallet from the harness's own signers.

silent-pay's ``TreasuryWalletConfig`` is entirely driven by a single BIP-380
descriptor string (see its ``wallet.rs``): there is no separate signer-list
input, and the key architecture is inferred purely from the descriptor's
shape. This module builds that descriptor -- and the wallet TOML file around
it -- directly from the harness's own published test seeds, rather than from
an externally sourced wallet file.

Both shapes silent-pay recognizes are supported, matching its
``descriptor_from_signers`` / ``parse_descriptor_signers`` pair byte for byte:

- aggregate-then-derive: ``tr(musig([xfp/48h/1h/0h/3h]xpub,...)/<0;1>/*)``.
  The multipath step applies to the MuSig2 aggregate (BIP-328 synthetic
  derivation). This is what Coldcard and Jade firmware have long used.
- derive-then-aggregate: ``tr(musig([xfp/48h/1h/0h/3h]xpub/<0;1>/*,...))``.
  The multipath step moves onto each participant (BIP-390 ranged
  participants), so the bare aggregate of already-derived keys is the taproot
  internal key. Jade supports this as of its ``32a020c3``; BitSaga rejects it.
"""

from __future__ import annotations

from typing import Sequence

from .errors import ConfigurationError
from .suites import KeyArchitecture
from .test_seeds import mnemonic_for


# BIP-48 script-type-3 (multisig) account path: m/48'/1'/0'/3'. Matches the
# path silent-pay's own demo fixtures (`sp_demo::operations::COSIGNERS_TEST`)
# and Coldcard/Jade MuSig2 firmware use.
ACCOUNT_PATH = (0x80000030, 0x80000001, 0x80000000, 0x80000003)
ACCOUNT_PATH_STR = "48h/1h/0h/3h"

# BIP-389 multipath receive/change step. Mirrors silent-pay's own
# `MULTIPATH_SUFFIX` in wallet.rs; the architecture is decided entirely by
# where this lands, so the two must stay identical.
MULTIPATH_SUFFIX = "/<0;1>/*"


def derive_signer_xpub(seed_id: str, *, network: str = "testnet") -> tuple[str, str]:
    """Derive a signer's master fingerprint and account-level xpub.

    Returns ``(xfp_hex, xpub_str)`` for the harness's published test seed
    ``seed_id``, at :data:`ACCOUNT_PATH`.
    """

    try:
        from embit import bip32, bip39, networks
    except ImportError as exc:
        raise ConfigurationError(
            "treasury wallet derivation needs SeedSigner's pinned embit dependency"
        ) from exc

    version = networks.NETWORKS[_embit_network_name(network)]
    mnemonic = mnemonic_for(seed_id)
    root = bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic), version=version["xprv"])
    account = root.derive(list(ACCOUNT_PATH))
    xpub = account.to_public().to_base58(version=version["xpub"])
    return root.my_fingerprint.hex(), xpub


def build_treasury_descriptor(
    seed_ids: Sequence[str],
    *,
    network: str = "testnet",
    key_architecture: KeyArchitecture | str = KeyArchitecture.AGGREGATE_THEN_DERIVE,
) -> str:
    """Build the MuSig2 treasury descriptor for these signers.

    ``key_architecture`` decides where the multipath step lands, which is the
    only thing that distinguishes the two shapes silent-pay parses.
    """

    architecture = _parse_key_architecture(key_architecture)
    if len(seed_ids) < 2:
        raise ConfigurationError("a MuSig2 treasury descriptor requires at least two signers")
    parts = []
    for seed_id in seed_ids:
        xfp_hex, xpub = derive_signer_xpub(seed_id, network=network)
        key = f"[{xfp_hex}/{ACCOUNT_PATH_STR}]{xpub}"
        if architecture is KeyArchitecture.DERIVE_THEN_AGGREGATE:
            key += MULTIPATH_SUFFIX
        parts.append(key)
    joined = ",".join(parts)
    if architecture is KeyArchitecture.DERIVE_THEN_AGGREGATE:
        return f"tr(musig({joined}))"
    return f"tr(musig({joined}){MULTIPATH_SUFFIX})"


def build_wallet_toml(
    seed_ids: Sequence[str],
    *,
    network: str = "testnet",
    last_derivation_index: int = 0,
    change_derivation_index: int = 0,
    key_architecture: KeyArchitecture | str = KeyArchitecture.AGGREGATE_THEN_DERIVE,
) -> str:
    """Render a silent-pay ``TreasuryWalletConfig`` TOML file for these signers."""

    descriptor = build_treasury_descriptor(
        seed_ids, network=network, key_architecture=key_architecture
    )
    return (
        f'network = "{network}"\n'
        f'descriptor = "{descriptor}"\n'
        f"last_derivation_index = {last_derivation_index}\n"
        f"change_derivation_index = {change_derivation_index}\n"
    )


_EMBIT_NETWORK_NAMES = {
    "mainnet": "main",
    "testnet": "test",
    "signet": "signet",
    "regtest": "regtest",
}


def _embit_network_name(network: str) -> str:
    try:
        return _EMBIT_NETWORK_NAMES[network]
    except KeyError as exc:
        raise ConfigurationError(f"unknown network: {network!r}") from exc


def _parse_key_architecture(value: KeyArchitecture | str) -> KeyArchitecture:
    try:
        return KeyArchitecture(value)
    except ValueError as exc:
        raise ConfigurationError(
            "key_architecture must be aggregate-then-derive or derive-then-aggregate"
        ) from exc
