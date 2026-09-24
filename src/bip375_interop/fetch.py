"""Clone every checkout of a profile at its lock commit, from ``sources.yaml``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .config import _load_yaml, load_config
from .errors import InteropError

FETCHED_NAME = "interop.fetched.yaml"


class FetchError(InteropError):
    pass


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise FetchError(f"git {' '.join(args)} in {cwd} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def load_sources(path: Path) -> dict[str, dict[str, Any]]:
    """Each entry is a URL, or a mapping with ``url``, ``urls`` and ``submodules``."""

    return {
        name: {"url": value} if isinstance(value, str) else value
        for name, value in _load_yaml(path).items()
    }


def _init_submodules(repo: Path, source: dict[str, Any]) -> None:
    overrides = source.get("urls", {})
    wanted = source.get("submodules", [])
    if wanted == "all":
        for path, url in overrides.items():
            _git(repo, "config", f"submodule.{path}.url", url)
        _git(repo, "submodule", "update", "-q", "--init", "--recursive", "--depth", "1")
        return
    # Paths may be nested ("external/libngu/libs/bech32"): each is initialized from the
    # deepest submodule already initialized that contains it.
    done: list[str] = []
    for path in wanted:
        owner = max((d for d in done if path.startswith(d + "/")), key=len, default="")
        rel = path[len(owner) + 1:] if owner else path
        where = repo / owner if owner else repo
        if path in overrides:
            _git(where, "config", f"submodule.{rel}.url", overrides[path])
        _git(where, "submodule", "update", "-q", "--init", "--depth", "1", rel)
        done.append(path)


def _clone(dest: Path, source: dict[str, Any], rev: str) -> bool:
    """Put ``rev`` at ``dest``; return False when it is already there."""

    if dest.is_dir():
        if _git(dest, "rev-parse", "HEAD") != rev:
            raise FetchError(f"{dest} exists at another commit")
        return False
    # Built under a temporary name, so an interrupted fetch never leaves a half-made
    # checkout at the final path.
    partial = dest.with_name(dest.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    _git(partial, "init", "-q")
    _git(partial, "fetch", "-q", "--depth", "1", source["url"], rev)
    _git(partial, "checkout", "-q", "--detach", "FETCH_HEAD")
    _init_submodules(partial, source)
    partial.rename(dest)
    return True


def fetch(config_path: Path, sources_path: Path, checkouts_dir: Path) -> tuple[Path, list[str]]:
    """Clone the profile's checkouts and write a config that points at them."""

    config = load_config(config_path)
    sources = load_sources(sources_path)
    raw = _load_yaml(config_path)
    fetched = []
    for name, checkout in config.checkouts.items():
        rev = checkout.revision
        if not rev or "," in rev:
            raise FetchError(f"{name}: needs a single pinned commit in the lock, got {rev!r}")
        if name not in sources:
            raise FetchError(f"{name}: no entry in {sources_path}")
        dest = (checkouts_dir / f"{name}@{rev[:12]}").resolve()
        if _clone(dest, sources[name], rev):
            fetched.append(name)
        raw["checkouts"][name]["path"] = str(dest)
    out = config_path.parent / FETCHED_NAME
    header = f"# Written by `fetch` from {config_path.name}: checkouts cloned at the lock's commits.\n"
    out.write_text(header + yaml.safe_dump(raw, sort_keys=False))
    return out, fetched
