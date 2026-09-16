"""Suite models and registry for the interoperability harness.

This module deliberately describes only the configuration that the harness can
execute today.  ``frost-sp`` is reserved so scenario files can name it and get a
stable, explicit unsupported-suite error without implying a FROST protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from .errors import CapabilityError, ConfigurationError
from .models import Scenario
from .engine import Round


class SuiteName(str, Enum):
    """Stable names accepted in scenario configuration."""

    BIP375 = "bip375"
    MUSIG2_SP = "musig2-sp"
    FROST_SP = "frost-sp"


class KeyArchitecture(str, Enum):
    """Where child derivation occurs relative to MuSig2 aggregation."""

    AGGREGATE_THEN_DERIVE = "aggregate-then-derive"
    DERIVE_THEN_AGGREGATE = "derive-then-aggregate"


@dataclass(frozen=True)
class Bip375Config:
    """Configuration for a plain BIP-375 signer interoperability run."""

    contribution_mode: str = "per-input"

    def validate(self) -> None:
        if self.contribution_mode not in {"global", "per-input"}:
            raise ConfigurationError(
                "bip375 contribution_mode must be global or per-input"
            )


@dataclass(frozen=True)
class Musig2SpConfig:
    """Configuration for the N-of-N MuSig2 Silent Payments suite."""

    key_architecture: KeyArchitecture = KeyArchitecture.AGGREGATE_THEN_DERIVE
    threshold: int | None = None
    receive_index: int = 0
    change_index: int = 0

    def validate(self) -> None:
        if not isinstance(self.key_architecture, KeyArchitecture):
            raise ConfigurationError(
                "musig2-sp key_architecture must be aggregate-then-derive "
                "or derive-then-aggregate"
            )
        if self.threshold is not None and self.threshold < 2:
            raise ConfigurationError("musig2-sp threshold must be at least two")
        if self.receive_index < 0 or self.change_index < 0:
            raise ConfigurationError("derivation indices must be non-negative")


@dataclass(frozen=True)
class FrostSpConfig:
    """Reserved configuration type for the unsupported FROST suite."""


SuiteConfig: TypeAlias = Bip375Config | Musig2SpConfig | FrostSpConfig


@dataclass(frozen=True)
class SuiteDefinition:
    """Registry entry for one stable suite name."""

    name: SuiteName
    config_type: type[SuiteConfig]
    supported: bool

    def rounds(self, signer_names: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
        """Return protocol phases without coupling them to a device transport."""
        if not self.supported:
            raise CapabilityError(f"suite {self.name.value!r} is not supported")
        if self.name is SuiteName.BIP375:
            return (("contribute", signer_names), ("sign", signer_names))
        if self.name is SuiteName.MUSIG2_SP:
            return (("round1", signer_names), ("round2", signer_names))
        raise CapabilityError(f"suite {self.name.value!r} is not supported")

    def validate(self, scenario: Scenario) -> SuiteConfig:
        """Validate suite identity, suite config, and signer cardinality."""

        if scenario.suite != self.name.value:
            raise ConfigurationError(
                f"scenario suite {scenario.suite!r} does not match {self.name.value!r}"
            )
        if not self.supported:
            raise CapabilityError(f"suite {self.name.value!r} is not supported")
        if not scenario.signers:
            raise ConfigurationError(f"{self.name.value} requires at least one signer")
        if self.name is SuiteName.MUSIG2_SP and len(scenario.signers) < 2:
            raise ConfigurationError("musig2-sp requires at least two signers")
        if self.name is SuiteName.BIP375:
            mode = scenario.suite_config.get("contribution_mode", "per-input")
            if mode == "global" and len(scenario.signers) != 1:
                raise ConfigurationError(
                    "bip375 global contribution mode requires exactly one signer"
                )
        if self.name is SuiteName.MUSIG2_SP:
            threshold = scenario.suite_config.get("threshold")
            if threshold is not None and threshold != len(scenario.signers):
                raise ConfigurationError("musig2-sp currently requires N-of-N threshold")
        return parse_suite_config(self.name, scenario.suite_config)


_SUITES = {
    SuiteName.BIP375: SuiteDefinition(SuiteName.BIP375, Bip375Config, True),
    SuiteName.MUSIG2_SP: SuiteDefinition(SuiteName.MUSIG2_SP, Musig2SpConfig, True),
    SuiteName.FROST_SP: SuiteDefinition(SuiteName.FROST_SP, FrostSpConfig, False),
}

SUITES: Mapping[SuiteName, SuiteDefinition] = MappingProxyType(_SUITES)


def get_suite(name: str | SuiteName) -> SuiteDefinition:
    """Return a registered suite, including reserved unsupported suites."""

    suite_name = _parse_suite_name(name)
    return SUITES[suite_name]


def parse_suite_config(
    name: str | SuiteName, values: Mapping[str, Any] | None = None
) -> SuiteConfig:
    """Construct and validate the typed config for a supported suite."""

    definition = get_suite(name)
    if not definition.supported:
        raise CapabilityError(f"suite {definition.name.value!r} is not supported")
    raw_values = dict(values or {})
    allowed = {field.name for field in fields(definition.config_type)}
    unknown = sorted(raw_values.keys() - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ConfigurationError(
            f"suite {definition.name.value!r} has unknown config field(s): {joined}"
        )

    if definition.name is SuiteName.MUSIG2_SP and "key_architecture" in raw_values:
        raw_values["key_architecture"] = _parse_key_architecture(
            raw_values["key_architecture"]
        )

    config = definition.config_type(**raw_values)
    validate_suite_config(config)
    return config


def validate_suite_config(config: SuiteConfig) -> None:
    """Validate a config instance and reject reserved config types."""

    if isinstance(config, FrostSpConfig):
        raise CapabilityError("suite 'frost-sp' is not supported")
    if not isinstance(config, (Bip375Config, Musig2SpConfig)):
        raise ConfigurationError(
            f"unrecognized suite config type: {type(config).__name__}"
        )
    config.validate()


def _parse_suite_name(name: str | SuiteName) -> SuiteName:
    if isinstance(name, SuiteName):
        return name
    try:
        return SuiteName(name)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"unknown suite: {name!r}") from exc


def _parse_key_architecture(value: Any) -> KeyArchitecture:
    if isinstance(value, KeyArchitecture):
        return value
    try:
        return KeyArchitecture(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "musig2-sp key_architecture must be aggregate-then-derive "
            "or derive-then-aggregate"
        ) from exc


def scenario_rounds(scenario: Scenario) -> tuple[Round, ...]:
    """Build the signer schedule required by a validated scenario."""
    definition = get_suite(scenario.suite)
    definition.validate(scenario)
    names = tuple(signer.name for signer in scenario.signers)
    if definition.name is SuiteName.MUSIG2_SP:
        return (Round("round1", names), Round("round2", names))
    mode = scenario.suite_config.get("contribution_mode", "per-input")
    # The contribute/resolve-sign/sign dance exists to collect every owner's
    # ECDH share before a Silent Payment *output* can be resolved (BIP-375
    # send). A scenario with no such output -- e.g. BIP-376 spend inputs
    # settling to a plain P2WPKH/P2TR destination -- has nothing to collect,
    # so each signer only needs one pass; a second "sign" round would hand a
    # signer an already-fully-signed PSBT (Coldcard correctly rejects that as
    # "completely signed already", so this is not just an optimization).
    needs_sp_output_contribution = any(
        item.get("type") == "silent-payment" for item in scenario.outputs
    )
    if mode == "global" or len(names) == 1 or not needs_sp_output_contribution:
        return (Round("resolve-sign", names),)
    return (
        Round("contribute", names[:-1]),
        Round("resolve-sign", names[-1:]),
        Round("sign", names[:-1]),
    )
