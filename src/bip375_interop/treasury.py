"""Derive a silent-pay MuSig2 treasury wallet from the harness's own signers.

silent-pay's ``TreasuryWalletConfig`` is entirely driven by a single BIP-380
descriptor string (see its ``wallet.rs``): there is no separate signer-list
input. This module builds that descriptor -- and the wallet TOML file around
it -- directly from the harness's own published test seeds, rather than from
an externally sourced wallet file. It uses the same
``tr(musig([xfp/48h/1h/0h/3h]xpub,...)/<0;1>/*)`` aggregate-then-derive shape
silent-pay's own demo fixtures use, so the resulting wallet is interoperable
with silent-pay's Coldcard/Jade-matching key architecture.
"""

from __future__ import annotations

from typing import Sequence

from .errors import ConfigurationError
from .test_seeds import mnemonic_for


# BIP-48 script-type-3 (multisig) account path: m/48'/1'/0'/3'. Matches the
# path silent-pay's own demo fixtures (`sp_demo::operations::COSIGNERS_TEST`)
# and Coldcard/Jade MuSig2 firmware use.
ACCOUNT_PATH = (0x80000030, 0x80000001, 0x80000000, 0x80000003)
ACCOUNT_PATH_STR = "48h/1h/0h/3h"


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


def build_treasury_descriptor(seed_ids: Sequence[str], *, network: str = "testnet") -> str:
    """Build the aggregate-then-derive MuSig2 descriptor for these signers."""

    if len(seed_ids) < 2:
        raise ConfigurationError("a MuSig2 treasury descriptor requires at least two signers")
    parts = []
    for seed_id in seed_ids:
        xfp_hex, xpub = derive_signer_xpub(seed_id, network=network)
        parts.append(f"[{xfp_hex}/{ACCOUNT_PATH_STR}]{xpub}")
    return f"tr(musig({','.join(parts)})/<0;1>/*)"


def build_wallet_toml(
    seed_ids: Sequence[str],
    *,
    network: str = "testnet",
    last_derivation_index: int = 0,
    change_derivation_index: int = 0,
) -> str:
    """Render a silent-pay ``TreasuryWalletConfig`` TOML file for these signers."""

    descriptor = build_treasury_descriptor(seed_ids, network=network)
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
