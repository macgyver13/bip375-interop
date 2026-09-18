import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "musig2-regtest.sh"


def _run(*args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, env={**os.environ, **env}
    )


def test_rejects_an_unknown_key_architecture():
    result = _run("sideways")

    assert result.returncode == 2
    assert "aggregate-then-derive" in result.stderr


def test_reports_a_missing_bitcoind_before_doing_any_work(tmp_path: Path):
    result = _run("aggregate-then-derive", BITCOIND="/nonexistent/bitcoind", WORK_DIR=str(tmp_path / "w"))

    assert result.returncode == 2
    assert "BITCOIND" in result.stderr
    assert not (tmp_path / "w" / "wallet.toml").exists()
