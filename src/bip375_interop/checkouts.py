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
    vcs: str = "git"


def _git(checkout: Checkout, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(checkout.path), *args], stderr=subprocess.STDOUT
        ).strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.output.decode(errors="replace").strip()
        raise CheckoutError(f"{checkout.name}: git {' '.join(args)} failed: {detail}") from exc


def _vcs(checkout: Checkout) -> str:
    if checkout.vcs:
        return checkout.vcs
    if (checkout.path / ".jj").is_dir():
        return "jj"
    try:
        ref = _git(checkout, "symbolic-ref", "--quiet", "--short", "HEAD").decode()
    except CheckoutError:
        return "git"
    return "gitbutler" if ref.startswith("gitbutler/") else "git"


def _jj_revision(checkout: Checkout) -> str:
    # In a jj repo git's HEAD is the working copy's parent, so the tip under test is
    # the working-copy commit itself. Reading it snapshots the working copy, which
    # makes the commit id a complete record of the files on disk.
    try:
        return subprocess.check_output(
            ["jj", "-R", str(checkout.path), "log", "-r", "@", "--no-graph", "-T", "commit_id"],
            stderr=subprocess.STDOUT,
        ).decode().strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "output", b"").decode(errors="replace").strip() or str(exc)
        raise CheckoutError(f"{checkout.name}: jj log failed: {detail}") from exc


def _require_pinned(checkout: Checkout, revision: str) -> None:
    if checkout.revision and revision != checkout.revision:
        raise CheckoutError(
            f"{checkout.name}: expected {checkout.revision}, found {revision}"
        )


def inspect_checkout(checkout: Checkout, allow_dirty: bool = False) -> CheckoutState:
    if not checkout.path.is_dir():
        raise CheckoutError(f"{checkout.name}: missing checkout {checkout.path}")
    vcs = _vcs(checkout)
    if vcs == "jj":
        revision = _jj_revision(checkout)
        _require_pinned(checkout, revision)
        return CheckoutState(checkout.name, str(checkout.path), revision, False, None, vcs)
    try:
        if vcs == "gitbutler":
            rev_list = _git(checkout, "rev-list", "--parents", "-n1", "HEAD").decode()
        else:
            revision = _git(checkout, "rev-parse", "--verify", "HEAD").decode()
    except CheckoutError:
        # An initialized but unborn repository is useful during development, but
        # can never be claimed as pinned or reproducible.
        if checkout.revision or not allow_dirty:
            raise
        revision = "UNBORN"
    else:
        if vcs == "gitbutler":
            parents = rev_list.split()[1:]
            if not parents:
                raise CheckoutError(
                    f"{checkout.name}: GitButler workspace commit has no parent tips"
                )
            revision = parents[0] if len(parents) == 1 else ",".join(sorted(parents))
    _require_pinned(checkout, revision)
    status = _git(checkout, "status", "--porcelain=v1")
    dirty = bool(status)
    if dirty and not allow_dirty:
        raise CheckoutError(f"{checkout.name}: checkout is dirty (use --allow-dirty for development)")
    diff_hash = None
    if dirty:
        tracked = b"" if revision == "UNBORN" else _git(checkout, "diff", "--binary", "HEAD")
        diff_hash = hashlib.sha256(tracked + b"\0" + status).hexdigest()
    return CheckoutState(checkout.name, str(checkout.path), revision, dirty, diff_hash, vcs)
