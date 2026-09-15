import sys
from pathlib import Path

import pytest

from bip375_interop.errors import CapabilityError
from bip375_interop.worker import WorkerClient


def test_persistent_worker_round_trip(tmp_path: Path):
    worker = WorkerClient(
        [sys.executable, str(Path(__file__).with_name("fake_worker.py"))],
        cwd=tmp_path,
    )
    hello = worker.start("bip375", "test-a", tmp_path / "instance")
    assert hello.backend == "fake"
    assert worker.process_psbt(b"psbt\xfffixture", "shares") == b"psbt\xfffixture"
    assert worker.process_psbt(b"psbt\xffsecond", "sign") == b"psbt\xffsecond"
    worker.stop()


def test_worker_rejects_unsupported_suite(tmp_path: Path):
    worker = WorkerClient(
        [sys.executable, str(Path(__file__).with_name("fake_worker.py"))],
        cwd=tmp_path,
    )
    with pytest.raises(CapabilityError, match="does not support"):
        worker.start("musig2-sp", "test-a", tmp_path / "instance")
