"""Compare live checkouts with locally available baseline revisions, without fetching."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

from .checkouts import _byproduct, _git, inspect_checkout
from .config import read_lock
from .errors import CheckoutError, ConfigurationError
from .models import Checkout, HarnessConfig


def compare_checkouts(config: HarnessConfig, baseline_lock: Path) -> dict:
    baseline_lock = baseline_lock.expanduser().resolve()
    try:
        lock_bytes = baseline_lock.read_bytes()
    except OSError as exc:
        raise ConfigurationError(f"failed to read baseline lock {baseline_lock}: {exc}") from exc
    pins = read_lock(baseline_lock)
    rows = []
    for name in sorted(set(pins) | set(config.checkouts)):
        checkout = config.checkouts.get(name)
        row = {
            "name": name, "path": str(checkout.path.resolve()) if checkout else None,
            "baseline_revision": pins.get(name), "revision": None,
            "dirty": False, "diff_sha256": None, "vcs": None,
            "status": "missing", "ahead": None, "behind": None,
            "commits": [], "changed_files": [], "uncommitted_files": [], "error": None,
        }
        rows.append(row)
        if checkout is None or not checkout.path.is_dir():
            row["error"] = "No live checkout configured" if checkout is None else "Live checkout is missing"
            continue
        checkout = replace(checkout, path=checkout.path.resolve(), revision=None)
        try:
            state = inspect_checkout(checkout, allow_dirty=True)
            row.update(revision=state.revision, dirty=state.dirty,
                       diff_sha256=state.diff_sha256, vcs=state.vcs)
            if state.vcs == "jj":
                _compare_jj(checkout, row)
            else:
                row["uncommitted_files"] = _uncommitted_git_files(checkout)
                if state.diff_sha256 is not None:
                    row["diff_sha256"] = _dirty_git_hash(checkout, state.diff_sha256)
                _compare_git(checkout, row)
        except (CheckoutError, OSError) as exc:
            row.update(status="error", error=str(exc))
    return {
        "baseline_lock": str(baseline_lock),
        "lock_sha256": "sha256:" + hashlib.sha256(lock_bytes).hexdigest(),
        "checkouts": rows,
    }


def _uncommitted_git_files(checkout: Checkout) -> list[str]:
    # NUL records preserve spaces, newlines, and both paths of a rename.
    records = iter(_git(checkout, "status", "--porcelain=v2", "-z", "--untracked-files=all").split(b"\0"))
    paths = set()
    for record in records:
        line = record.decode(errors="replace")
        if not line:
            continue
        if line.startswith("1 "):
            if not _byproduct(checkout, line):
                paths.add(line.split(" ", 8)[8])
        elif line.startswith("2 "):
            paths.add(line.split(" ", 9)[9])
            paths.add(next(records).decode(errors="replace"))
        elif line.startswith("u "):
            paths.add(line.split(" ", 10)[10])
        elif line.startswith("? "):
            paths.add(line[2:])
    return sorted(paths)


def _dirty_git_hash(checkout: Checkout, tracked_hash: str) -> str:
    files = _git(checkout, "ls-files", "--others", "--exclude-standard", "-z")
    if not files:
        return tracked_hash
    digest = hashlib.sha256(bytes.fromhex(tracked_hash))
    for name in sorted(filter(None, files.split(b"\0"))):
        path = checkout.path / os.fsdecode(name)
        # Hash a symlink's target string, as Git does, without following it.
        content = os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()
        digest.update(name + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def _status(row: dict, ahead: int, behind: int) -> None:
    row.update(ahead=ahead, behind=behind)
    row["status"] = ("diverged" if ahead and behind else "ahead" if ahead
                     else "behind" if behind else "matches")


def _unavailable(row: dict, exc: Exception) -> None:
    row.update(status="revision-differs", error=f"Baseline object or history unavailable locally; no fetch attempted: {exc}")


def _compare_git(checkout: Checkout, row: dict) -> None:
    baseline = row["baseline_revision"]
    if baseline is None:
        row["status"] = "unpinned"
        return
    if "," in baseline:
        # A list of GitButler parent tips does not identify a baseline tree.
        if baseline == row["revision"]:
            _status(row, 0, 0)
        else:
            row["status"] = "revision-differs"
        row["error"] = "GitButler baseline has multiple parent tips; baseline tree and ancestry comparison unavailable"
        return
    try:
        base = _git(checkout, "rev-parse", "--verify", "--end-of-options", baseline + "^{commit}").decode()
        multiple_tips = row["vcs"] == "gitbutler" and "," in row["revision"]
        live_tip = row["revision"] if row["vcs"] == "gitbutler" and not multiple_tips else "HEAD"
        head = _git(checkout, "rev-parse", "--verify", live_tip).decode()
        row["changed_files"] = sorted(filter(None, _git(
            checkout, "diff", "--name-only", "-z", base, head, "--",
        ).decode(errors="replace").split("\0")))
        if base != head and _git(checkout, "rev-parse", "--is-shallow-repository") == b"true":
            # Shallow boundaries can make related tips appear to have diverged.
            raise CheckoutError("Checkout is shallow; complete ancestry is unavailable")
        behind, ahead = map(int, _git(checkout, "rev-list", "--left-right", "--count", f"{base}...{head}").split())
        history = _git(checkout, "log", "-50", "--format=%H%x00%s", f"{base}..{head}").decode(errors="replace")
        row["commits"] = [dict(zip(("revision", "subject"), line.split("\0", 1))) for line in history.splitlines()]
        _status(row, ahead, behind)
        if multiple_tips:
            row["error"] = "Tree and ancestry use GitButler workspace HEAD; history may include synthetic workspace commits"
    except CheckoutError as exc:
        _unavailable(row, exc)


def _jj(checkout: Checkout, *args: str) -> bytes:
    try:
        return subprocess.run(["jj", "-R", str(checkout.path), *args],
                              cwd=checkout.path, capture_output=True, check=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise CheckoutError(exc.stderr.decode(errors="replace").strip()) from exc


def _compare_jj(checkout: Checkout, row: dict) -> None:
    live = json.dumps(row["revision"])
    # Inspection snapshots @; all working files are recorded in that revision.
    baseline = row["baseline_revision"]
    if baseline is None:
        row["status"] = "unpinned"
        return
    try:
        base = json.dumps(_jj(checkout, "log", "-r", json.dumps(baseline), "--no-graph", "-T", "commit_id").decode())
        ahead_set = f"ancestors({live}) ~ ancestors({base})"
        behind_set = f"ancestors({base}) ~ ancestors({live})"
        template = 'commit_id ++ "\\0" ++ description.first_line() ++ "\\n"'
        history = _jj(checkout, "log", "-r", ahead_set, "--limit", "50", "--no-graph", "-T", template).decode(errors="replace")
        row["commits"] = [dict(zip(("revision", "subject"), line.split("\0", 1))) for line in history.splitlines()]
        ahead = len(_jj(checkout, "log", "-r", ahead_set, "--no-graph", "-T", 'commit_id ++ "\\n"').splitlines())
        behind = len(_jj(checkout, "log", "-r", behind_set, "--no-graph", "-T", 'commit_id ++ "\\n"').splitlines())
        row["changed_files"] = _jj(checkout, "diff", "--from", base, "--to", live, "--name-only").decode(errors="replace").splitlines()
        _status(row, ahead, behind)
    except CheckoutError as exc:
        _unavailable(row, exc)
