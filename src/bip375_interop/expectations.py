"""Expected outcome per scenario for the pinned checkout revisions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import _load_yaml
from .errors import ConfigurationError

STATUSES = ("supported", "finding", "unsupported", "needs-external-psbt", "unclassified")


def require_lock_digest(expectations_path: Path, lock_path: Path) -> None:
    """A release expectation applies only to the exact checkout lock it names."""

    expected = _load_yaml(expectations_path).get("lock_sha256")
    if not lock_path.is_file():
        raise ConfigurationError(f"release lock is missing: {lock_path}")
    actual = "sha256:" + hashlib.sha256(lock_path.read_bytes()).hexdigest()
    if expected != actual:
        raise ConfigurationError(
            f"{expectations_path} lock_sha256 does not match {lock_path}: "
            f"expected {actual}"
        )


def interop_lab_allowed_findings(path: Path) -> set[str]:
    from .interop_lab import KNOWN_FINDINGS

    values = _load_yaml(path).get("interop_lab_allowed_findings", [])
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise ConfigurationError(f"{path} interop_lab_allowed_findings must be a list of names")
    unknown = set(values) - set(KNOWN_FINDINGS)
    if unknown:
        raise ConfigurationError(f"unknown Interop Lab findings: {', '.join(sorted(unknown))}")
    return set(values)


@dataclass(frozen=True)
class Expectation:
    status: str
    reason: str
    reference: str | None


def load_expectations(path: Path) -> dict[str, Expectation]:
    raw = _load_yaml(path).get("scenarios")
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path} must define a scenarios mapping")
    expectations: dict[str, Expectation] = {}
    for name, item in raw.items():
        if not isinstance(item, dict):
            raise ConfigurationError(f"expectation {name} must be a mapping")
        status = item.get("status")
        if status not in STATUSES:
            raise ConfigurationError(
                f"expectation {name} has invalid status {status!r}; expected one of {STATUSES}"
            )
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason:
            raise ConfigurationError(f"expectation {name} must define a reason")
        expectations[name] = Expectation(status, reason, item.get("reference"))
    return expectations


def require_coverage(expectations: dict[str, Expectation], scenario_names: Iterable[str]) -> None:
    """Every scenario needs exactly one expectation, and no expectation may be stale."""

    names = set(scenario_names)
    missing = sorted(names - expectations.keys())
    unknown = sorted(expectations.keys() - names)
    if missing or unknown:
        parts = []
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        if unknown:
            parts.append(f"unknown: {', '.join(unknown)}")
        raise ConfigurationError("expectations do not match scenarios (" + "; ".join(parts) + ")")
