import subprocess
from pathlib import Path

import pytest

from bip375_interop.errors import InteropError
from bip375_interop.models import Checkout, HarnessConfig, Scenario
from bip375_interop.preflight import backend_checkout, run_preflight


def _repo(path: Path, dirty: bool = False) -> Path:
    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "f").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "c"], check=True)
    if dirty:
        (path / "f").write_text("changed")
    return path


def _scenario(*backends: str, validators: tuple[str, ...] = ()) -> Scenario:
    return Scenario.from_dict({
        "name": "case", "suite": "bip375", "network": "regtest",
        "signers": [
            {"name": f"s{i}", "backend": backend, "seed_id": f"seed-{i}"}
            for i, backend in enumerate(backends)
        ],
        "validators": list(validators),
    })


def _config(**checkouts: Path) -> HarnessConfig:
    return HarnessConfig(Path("artifacts"), {n: Checkout(n, p) for n, p in checkouts.items()})


def test_backend_checkout_maps_bitsaga_aliases():
    assert backend_checkout("bitsaga") == "bitsaga-seedsigner"
    assert backend_checkout("jade") == "jade"


def test_preflight_reports_every_problem_in_one_error(tmp_path: Path):
    config = _config(jade=_repo(tmp_path / "jade", dirty=True), coldcard=_repo(tmp_path / "cc", dirty=True))

    with pytest.raises(InteropError) as exc:
        run_preflight(config, [_scenario("jade", "coldcard", "seedsigner")])

    text = str(exc.value)
    assert text.startswith("preflight failed")
    assert "jade: checkout is dirty" in text
    assert "coldcard: checkout is dirty" in text
    assert "seedsigner: no checkout configured" in text


def test_preflight_covers_validator_checkouts(tmp_path: Path):
    config = _config(jade=_repo(tmp_path / "jade"))

    with pytest.raises(InteropError, match="caravan: no checkout configured"):
        run_preflight(config, [_scenario("jade", validators=("caravan",))])


def test_preflight_returns_states_for_clean_checkouts(tmp_path: Path):
    config = _config(jade=_repo(tmp_path / "jade"), coldcard=_repo(tmp_path / "cc"))

    states = run_preflight(config, [_scenario("jade", "coldcard", "jade")])

    assert [state.name for state in states] == ["jade", "coldcard"]


def test_preflight_allows_dirty_checkouts_when_configured(tmp_path: Path):
    from dataclasses import replace

    config = replace(_config(jade=_repo(tmp_path / "jade", dirty=True)), allow_dirty=True)

    assert run_preflight(config, [_scenario("jade")])[0].dirty


def test_preflight_names_an_embit_api_mismatch(tmp_path: Path, monkeypatch):
    import embit.silent_payments.sp as sp

    monkeypatch.delattr(sp, "derive_sp_outputs")
    config = _config(jade=_repo(tmp_path / "jade"))

    with pytest.raises(InteropError, match="embit.*derive_sp_outputs"):
        run_preflight(config, [_scenario("jade")])


def test_preflight_rejects_an_embit_whose_grouping_returns_the_old_shape(tmp_path: Path, monkeypatch):
    import embit.silent_payments.sp as sp

    monkeypatch.setattr(sp, "group_sp_outputs_by_scan_key", lambda outputs: {})
    config = _config(jade=_repo(tmp_path / "jade"))

    with pytest.raises(InteropError, match="group_sp_outputs_by_scan_key"):
        run_preflight(config, [_scenario("jade")])


def test_embit_must_be_imported_from_the_configured_checkout(tmp_path: Path):
    import embit

    from bip375_interop.preflight import _embit_problem

    imported_from = Path(embit.__file__).resolve().parent.parent
    assert _embit_problem(Checkout("embit", imported_from)) is None
    assert _embit_problem(None) is None

    problem = _embit_problem(Checkout("embit", tmp_path))

    assert "not the configured checkout" in problem
    assert str(tmp_path) in problem
