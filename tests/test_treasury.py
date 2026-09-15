from __future__ import annotations

import pytest

from bip375_interop.errors import ConfigurationError
from bip375_interop.treasury import build_treasury_descriptor, build_wallet_toml, derive_signer_xpub


def test_derive_signer_xpub_is_deterministic() -> None:
    pytest.importorskip("embit")
    xfp_a, xpub_a = derive_signer_xpub("test-a")
    xfp_b, xpub_b = derive_signer_xpub("test-a")
    assert (xfp_a, xpub_a) == (xfp_b, xpub_b)
    assert len(xfp_a) == 8
    assert xpub_a.startswith("tpub")


def test_derive_signer_xpub_differs_per_seed() -> None:
    pytest.importorskip("embit")
    xfp_a, xpub_a = derive_signer_xpub("test-a")
    xfp_b, xpub_b = derive_signer_xpub("test-b")
    assert xfp_a != xfp_b
    assert xpub_a != xpub_b


def test_build_treasury_descriptor_is_aggregate_then_derive() -> None:
    pytest.importorskip("embit")
    descriptor = build_treasury_descriptor(["test-a", "test-b", "test-c"])
    assert descriptor.startswith("tr(musig(")
    assert descriptor.endswith(")/<0;1>/*)")
    assert descriptor.count("[") == 3


def test_build_treasury_descriptor_requires_at_least_two_signers() -> None:
    pytest.importorskip("embit")
    with pytest.raises(ConfigurationError):
        build_treasury_descriptor(["test-a"])


def test_build_wallet_toml_round_trips_as_toml() -> None:
    pytest.importorskip("embit")
    pytest.importorskip("toml", reason="only needed to validate the rendered TOML")
    import toml

    text = build_wallet_toml(["test-a", "test-b", "test-c"], network="signet")
    parsed = toml.loads(text)
    assert parsed["network"] == "signet"
    assert parsed["descriptor"].startswith("tr(musig(")
    assert parsed["last_derivation_index"] == 0
    assert parsed["change_derivation_index"] == 0


def test_derive_signer_xpub_rejects_unknown_seed() -> None:
    pytest.importorskip("embit")
    with pytest.raises(ConfigurationError):
        derive_signer_xpub("not-a-real-seed")
