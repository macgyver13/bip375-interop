import subprocess
import sys
from pathlib import Path

import pytest

from bip375_interop.adapters import CaravanAdapter
from bip375_interop.build import BuildError, build, plan_builds
from bip375_interop.models import Checkout, HarnessConfig


def _config(tmp_path: Path, *names: str) -> HarnessConfig:
    checkouts = {}
    for name in names:
        path = tmp_path / name
        path.mkdir()
        checkouts[name] = Checkout(name, path)
    return HarnessConfig(tmp_path / "artifacts", checkouts)


def test_silent_pay_and_embit_have_build_steps(tmp_path: Path):
    plans = dict(plan_builds(_config(tmp_path, "silent-pay", "embit")))

    assert plans["silent-pay"][0].argv[:7] == ("cargo", "build", "--release", "--locked", "-p", "sp-demo", "--bin")
    assert plans["embit"][0].argv[1:] == ("-m", "pip", "install", "-q", "-e", ".")


def test_only_names_must_be_in_the_profile(tmp_path: Path):
    with pytest.raises(BuildError, match="not in this profile: jade"):
        plan_builds(_config(tmp_path, "embit"), ["jade"])


def test_jade_needs_idf_path(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("IDF_PATH", raising=False)
    with pytest.raises(BuildError, match="IDF_PATH"):
        plan_builds(_config(tmp_path, "jade"))


def test_caravan_installs_rather_than_ci(tmp_path: Path):
    (tmp_path / "turbo.json").touch()
    (tmp_path / "packages/caravan-psbt").mkdir(parents=True)
    (tmp_path / "packages/caravan-psbt/package.json").touch()

    assert CaravanAdapter(tmp_path).plan_build()[0].argv[:2] == ("npm", "install")


def test_build_stops_at_the_first_failing_step(tmp_path: Path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if argv[0] == "cargo" else 0, "out\n", "boom\n")

    with pytest.raises(BuildError, match="sp-demo-build failed") as error:
        build(_config(tmp_path, "silent-pay", "embit"), runner=runner)

    assert "boom" in str(error.value)
    assert calls[0][0] == "cargo" and len(calls) == 1


def test_a_step_sees_pwd_as_its_working_directory(tmp_path: Path):
    # make's $(PWD) (coldcard's unix/Makefile) comes from the environment, not the cwd.
    from bip375_interop.adapters.base import CommandPlan, execute_plan

    # Not sh: it resets a stale PWD itself, where make does not.
    argv = (sys.executable, "-c", "import os; print(os.environ['PWD'], end='')")
    result = execute_plan(CommandPlan("pwd", argv, tmp_path), subprocess.run)

    assert result.stdout == str(tmp_path)
