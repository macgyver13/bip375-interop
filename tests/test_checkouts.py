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


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _configure_git(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)


def _committed_branch(path: Path, commits: int = 2) -> str:
    _configure_git(path)
    for i in range(commits):
        (path / "f").write_text(f"c{i}")
        subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
        subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", f"c{i}"], check=True)
    return _git(path, "rev-parse", "HEAD")


def _point_gitbutler_workspace(path: Path, parents: list[str], message: str) -> str:
    tree = _git(path, "rev-parse", "HEAD^{tree}")
    cmd = ["commit-tree", tree]
    for parent in parents:
        cmd.extend(["-p", parent])
    cmd.extend(["-m", message])
    workspace = _git(path, *cmd)
    subprocess.run(
        ["git", "-C", str(path), "update-ref", "refs/heads/gitbutler/workspace", workspace],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "HEAD", "refs/heads/gitbutler/workspace"],
        check=True,
    )
    return workspace


def _gitbutler_workspace(path: Path, parent_count: int = 1) -> tuple[str, list[str]]:
    _configure_git(path)
    (path / "f").write_text("base")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "base"], check=True)
    base = _git(path, "rev-parse", "HEAD")
    tree = _git(path, "rev-parse", "HEAD^{tree}")
    parents = [
        _git(path, "commit-tree", tree, "-p", base, "-m", f"tip-{i}")
        for i in range(parent_count)
    ]
    workspace = _point_gitbutler_workspace(path, sorted(parents, reverse=True), "workspace")
    return workspace, parents


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
def test_jj_checkout_revision_ignores_a_stderr_notice_from_jj(tmp_path: Path):
    # A colocated jj/git repo prints an informational notice to stderr, not stdout, the
    # next time jj runs after something touches the underlying git refs directly (a bare
    # git command, GitButler, another process). That notice must not corrupt the
    # commit id `doctor` reports.
    subprocess.run(["jj", "git", "init", "--colocate", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "file").write_text("one")
    subprocess.run(["jj", "-R", str(tmp_path), "describe", "-m", "first"], check=True, capture_output=True)
    tip = subprocess.check_output(
        ["jj", "-R", str(tmp_path), "log", "-r", "@", "--no-graph", "-T", "commit_id"], text=True
    ).strip()
    # A plain git branch pointed at the working-copy commit's own git object touches the
    # underlying git refs without going through jj (and without moving @, unlike a git
    # commit on the tracked branch, which jj would legitimately rebase @ onto). This is
    # what makes jj print the notice below on its next invocation.
    subprocess.run(["git", "-C", str(tmp_path), "branch", "touched-by-git", tip], check=True)
    notice = subprocess.run(
        ["jj", "-R", str(tmp_path), "log", "-r", "@", "--no-graph", "-T", "commit_id"],
        capture_output=True,
    ).stderr
    assert notice, "fixture did not reproduce jj's stderr notice; adjust the setup"

    state = inspect_checkout(Checkout("silent-pay", tmp_path))

    assert state.revision == tip


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
    head = _committed_branch(tmp_path)
    parent = _git(tmp_path, "rev-parse", "HEAD^")
    state = inspect_checkout(Checkout("seedsigner", tmp_path))

    assert state.vcs == "git"
    assert state.revision == head
    assert state.revision != parent
    assert not state.dirty


def test_gitbutler_one_parent_reports_parent_not_workspace(tmp_path: Path):
    workspace, parents = _gitbutler_workspace(tmp_path)
    state = inspect_checkout(Checkout("jade", tmp_path))

    assert state.vcs == "gitbutler"
    assert state.revision == parents[0]
    assert state.revision != workspace

    rewritten = _point_gitbutler_workspace(tmp_path, parents, "workspace-rewrite")
    assert rewritten != workspace
    again = inspect_checkout(Checkout("jade", tmp_path))
    assert again.revision == parents[0]


def test_gitbutler_several_parents_are_sorted_and_joined(tmp_path: Path):
    workspace, parents = _gitbutler_workspace(tmp_path, parent_count=2)
    merge_order = _git(tmp_path, "rev-list", "--parents", "-n1", "HEAD").split()[1:]
    state = inspect_checkout(Checkout("jade", tmp_path))

    assert merge_order == sorted(parents, reverse=True)
    assert state.vcs == "gitbutler"
    assert state.revision == ",".join(sorted(parents))
    assert state.revision != workspace
    assert ",".join(merge_order) != state.revision


def test_explicit_vcs_overrides_gitbutler_detection(tmp_path: Path):
    gb = tmp_path / "gb"
    workspace, parents = _gitbutler_workspace(gb)
    git_state = inspect_checkout(Checkout("jade", gb, vcs="git"))

    assert git_state.vcs == "git"
    assert git_state.revision == workspace
    assert git_state.revision != parents[0]

    ordinary = tmp_path / "ordinary"
    head = _committed_branch(ordinary)
    parent = _git(ordinary, "rev-parse", "HEAD^")
    gb_state = inspect_checkout(Checkout("seedsigner", ordinary, vcs="gitbutler"))

    assert gb_state.vcs == "gitbutler"
    assert gb_state.revision == parent
    assert gb_state.revision != head


def test_gitbutler_dirty_is_still_detected(tmp_path: Path):
    _, parents = _gitbutler_workspace(tmp_path)
    (tmp_path / "f").write_text("dirty")
    checkout = Checkout("jade", tmp_path)

    with pytest.raises(CheckoutError, match="dirty"):
        inspect_checkout(checkout)

    state = inspect_checkout(checkout, allow_dirty=True)
    assert state.dirty
    assert state.diff_sha256
    assert state.revision == parents[0]
    assert state.vcs == "gitbutler"


def test_gitbutler_tip_change_fails_a_parent_tip_pin(tmp_path: Path):
    _, parents = _gitbutler_workspace(tmp_path)
    first = inspect_checkout(Checkout("jade", tmp_path)).revision
    tree = _git(tmp_path, "rev-parse", "HEAD^{tree}")
    new_tip = _git(tmp_path, "commit-tree", tree, "-p", parents[0], "-m", "moved")
    _point_gitbutler_workspace(tmp_path, [new_tip], "workspace-moved")

    moved = inspect_checkout(Checkout("jade", tmp_path)).revision
    assert moved == new_tip
    assert moved != first
    with pytest.raises(CheckoutError, match="expected"):
        inspect_checkout(Checkout("jade", tmp_path, revision=first))


def _commit_all(path: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True)


def test_jade_lock_rewrite_is_a_known_byproduct(tmp_path: Path):
    _configure_git(tmp_path)
    (tmp_path / "dependencies.lock.esp32").write_text("version: 5.5.5\n")
    _commit_all(tmp_path, "base")
    (tmp_path / "dependencies.lock.esp32").write_text("version: 5.5.4\n")

    state = inspect_checkout(Checkout("jade", tmp_path))
    assert not state.dirty
    assert state.byproducts == ("dependencies.lock.esp32",)

    # The same file is an ordinary change in any other checkout.
    with pytest.raises(CheckoutError, match="dirty"):
        inspect_checkout(Checkout("seedsigner", tmp_path))

    # A byproduct does not excuse a real change beside it.
    (tmp_path / "other").write_text("x")
    with pytest.raises(CheckoutError, match="dirty"):
        inspect_checkout(Checkout("jade", tmp_path))


def test_coldcard_libngu_patch_is_a_known_byproduct_but_a_moved_commit_is_not(tmp_path: Path):
    libngu = tmp_path / "libngu"
    _committed_branch(libngu)
    repo = tmp_path / "coldcard"
    _configure_git(repo)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "protocol.file.allow=always", "submodule", "add", "-q",
         str(libngu), "external/libngu"],
        check=True,
    )
    _commit_all(repo, "base")
    sub = repo / "external" / "libngu"
    (sub / "f").write_text("patched")

    state = inspect_checkout(Checkout("coldcard", repo))
    assert not state.dirty
    assert state.byproducts == ("external/libngu",)

    _commit_all(sub, "moved")
    with pytest.raises(CheckoutError, match="dirty"):
        inspect_checkout(Checkout("coldcard", repo))
