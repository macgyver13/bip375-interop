from pathlib import Path

import pytest

from bip375_interop.config import load_config, read_lock, write_lock
from bip375_interop.errors import ConfigurationError


def _config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "interop.yaml"
    path.write_text(body)
    return path


def test_write_then_read_lock_round_trips_sorted(tmp_path: Path):
    lock = tmp_path / "interop.lock"

    write_lock(lock, {"spdk": "b" * 40, "embit": "a" * 40})

    assert read_lock(lock) == {"embit": "a" * 40, "spdk": "b" * 40}
    assert lock.read_text().index("embit") < lock.read_text().index("spdk")


def test_missing_lock_is_empty(tmp_path: Path):
    assert read_lock(tmp_path / "interop.lock") == {}


def test_lock_fills_unset_checkout_revisions(tmp_path: Path):
    path = _config(tmp_path, "checkouts:\n  embit: {path: /src/embit}\n  spdk: {path: /src/spdk}\n")
    write_lock(tmp_path / "interop.lock", {"embit": "a" * 40})

    checkouts = load_config(path).checkouts

    assert checkouts["embit"].revision == "a" * 40
    assert checkouts["spdk"].revision is None


def test_lock_conflicting_with_explicit_revision_is_rejected(tmp_path: Path):
    path = _config(tmp_path, f"checkouts:\n  embit: {{path: /src/embit, revision: {'c' * 40}}}\n")
    write_lock(tmp_path / "interop.lock", {"embit": "a" * 40})

    with pytest.raises(ConfigurationError, match="embit"):
        load_config(path)


def test_checkout_vcs_defaults_to_auto_and_rejects_unknown(tmp_path: Path):
    path = _config(tmp_path, "checkouts:\n  embit: {path: /src/embit}\n  spdk: {path: /src/spdk, vcs: jj}\n")
    checkouts = load_config(path).checkouts
    assert checkouts["embit"].vcs is None
    assert checkouts["spdk"].vcs == "jj"

    bad = _config(tmp_path, "checkouts:\n  embit: {path: /src/embit, vcs: hg}\n")
    with pytest.raises(ConfigurationError, match="vcs"):
        load_config(bad)


def test_config_suites_default_to_all_and_reject_unknown(tmp_path: Path):
    assert load_config(_config(tmp_path, "checkouts: {}\n")).suites is None
    assert load_config(_config(tmp_path, "suites: [bip375]\ncheckouts: {}\n")).suites == ("bip375",)
    with pytest.raises(ConfigurationError, match="suites"):
        load_config(_config(tmp_path, "suites: [bip999]\ncheckouts: {}\n"))
