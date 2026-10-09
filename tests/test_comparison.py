import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bip375_interop import cli
from bip375_interop.comparison import compare_checkouts
from bip375_interop.config import write_lock
from bip375_interop.errors import ConfigurationError, InteropError
from bip375_interop.models import Checkout, HarnessConfig


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.PIPE).decode().strip()


def commit(path, content, subject):
    (path / "file").write_text(content)
    git(path, "add", "file")
    git(path, "commit", "-qm", subject)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    commit(path, "first", "first")
    return path


def compare(tmp_path, repo, baseline, name="live", vcs=None):
    lock = tmp_path / "baseline.lock"
    write_lock(lock, {name: baseline} if baseline else {})
    config = HarnessConfig(tmp_path / "artifacts", {name: Checkout(name, repo, "ignored-pin", vcs)})
    return compare_checkouts(config, lock)["checkouts"][0]


def test_matches_and_dirty_equal_commit(tmp_path, repo):
    base = git(repo, "rev-parse", "HEAD")
    row = compare(tmp_path, repo, base)
    assert row["status"] == "matches"
    assert (row["ahead"], row["behind"], row["dirty"]) == (0, 0, False)
    assert row["changed_files"] == row["uncommitted_files"] == row["commits"] == []
    (repo / "file").write_text("dirty")
    row = compare(tmp_path, repo, base)
    assert row["status"] == "matches"
    assert row["revision"] == base
    assert row["dirty"] and row["diff_sha256"]
    assert row["uncommitted_files"] == ["file"]


@pytest.mark.parametrize("direction", ["ahead", "behind", "diverged"])
def test_directional_history(tmp_path, repo, direction):
    first = git(repo, "rev-parse", "HEAD")
    second = commit(repo, "second", "second subject")
    baseline = first
    if direction == "behind":
        baseline = second
        git(repo, "checkout", "--detach", first)
    elif direction == "diverged":
        baseline = second
        git(repo, "checkout", "--detach", first)
        commit(repo, "other", "other subject")
    row = compare(tmp_path, repo, baseline)
    assert row["status"] == direction
    assert (row["ahead"], row["behind"]) == {"ahead": (1, 0), "behind": (0, 1), "diverged": (1, 1)}[direction]
    assert row["changed_files"] == ["file"]
    assert [item["subject"] for item in row["commits"]] == {"ahead": ["second subject"], "behind": [], "diverged": ["other subject"]}[direction]


def test_missing_object_preserves_local_edits(tmp_path, repo):
    (repo / "file").write_text("dirty")
    (repo / "new file").write_text("untracked")
    row = compare(tmp_path, repo, "a" * 40)
    assert row["status"] == "revision-differs"
    assert "no fetch" in row["error"]
    assert row["dirty"]
    assert row["uncommitted_files"] == ["file", "new file"]
    assert row["ahead"] is row["behind"] is None


def test_staged_unstaged_untracked_and_renamed_files(tmp_path, repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "mv", "file", "renamed file")
    (repo / "renamed file").write_text("unstaged")
    (repo / "new file").write_text("staged")
    git(repo, "add", "new file")
    (repo / "untracked\nfile").write_text("untracked")
    row = compare(tmp_path, repo, base)
    assert row["uncommitted_files"] == ["file", "new file", "renamed file", "untracked\nfile"]


def test_untracked_content_edit_changes_comparison_hash(tmp_path, repo):
    base = git(repo, "rev-parse", "HEAD")
    untracked = repo / "new file"
    untracked.write_text("first contents")
    first = compare(tmp_path, repo, base)
    untracked.write_text("edited contents")
    second = compare(tmp_path, repo, base)
    assert first["uncommitted_files"] == second["uncommitted_files"] == ["new file"]
    assert first["dirty"] and second["dirty"]
    assert first["diff_sha256"] != second["diff_sha256"]


def test_untracked_symlink_hashes_target_without_following_it(tmp_path, repo):
    base = git(repo, "rev-parse", "HEAD")
    symlink = repo / "link"
    symlink.symlink_to("missing-target")
    first = compare(tmp_path, repo, base)
    symlink.unlink()
    symlink.symlink_to("other-missing-target")
    second = compare(tmp_path, repo, base)
    assert first["status"] == second["status"] == "matches"
    assert first["diff_sha256"] != second["diff_sha256"]


def test_unpinned_and_known_byproducts(tmp_path, repo):
    (repo / "dependencies.lock.esp32").write_text("build lock")
    git(repo, "add", "dependencies.lock.esp32")
    git(repo, "commit", "-qm", "build lock")
    (repo / "dependencies.lock.esp32").write_text("changed lock")
    row = compare(tmp_path, repo, None, name="jade")
    assert row["status"] == "unpinned"
    assert row["baseline_revision"] is None
    assert not row["dirty"]
    assert row["uncommitted_files"] == []


def test_union_and_partial_failures(tmp_path, repo):
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    lock = tmp_path / "baseline.lock"
    base = git(repo, "rev-parse", "HEAD")
    write_lock(lock, {"baseline-only": base, "good": base})
    config = HarnessConfig(tmp_path, {
        "good": Checkout("good", repo), "bad": Checkout("bad", invalid),
        "missing": Checkout("missing", tmp_path / "absent"),
    })
    result = compare_checkouts(config, lock)
    assert result["baseline_lock"] == str(lock.resolve())
    assert result["lock_sha256"] == "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest()
    rows = {row["name"]: row for row in result["checkouts"]}
    assert {name: row["status"] for name, row in rows.items()} == {
        "baseline-only": "missing", "good": "matches", "bad": "error", "missing": "missing",
    }
    assert rows["baseline-only"]["path"] is None


def test_missing_lock_errors(tmp_path):
    with pytest.raises(ConfigurationError, match="baseline lock"):
        compare_checkouts(HarnessConfig(tmp_path, {}), tmp_path / "absent.lock")


def test_history_is_bounded_but_ahead_count_is_complete(tmp_path, repo):
    base = git(repo, "rev-parse", "HEAD")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    head = base
    for index in range(55):
        head = git(repo, "commit-tree", tree, "-p", head, "-m", f"commit {index}")
    git(repo, "update-ref", "HEAD", head)
    row = compare(tmp_path, repo, base)
    assert row["ahead"] == 55
    assert len(row["commits"]) == 50
    assert row["commits"][0]["revision"] == head


def test_shallow_history_is_explained_without_losing_tree_delta(tmp_path, repo):
    base = git(repo, "rev-parse", "HEAD")
    live = commit(repo, "live", "live")
    (repo / ".git" / "shallow").write_text(live + "\n")
    row = compare(tmp_path, repo, base)
    assert row["status"] == "revision-differs"
    assert "shallow" in row["error"]
    assert row["changed_files"] == ["file"]
    assert compare(tmp_path, repo, live)["status"] == "matches"


def test_unborn_checkout_keeps_local_files(tmp_path):
    repo = tmp_path / "unborn"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "new file").write_text("new")
    row = compare(tmp_path, repo, "a" * 40)
    assert row["revision"] == "UNBORN"
    assert row["dirty"]
    assert row["status"] == "revision-differs"
    assert row["uncommitted_files"] == ["new file"]


def test_gitbutler_uses_workspace_tree(tmp_path, repo):
    base = git(repo, "rev-parse", "HEAD")
    live = commit(repo, "live", "live subject")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    workspace = git(repo, "commit-tree", tree, "-p", base, "-p", live, "-m", "workspace")
    git(repo, "update-ref", "refs/heads/gitbutler/workspace", workspace)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/gitbutler/workspace")
    row = compare(tmp_path, repo, base)
    assert row["vcs"] == "gitbutler"
    assert "," in row["revision"]
    assert row["status"] == "ahead"
    assert row["changed_files"] == ["file"]
    assert "workspace HEAD" in row["error"]
    row = compare(tmp_path, repo, row["revision"])
    assert row["status"] == "matches"
    assert "multiple parent tips" in row["error"]


@pytest.mark.parametrize("equal", [True, False])
def test_gitbutler_single_tip_uses_effective_revision(tmp_path, repo, equal):
    base = git(repo, "rev-parse", "HEAD")
    live = base if equal else commit(repo, "live", "live subject")
    (repo / "workspace-only").write_text("synthetic workspace change")
    git(repo, "add", "workspace-only")
    tree = git(repo, "write-tree")
    workspace = git(repo, "commit-tree", tree, "-p", live, "-m", "workspace")
    git(repo, "update-ref", "refs/heads/gitbutler/workspace", workspace)
    git(repo, "symbolic-ref", "HEAD", "refs/heads/gitbutler/workspace")
    row = compare(tmp_path, repo, base)
    assert row["revision"] == live
    assert row["status"] == ("matches" if equal else "ahead")
    assert (row["ahead"], row["behind"]) == (0 if equal else 1, 0)
    assert row["commits"] == ([] if equal else [{"revision": live, "subject": "live subject"}])
    assert row["changed_files"] == ([] if equal else ["file"])
    assert row["error"] is None


@pytest.mark.skipif(shutil.which("jj") is None, reason="jj is not installed")
def test_jj_compares_working_revision_not_git_head(tmp_path):
    repo = tmp_path / "jj-repo"
    def jj(*args):
        return subprocess.check_output(["jj", "-R", str(repo), *args], stderr=subprocess.PIPE).decode().strip()
    subprocess.run(["jj", "git", "init", "--colocate", str(repo)], check=True, capture_output=True)
    (repo / "file").write_text("first")
    jj("describe", "-m", "baseline")
    baseline = jj("log", "-r", "@", "--no-graph", "-T", "commit_id")
    row = compare(tmp_path, repo, baseline)
    assert row["status"] == "matches"
    assert not row["dirty"]
    assert row["diff_sha256"] is None
    assert row["uncommitted_files"] == []
    jj("new")
    (repo / "file").write_text("live")
    jj("describe", "-m", "working edits")
    row = compare(tmp_path, repo, baseline)
    assert row["vcs"] == "jj"
    assert row["revision"] != git(repo, "rev-parse", "HEAD")
    assert row["status"] == "ahead"
    assert (row["ahead"], row["behind"]) == (1, 0)
    assert row["changed_files"] == ["file"]
    assert not row["dirty"]
    assert row["diff_sha256"] is None
    assert row["uncommitted_files"] == []
    assert row["commits"][0]["subject"] == "working edits"
    row = compare(tmp_path, repo, "a" * 40)
    assert row["status"] == "revision-differs"
    assert not row["dirty"]
    assert row["diff_sha256"] is None
    assert row["uncommitted_files"] == []


def test_compare_cli_dirty_and_missing_lock(tmp_path, repo, capsys):
    base = git(repo, "rev-parse", "HEAD")
    config = tmp_path / "interop.yaml"
    config.write_text(f"checkouts:\n  live: {{path: {repo}, revision: ignored-pin}}\n")
    lock = tmp_path / "baseline.lock"
    write_lock(lock, {"live": base})
    (repo / "file").write_text("dirty")
    args = ["--config", str(config), "compare", "--baseline-lock", str(lock)]
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["checkouts"][0]["dirty"]
    lock.unlink()
    assert cli.main(args) == 2
    assert "baseline lock" in capsys.readouterr().err


def mock_check(monkeypatch, tmp_path):
    config_path = tmp_path / "interop.yaml"
    config_path.write_text(f"artifact_root: {tmp_path / 'artifacts'}\n")
    scenario = SimpleNamespace(name="case", validators=())
    entry = SimpleNamespace(scenario=scenario, path=tmp_path / "case.yaml",
                            runnable_generated=True, reason=None)
    monkeypatch.setattr(cli, "discover", lambda _: [entry])
    monkeypatch.setattr(cli, "select", lambda *args: [entry])
    monkeypatch.setattr(cli, "changed_files", lambda *args: ())
    monkeypatch.setattr(cli, "run_preflight", lambda *args: [])
    def run(*args, **kwargs):
        return tmp_path / "final.psbt", tmp_path / "case.json", 1
    monkeypatch.setattr(cli, "_run_generated_scenario", run)
    monkeypatch.setattr(cli, "case_result_for_run", lambda *args, **kwargs: cli.CaseResult("case", "passed"))
    return ["--config", str(config_path), "check"]


def test_check_persists_pre_test_snapshot(monkeypatch, tmp_path, repo, capsys):
    args = mock_check(monkeypatch, tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    lock = tmp_path / "baseline.lock"
    write_lock(lock, {"live": base})
    config = HarnessConfig(tmp_path / "artifacts", {"live": Checkout("live", repo)})
    monkeypatch.setattr(cli, "load_config", lambda _: config)
    def run(*args, **kwargs):
        commit(repo, "changed during test", "test side effect")
        return tmp_path / "final.psbt", tmp_path / "case.json", 1
    monkeypatch.setattr(cli, "_run_generated_scenario", run)
    assert cli.main([*args, "--baseline-lock", str(lock), "--progress-json"]) == 0
    output = capsys.readouterr()
    summary = json.loads(output.out)
    events = [json.loads(line) for line in output.err.splitlines()]
    assert [event["stage"] for event in events] == ["baseline-comparison", "preflight", "case-start", "case-done"]
    assert events[0]["comparison"] == summary["baseline_comparison"]
    saved = json.loads(Path(summary["manifest"]).read_text())
    assert saved["baseline_comparison"] == summary["baseline_comparison"]
    assert saved["baseline_comparison"]["checkouts"][0]["revision"] == base
    assert git(repo, "rev-parse", "HEAD") != base


def test_dry_run_snapshot_and_no_flag_behavior(monkeypatch, tmp_path, capsys):
    args = mock_check(monkeypatch, tmp_path)
    lock = tmp_path / "baseline.lock"
    write_lock(lock, {})
    assert cli.main([*args, "--dry-run", "--baseline-lock", str(lock)]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert output.err == ""
    assert payload["baseline_comparison"]["checkouts"] == []
    assert cli.main([*args, "--dry-run"]) == 0
    without = json.loads(capsys.readouterr().out)
    assert "baseline_comparison" not in without
    assert without["cases"] == payload["cases"]
    assert cli.main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert "baseline_comparison" not in summary
    assert "baseline_comparison" not in json.loads(Path(summary["manifest"]).read_text())


def test_comparison_does_not_bypass_preflight(monkeypatch, tmp_path, capsys):
    args = mock_check(monkeypatch, tmp_path)
    lock = tmp_path / "baseline.lock"
    write_lock(lock, {})
    def reject(*args):
        raise InteropError("strict preflight rejected checkout")
    monkeypatch.setattr(cli, "run_preflight", reject)
    assert cli.main([*args, "--baseline-lock", str(lock), "--progress-json"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    lines = output.err.splitlines()
    assert json.loads(lines[0]) == {
        "stage": "baseline-comparison",
        "comparison": compare_checkouts(HarnessConfig(tmp_path, {}), lock),
    }
    assert json.loads(lines[1])["stage"] == "preflight"
    assert "strict preflight" in lines[2]
    assert not (tmp_path / "artifacts").exists()


def test_dry_run_emits_comparison_only_with_progress_flag(monkeypatch, tmp_path, capsys):
    args = mock_check(monkeypatch, tmp_path)
    lock = tmp_path / "baseline.lock"
    write_lock(lock, {})
    assert cli.main([*args, "--dry-run", "--baseline-lock", str(lock), "--progress-json"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert json.loads(output.err) == {
        "stage": "baseline-comparison", "comparison": payload["baseline_comparison"],
    }


def test_check_missing_baseline_aborts_before_tests(monkeypatch, tmp_path, capsys):
    args = mock_check(monkeypatch, tmp_path)
    def unexpected(*args):
        pytest.fail("preflight must not run when baseline is missing")
    monkeypatch.setattr(cli, "run_preflight", unexpected)
    assert cli.main([*args, "--baseline-lock", str(tmp_path / "missing.lock")]) == 2
    assert "baseline lock" in capsys.readouterr().err
    assert not (tmp_path / "artifacts").exists()
