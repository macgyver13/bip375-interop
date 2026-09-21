from pathlib import Path

from bip375_interop.cli import main


def _git_repo(path: Path) -> str:
    import subprocess

    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "f").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "c"], check=True)
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def test_pin_writes_lock_with_current_tips(tmp_path: Path):
    from bip375_interop.config import read_lock

    head = _git_repo(tmp_path / "embit")
    config_path = tmp_path / "interop.yaml"
    config_path.write_text(f"checkouts:\n  embit: {{path: {tmp_path / 'embit'}}}\n")

    assert main(["--config", str(config_path), "pin"]) == 0

    assert read_lock(tmp_path / "interop.lock") == {"embit": head}


def test_pin_refuses_a_dirty_checkout_even_with_allow_dirty(tmp_path: Path, capsys):
    _git_repo(tmp_path / "embit")
    (tmp_path / "embit" / "f").write_text("changed")
    config_path = tmp_path / "interop.yaml"
    config_path.write_text(f"checkouts:\n  embit: {{path: {tmp_path / 'embit'}}}\n")

    assert main(["--config", str(config_path), "--allow-dirty", "pin"]) == 2

    assert not (tmp_path / "interop.lock").exists()
    assert "dirty" in capsys.readouterr().err


def _gitbutler_workspace(path: Path) -> list[str]:
    import subprocess

    path.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    (path / "f").write_text("x")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "base"], check=True)
    tree = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    base = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    parents = [
        subprocess.check_output(
            ["git", "-C", str(path), "commit-tree", tree, "-p", base, "-m", name],
            text=True,
        ).strip()
        for name in ("tip-a", "tip-b")
    ]
    cmd = ["git", "-C", str(path), "commit-tree", tree]
    for parent in sorted(parents, reverse=True):
        cmd.extend(["-p", parent])
    cmd.extend(["-m", "workspace"])
    workspace = subprocess.check_output(cmd, text=True).strip()
    subprocess.run(
        ["git", "-C", str(path), "update-ref", "refs/heads/gitbutler/workspace", workspace],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "HEAD", "refs/heads/gitbutler/workspace"],
        check=True,
    )
    return parents


def test_pin_round_trips_gitbutler_parent_tips(tmp_path: Path):
    from bip375_interop.checkouts import inspect_checkout
    from bip375_interop.config import load_config, read_lock

    parents = _gitbutler_workspace(tmp_path / "jade")
    expected = ",".join(sorted(parents))
    config_path = tmp_path / "interop.yaml"
    config_path.write_text(f"checkouts:\n  jade: {{path: {tmp_path / 'jade'}}}\n")

    assert main(["--config", str(config_path), "pin"]) == 0

    assert read_lock(tmp_path / "interop.lock") == {"jade": expected}
    checkout = load_config(config_path).checkouts["jade"]
    assert checkout.revision == expected
    assert inspect_checkout(checkout).revision == expected
