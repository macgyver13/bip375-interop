from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError
from .models import Checkout, HarnessConfig, Scenario
from .suites import SuiteName

LOCK_NAME = "interop.lock"
VCS_KINDS = ("git", "jj", "gitbutler")


def read_lock(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    pins = _load_yaml(path).get("checkouts", {})
    if not isinstance(pins, dict):
        raise ConfigurationError(f"{path} checkouts must be a mapping")
    return {str(name): str(revision) for name, revision in pins.items()}


def write_lock(path: Path, pins: dict[str, str]) -> None:
    header = "# Commit id of every checkout the expectations apply to. Written by `pin`.\n"
    path.write_text(header + yaml.safe_dump({"checkouts": dict(sorted(pins.items()))}))


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"failed to read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must contain a YAML mapping")
    return value


def load_config(path: Path) -> HarnessConfig:
    value = _load_yaml(path)
    base = path.parent
    artifact_root = Path(value.get("artifact_root", "artifacts"))
    if not artifact_root.is_absolute():
        artifact_root = (base / artifact_root).resolve()
    raw_checkouts = value.get("checkouts", {})
    if not isinstance(raw_checkouts, dict):
        raise ConfigurationError("checkouts must be a mapping")
    lock = read_lock(base / LOCK_NAME)
    checkouts: dict[str, Checkout] = {}
    for name, item in raw_checkouts.items():
        if not isinstance(item, dict) or "path" not in item:
            raise ConfigurationError(f"checkout {name} must define path")
        checkout_path = Path(item["path"]).expanduser()
        revision = item.get("revision")
        pinned = lock.get(name)
        if revision and pinned and revision != pinned:
            raise ConfigurationError(
                f"checkout {name}: interop.yaml revision {revision} conflicts with {LOCK_NAME} {pinned}"
            )
        vcs = item.get("vcs")
        if vcs is not None and vcs not in VCS_KINDS:
            raise ConfigurationError(f"checkout {name}: vcs must be one of {VCS_KINDS}, got {vcs!r}")
        checkouts[name] = Checkout(name, checkout_path, revision or pinned, vcs)
    suites = value.get("suites")
    if suites is not None:
        known = {suite.value for suite in SuiteName}
        if not isinstance(suites, list) or not suites or not set(suites) <= known:
            raise ConfigurationError(f"suites must be a non-empty list drawn from {sorted(known)}")
        suites = tuple(suites)
    return HarnessConfig(artifact_root, checkouts, bool(value.get("allow_dirty", False)), suites)


def load_scenario(path: Path) -> Scenario:
    return Scenario.from_dict(_load_yaml(path))
