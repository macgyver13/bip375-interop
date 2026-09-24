import subprocess
from pathlib import Path

import pytest
import yaml

from bip375_interop.checkouts import inspect_checkout
from bip375_interop.cli import main
from bip375_interop.config import load_config


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        _git(path, *args)
    for name, text in files.items():
        (path / name).write_text(text)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "c")
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def _allow_file_submodules(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "protocol.file.allow")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")


def _profile(tmp_path: Path, pins: dict[str, str], sources: dict) -> tuple[Path, list[str]]:
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "interop.yaml").write_text(yaml.safe_dump({
        "suites": ["bip375"],
        "checkouts": {name: {"path": f"~/src/{name}", "revision": None} for name in pins},
    }))
    (profile / "interop.lock").write_text(yaml.safe_dump({"checkouts": pins}))
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump(sources))
    args = [
        "--config", str(profile / "interop.yaml"), "fetch",
        "--sources", str(tmp_path / "sources.yaml"), "--checkouts-dir", str(tmp_path / ".checkouts"),
    ]
    return profile, args


def test_fetch_clones_the_lock_commit_and_writes_a_config(tmp_path: Path):
    upstream = tmp_path / "upstream"
    first = _repo(upstream, {"f": "one"})
    (upstream / "f").write_text("two")
    _git(upstream, "commit", "-q", "-am", "moved on")
    profile, args = _profile(tmp_path, {"embit": first}, {"embit": f"file://{upstream}"})

    assert main(args) == 0

    fetched = profile / "interop.fetched.yaml"
    config = load_config(fetched)
    assert config.checkouts["embit"].path == (tmp_path / ".checkouts" / f"embit@{first[:12]}").resolve()
    assert config.suites == ("bip375",)
    state = inspect_checkout(config.checkouts["embit"])
    assert state.revision == first and not state.dirty
    assert (config.checkouts["embit"].path / "f").read_text() == "one"


def test_fetch_is_idempotent_and_refuses_a_moved_checkout(tmp_path: Path, capsys):
    rev = _repo(tmp_path / "upstream", {"f": "x"})
    _, args = _profile(tmp_path, {"embit": rev}, {"embit": f"file://{tmp_path / 'upstream'}"})
    assert main(args) == 0
    capsys.readouterr()

    assert main(args) == 0
    assert '"fetched": []' in capsys.readouterr().out

    dest = tmp_path / ".checkouts" / f"embit@{rev[:12]}"
    (dest / "f").write_text("y")
    _git(dest, "commit", "-q", "-am", "local")
    assert main(args) == 2
    assert "another commit" in capsys.readouterr().err


def test_fetch_inits_only_listed_submodules_with_url_overrides(tmp_path: Path):
    inner = tmp_path / "inner"
    _repo(inner, {"i": "inner"})
    lib = tmp_path / "lib-upstream"
    _repo(lib, {"l": "lib"})
    _git(lib, "-c", "protocol.file.allow=always", "submodule", "add", "-q", f"file://{inner}", "libs/inner")
    _git(lib, "commit", "-q", "-m", "inner")
    fork = tmp_path / "lib-fork"
    subprocess.run(["git", "clone", "-q", str(lib), str(fork)], check=True)
    skipped = tmp_path / "skipped"
    _repo(skipped, {"s": "s"})
    top = tmp_path / "top"
    _repo(top, {"t": "t"})
    for url, path in ((lib, "external/lib"), (skipped, "external/skipped")):
        _git(top, "-c", "protocol.file.allow=always", "submodule", "add", "-q", f"file://{url}", path)
    _git(top, "commit", "-q", "-m", "subs")
    rev = _git(top, "rev-parse", "HEAD")
    # The fork alone may have the commits: point the upstream URL nowhere.
    _git(top, "config", "-f", ".gitmodules", "submodule.external/lib.url", "file:///nonexistent")
    _git(top, "commit", "-q", "-am", "upstream gone")
    rev = _git(top, "rev-parse", "HEAD")
    _, args = _profile(tmp_path, {"coldcard": rev}, {"coldcard": {
        "url": f"file://{top}",
        "urls": {"external/lib": f"file://{fork}"},
        "submodules": ["external/lib", "external/lib/libs/inner"],
    }})

    assert main(args) == 0

    dest = tmp_path / ".checkouts" / f"coldcard@{rev[:12]}"
    assert (dest / "external/lib/l").read_text() == "lib"
    assert (dest / "external/lib/libs/inner/i").read_text() == "inner"
    assert not (dest / "external/skipped/s").exists()


def test_fetch_needs_a_source_for_every_checkout(tmp_path: Path, capsys):
    rev = _repo(tmp_path / "upstream", {"f": "x"})
    _, args = _profile(tmp_path, {"embit": rev, "jade": rev}, {"embit": f"file://{tmp_path / 'upstream'}"})

    assert main(args) == 2

    assert "jade: no entry" in capsys.readouterr().err
