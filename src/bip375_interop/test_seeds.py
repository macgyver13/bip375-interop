"""Published disposable mnemonics for emulator-only interoperability tests."""

from .errors import ConfigurationError


TEST_MNEMONICS = {
    "test-a": "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
    "test-b": "legal winner thank year wave sausage worth useful legal winner thank yellow",
    "test-c": "letter advice cage absurd amount doctor acoustic avoid letter advice cage above",
    "jade-single": "paddle puppy easily actor poet apart screen drastic city front predict damp",
}


def mnemonic_for(seed_id: str) -> str:
    try:
        return TEST_MNEMONICS[seed_id]
    except KeyError as exc:
        raise ConfigurationError(
            f"unknown test seed {seed_id!r}; production secrets are not accepted"
        ) from exc
