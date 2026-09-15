from pathlib import Path

import pytest

from bip375_interop.artifacts import ArtifactRun
from bip375_interop.config import load_scenario
from bip375_interop.errors import ConfigurationError
from bip375_interop.models import Scenario


def test_scenario_requires_unique_signers():
    with pytest.raises(ConfigurationError, match="unique"):
        Scenario.from_dict({
            "name": "bad", "suite": "bip375", "network": "regtest",
            "signers": [
                {"name": "a", "backend": "jade", "seed_id": "one"},
                {"name": "a", "backend": "coldcard", "seed_id": "two"},
            ],
        })


def test_artifact_manifest_hashes_files(tmp_path: Path):
    run = ArtifactRun(tmp_path, "example")
    run.write("initial.psbt", b"psbt\xfffixture")
    manifest = run.finalize({"checkouts": [{"dirty": False}]})
    text = manifest.read_text()
    assert "initial.psbt" in text
    assert '"reproducible": true' in text
