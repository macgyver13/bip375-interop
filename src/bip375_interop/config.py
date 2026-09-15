from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigurationError
from .models import Checkout, HarnessConfig, Scenario


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
    checkouts: dict[str, Checkout] = {}
    for name, item in raw_checkouts.items():
        if not isinstance(item, dict) or "path" not in item:
            raise ConfigurationError(f"checkout {name} must define path")
        checkout_path = Path(item["path"]).expanduser()
        checkouts[name] = Checkout(name, checkout_path, item.get("revision"))
    return HarnessConfig(artifact_root, checkouts, bool(value.get("allow_dirty", False)))


def load_scenario(path: Path) -> Scenario:
    return Scenario.from_dict(_load_yaml(path))
