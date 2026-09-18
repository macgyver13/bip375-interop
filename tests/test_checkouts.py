from pathlib import Path

import shutil
import subprocess

import pytest

from bip375_interop.checkouts import inspect_checkout
from bip375_interop.errors import CheckoutError
from bip375_interop.models import Checkout


def test_unborn_checkout_is_development_only(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "fixture").write_text("data")
    checkout = Checkout("fixture-generator", tmp_path)

    with pytest.raises(CheckoutError):
        inspect_checkout(checkout)

    state = inspect_checkout(checkout, allow_dirty=True)
    assert state.revision == "UNBORN"
    assert state.dirty
    assert state.diff_sha256


needs_jj = pytest.mark.skipif(shutil.which("jj") is None, reason="jj is not installed")


def _jj_repo(path: Path) -> None:
    subprocess.run(["jj", "git", "init", str(path)], check=True, capture_output=True)
    (path / "file").write_text("one")
    subprocess.run(["jj", "-R", str(path), "describe", "-m", "first"], check=True, capture_output=True)


@needs_jj
def test_jj_checkout_reports_working_copy_commit_not_git_head(tmp_path: Path):
    _jj_repo(tmp_path)
    tip = subprocess.check_output(
        ["jj", "-R", str(tmp_path), "log", "-r", "@", "--no-graph", "-T", "commit_id"], text=True
    ).strip()

    state = inspect_checkout(Checkout("silent-pay", tmp_path))

    assert state.vcs == "jj"
    assert state.revision == tip
    assert not state.dirty


@needs_jj
def test_jj_checkout_revision_changes_with_the_working_copy(tmp_path: Path):
    _jj_repo(tmp_path)
    first = inspect_checkout(Checkout("silent-pay", tmp_path)).revision
    (tmp_path / "file").write_text("two")

    second = inspect_checkout(Checkout("silent-pay", tmp_path)).revision

    assert second != first
    with pytest.raises(CheckoutError, match="expected"):
        inspect_checkout(Checkout("silent-pay", tmp_path, revision=first))


def test_git_checkout_reports_git_vcs(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    state = inspect_checkout(Checkout("seedsigner", tmp_path), allow_dirty=True)

    assert state.vcs == "git"
