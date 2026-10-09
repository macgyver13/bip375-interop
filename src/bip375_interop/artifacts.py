from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class ArtifactRun:
    def __init__(self, root: Path, scenario_name: str):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.path = root / f"{timestamp}-{scenario_name}-{uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=False)
        self._files: dict[str, str] = {}

    def write(self, name: str, data: bytes) -> Path:
        path = self.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._files[name] = hashlib.sha256(data).hexdigest()
        return path

    def finalize(self, metadata: dict[str, Any]) -> Path:
        manifest = dict(metadata)
        manifest["files"] = dict(sorted(self._files.items()))
        manifest["reproducible"] = not any(
            item.get("dirty", False) for item in manifest.get("checkouts", [])
        )
        data = json.dumps(manifest, indent=2, sort_keys=True, default=_json_default).encode() + b"\n"
        return self.write("manifest.json", data)


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)
