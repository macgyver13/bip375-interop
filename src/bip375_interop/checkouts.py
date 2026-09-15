from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass

from .errors import CheckoutError
from .models import Checkout


@dataclass(frozen=True)
class CheckoutState:
    name: str
    path: str
    revision: str
    dirty: bool
    diff_sha256: str | None


def _git(checkout: Checkout, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(checkout.path), *args], stderr=subprocess.STDOUT
        ).strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.output.decode(errors="replace").strip()
        raise CheckoutError(f"{checkout.name}: git {' '.join(args)} failed: {detail}") from exc


def inspect_checkout(checkout: Checkout, allow_dirty: bool = False) -> CheckoutState:
    if not checkout.path.is_dir():
        raise CheckoutError(f"{checkout.name}: missing checkout {checkout.path}")
    try:
        revision = _git(checkout, "rev-parse", "--verify", "HEAD").decode()
    except CheckoutError:
        # An initialized but unborn repository is useful during development, but
        # can never be claimed as pinned or reproducible.
        if checkout.revision or not allow_dirty:
            raise
        revision = "UNBORN"
    if checkout.revision and revision != checkout.revision:
        raise CheckoutError(
            f"{checkout.name}: expected {checkout.revision}, found {revision}"
        )
    status = _git(checkout, "status", "--porcelain=v1")
    dirty = bool(status)
    if dirty and not allow_dirty:
        raise CheckoutError(f"{checkout.name}: checkout is dirty (use --allow-dirty for development)")
    diff_hash = None
    if dirty:
        tracked = b"" if revision == "UNBORN" else _git(checkout, "diff", "--binary", "HEAD")
        diff_hash = hashlib.sha256(tracked + b"\0" + status).hexdigest()
    return CheckoutState(checkout.name, str(checkout.path), revision, dirty, diff_hash)
