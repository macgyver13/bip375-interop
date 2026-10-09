import pytest

from bip375_interop.suites import (
    SUITES,
    Bip375Config,
    FrostSpConfig,
    KeyArchitecture,
    Musig2SpConfig,
    SuiteName,
    get_suite,
    parse_suite_config,
    validate_suite_config,
)
from bip375_interop.errors import CapabilityError, ConfigurationError
from bip375_interop.models import Scenario, SignerSpec


def scenario(
    suite: str,
    *,
    signer_count: int = 1,
    suite_config: dict[str, object] | None = None,
) -> Scenario:
    return Scenario(
        name="test",
        suite=suite,
        network="regtest",
        signers=tuple(
            SignerSpec(name=f"signer-{index}", backend="test", seed_id=str(index))
            for index in range(signer_count)
        ),
        suite_config=suite_config or {},
    )


def test_registry_contains_stable_suite_names() -> None:
    assert set(SUITES) == {
        SuiteName.BIP375,
        SuiteName.MUSIG2_SP,
        SuiteName.FROST_SP,
    }
    assert SUITES[SuiteName.FROST_SP].supported is False


def test_bip375_has_no_suite_specific_options() -> None:
    assert parse_suite_config("bip375") == Bip375Config()


@pytest.mark.parametrize("signer_count", [1, 2, 5])
def test_bip375_accepts_one_or_more_signers(signer_count: int) -> None:
    assert get_suite("bip375").validate(
        scenario("bip375", signer_count=signer_count)
    ) == Bip375Config()


def test_bip375_rejects_an_empty_directly_constructed_roster() -> None:
    with pytest.raises(ConfigurationError, match="at least one"):
        get_suite("bip375").validate(scenario("bip375", signer_count=0))


def test_musig2_sp_defaults_to_firmware_compatible_architecture() -> None:
    assert parse_suite_config("musig2-sp") == Musig2SpConfig(
        key_architecture=KeyArchitecture.AGGREGATE_THEN_DERIVE,
    )


@pytest.mark.parametrize("architecture", list(KeyArchitecture))
def test_musig2_sp_accepts_both_key_architectures(
    architecture: KeyArchitecture,
) -> None:
    assert parse_suite_config(
        "musig2-sp",
        {"key_architecture": architecture.value},
    ) == Musig2SpConfig(key_architecture=architecture)


def test_musig2_sp_requires_two_or_more_signers() -> None:
    with pytest.raises(ConfigurationError, match="at least two"):
        get_suite("musig2-sp").validate(scenario("musig2-sp"))


def test_musig2_sp_validates_complete_scenario() -> None:
    config = get_suite("musig2-sp").validate(
        scenario(
            "musig2-sp",
            signer_count=3,
            suite_config={"key_architecture": "derive-then-aggregate"},
        )
    )
    assert config == Musig2SpConfig(KeyArchitecture.DERIVE_THEN_AGGREGATE)


def test_musig2_sp_rejects_unknown_key_architecture() -> None:
    with pytest.raises(ConfigurationError, match="key_architecture"):
        parse_suite_config("musig2-sp", {"key_architecture": "unknown"})


def test_unknown_config_fields_are_rejected() -> None:
    with pytest.raises(ConfigurationError, match="unknown config field"):
        parse_suite_config("bip375", {"threshold": 1})


def test_unknown_suite_is_distinct_from_reserved_suite() -> None:
    with pytest.raises(ConfigurationError, match="unknown suite"):
        get_suite("unknown")
    assert get_suite("frost-sp").supported is False
    with pytest.raises(CapabilityError, match="not supported"):
        get_suite("frost-sp").validate(scenario("frost-sp"))


def test_frost_config_cannot_be_validated_directly() -> None:
    with pytest.raises(CapabilityError, match="not supported"):
        validate_suite_config(FrostSpConfig())


def test_definition_rejects_a_scenario_for_another_suite() -> None:
    with pytest.raises(ConfigurationError, match="does not match"):
        get_suite("bip375").validate(scenario("musig2-sp", signer_count=2))
