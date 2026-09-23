from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigurationError


# Independent implementations that re-check a run's PSBTs without signing.
# Opt-in per scenario, ``check --exhaustive``, or the ``check --release`` profile.
KNOWN_VALIDATORS = ("caravan", "spdk")


@dataclass(frozen=True)
class Checkout:
    name: str
    path: Path
    revision: str | None = None
    vcs: str | None = None


@dataclass(frozen=True)
class HarnessConfig:
    artifact_root: Path
    checkouts: Mapping[str, Checkout]
    allow_dirty: bool = False


@dataclass(frozen=True)
class SignerSpec:
    name: str
    backend: str
    seed_id: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SignerSpec":
        missing = {"name", "backend", "seed_id"} - value.keys()
        if missing:
            raise ConfigurationError(f"signer missing fields: {', '.join(sorted(missing))}")
        return cls(*(str(value[k]) for k in ("name", "backend", "seed_id")))


@dataclass(frozen=True)
class Scenario:
    name: str
    suite: str
    network: str
    signers: tuple[SignerSpec, ...]
    suite_config: Mapping[str, Any] = field(default_factory=dict)
    inputs: tuple[Mapping[str, Any], ...] = ()
    outputs: tuple[Mapping[str, Any], ...] = ()
    merge_policy: str = "strict"
    verification: str = "full"
    expect_warning: bool = False
    validators: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Scenario":
        missing = {"name", "suite", "network", "signers"} - value.keys()
        if missing:
            raise ConfigurationError(f"scenario missing fields: {', '.join(sorted(missing))}")
        signers = tuple(SignerSpec.from_dict(item) for item in value["signers"])
        if not signers:
            raise ConfigurationError("scenario requires at least one signer")
        names = [signer.name for signer in signers]
        if len(names) != len(set(names)):
            raise ConfigurationError("signer names must be unique")
        merge_policy = str(value.get("merge_policy", "strict"))
        if merge_policy not in ("strict", "combiner"):
            raise ConfigurationError(f"merge_policy must be strict or combiner, got {merge_policy!r}")
        verification = str(value.get("verification", "full"))
        if verification not in ("full", "structural"):
            raise ConfigurationError(f"verification must be full or structural, got {verification!r}")
        validators = tuple(str(item) for item in value.get("validators", ()))
        unknown = sorted(set(validators) - set(KNOWN_VALIDATORS))
        if unknown:
            raise ConfigurationError(f"unknown validators: {', '.join(unknown)}")
        if validators and value["suite"] != "bip375":
            raise ConfigurationError("validators currently apply to the bip375 suite only")
        return cls(
            name=str(value["name"]), suite=str(value["suite"]),
            network=str(value["network"]), signers=signers,
            suite_config=dict(value.get("suite_config", {})),
            inputs=tuple(value.get("inputs", ())), outputs=tuple(value.get("outputs", ())),
            merge_policy=merge_policy, verification=verification,
            expect_warning=bool(value.get("expect_warning", False)),
            validators=validators,
        )
