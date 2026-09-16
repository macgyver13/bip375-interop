import sys
from pathlib import Path

import pytest

from bip375_interop.errors import CapabilityError
from bip375_interop.worker import WorkerClient


def test_persistent_worker_round_trip(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    worker = WorkerClient(
        [sys.executable, str(Path(__file__).with_name("fake_worker.py"))],
        cwd=tmp_path,
    )
    hello = worker.start("bip375", "test-a", instance_dir)
    assert hello.backend == "fake"
    assert worker.process_psbt(b"psbt\xfffixture", "shares").psbt == b"psbt\xfffixture"
    assert worker.process_psbt(b"psbt\xffsecond", "sign").psbt == b"psbt\xffsecond"
    worker.stop()
    assert (instance_dir / "worker.stderr.log").is_file()


def test_worker_drains_stderr_into_instance_dir_log(tmp_path: Path):
    instance_dir = tmp_path / "instance"
    worker = WorkerClient(
        [sys.executable, str(Path(__file__).with_name("fake_worker_stderr.py"))],
        cwd=tmp_path,
    )
    worker.start("bip375", "test-a", instance_dir)
    worker.process_psbt(b"psbt\xfffixture", "shares")
    worker.stop()

    log_text = (instance_dir / "worker.stderr.log").read_text()
    assert "booting fake worker" in log_text
    assert "handling capabilities" in log_text
    assert "handling process_psbt" in log_text


def test_worker_rejects_unsupported_suite(tmp_path: Path):
    worker = WorkerClient(
        [sys.executable, str(Path(__file__).with_name("fake_worker.py"))],
        cwd=tmp_path,
    )
    with pytest.raises(CapabilityError, match="does not support"):
        worker.start("musig2-sp", "test-a", tmp_path / "instance")
