from pathlib import Path

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
