"""Run Coldcard's native simulator tests in a disposable checkout copy."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Sequence

from bip375_interop.suites import SuiteName


MUSIG2_FIXTURES = (
    "desc-musig-sp-demo.txt",
    "musig2-sp-round1-in.psbt",
    "musig2-sp-cosigner-contrib.psbt",
)
TEST_MODULES = {
    SuiteName.BIP375: (
        "test_bip375_vectors.py",
        "test_silentpayments.py",
        "test_bip352_vectors.py",
    ),
    SuiteName.MUSIG2_SP: (
        "test_musig2_silentpayments.py",
        "test_musig2_sp_signers.py",
    ),
}


WorkerRunner = Callable[..., subprocess.CompletedProcess]


def _ignore_copy(_directory: str, names: list[str]) -> set[str]:
    disposable = {".git", ".jj", "ENV", "__pycache__", ".pytest_cache", ".ruff_cache"}
    return disposable.intersection(names)


def _copy_checkout(source_checkout: Path, destination: Path) -> Path:
    copied_checkout = destination / "coldcard-firmware"
    shutil.copytree(
        source_checkout,
        copied_checkout,
        symlinks=True,
        ignore=_ignore_copy,
    )
    return copied_checkout


def _overlay_fixtures(copied_checkout: Path, fixture_dir: Path, suite: SuiteName) -> None:
    if suite is not SuiteName.MUSIG2_SP:
        return
    data_dir = copied_checkout / "testing" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in MUSIG2_FIXTURES:
        source = fixture_dir / name
        if not source.is_file():
            raise ValueError(f"Coldcard fixture is missing: {source}")
        shutil.copy2(source, data_dir / name)


def _isolate_simulators(copied_checkout: Path, runtime_dir: Path) -> None:
    """Give the disposable runner a private directory for segregated simulators."""
    for relative_path in ("testing/run_sim_tests.py", "unix/simulator.py"):
        path = copied_checkout / relative_path
        source = path.read_text()
        source = source.replace('"/tmp/cc-simulators"', repr(str(runtime_dir)))
        if relative_path == "testing/run_sim_tests.py":
            source = source.replace(
                "ColdcardSimulator(sim_args, segregate=True)",
                "ColdcardSimulator(sim_args, headless=args.headless, segregate=True)",
            )
        path.write_text(source)


def _secp256k1_library() -> str | None:
    """Locate Homebrew's library when the test suite has no configured path."""

    configured = os.environ.get("PYSECP_SO")
    if configured:
        return configured
    for candidate in (
        "/opt/homebrew/lib/libsecp256k1.dylib",
        "/usr/local/lib/libsecp256k1.dylib",
        "/usr/lib/libsecp256k1.so",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _run_test_module(
    runner: WorkerRunner,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess:
    """Run one vendor module and reject its zero-exit failure report."""
    result = runner(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    if result.returncode or "\nFAILED " in stdout:
        return subprocess.CompletedProcess(result.args, 1, stdout, stderr)
    return result


def run_worker(
    source_checkout: str | Path,
    fixture_dir: str | Path,
    artifact_dir: str | Path,
    suite: SuiteName,
    python_executable: str | Path,
    *,
    pytest_filter: str | None = None,
    runner: WorkerRunner = subprocess.run,
) -> subprocess.CompletedProcess:
    """Copy, overlay, and run one native Coldcard suite."""
    source_checkout = Path(source_checkout).expanduser().resolve()
    fixture_dir = Path(fixture_dir).expanduser().resolve()
    artifact_dir = Path(artifact_dir).expanduser().resolve()
    if artifact_dir.is_relative_to(source_checkout):
        raise ValueError(
            f"Coldcard artifact directory must be outside the source checkout: {artifact_dir}"
        )
    artifact_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bip375-coldcard-") as temporary:
        temporary_path = Path(temporary)
        copied_checkout = _copy_checkout(source_checkout, temporary_path)
        _overlay_fixtures(copied_checkout, fixture_dir, suite)
        _isolate_simulators(copied_checkout, temporary_path / "simulators")

        env = os.environ.copy()
        python_directory = str(Path(python_executable).expanduser().parent)
        env["PATH"] = python_directory + os.pathsep + env.get("PATH", "")
        env["CC_SP_OUT"] = str(artifact_dir)
        secp256k1_library = _secp256k1_library()
        if secp256k1_library:
            env["PYSECP_SO"] = secp256k1_library
        result: subprocess.CompletedProcess | None = None
        for module in TEST_MODULES[suite]:
            command = [
                str(python_executable),
                "run_sim_tests.py",
                "--module",
                module,
                "--headless",
                "--multiproc",
                "--num-proc",
                "1",
            ]
            if pytest_filter:
                command.extend(("--pytest-k", pytest_filter))
            result = _run_test_module(
                runner,
                command,
                cwd=copied_checkout / "testing",
                env=env,
            )
            if result.returncode:
                return result
        assert result is not None
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-checkout", required=True)
    parser.add_argument("--fixture-dir", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--suite", required=True, choices=tuple(TEST_MODULES))
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--pytest-filter")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    completed = run_worker(
        args.source_checkout,
        args.fixture_dir,
        args.artifact_dir,
        SuiteName(args.suite),
        args.python_executable,
        pytest_filter=args.pytest_filter,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
