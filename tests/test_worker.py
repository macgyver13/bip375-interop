import json
import subprocess
import sys
from pathlib import Path

import pytest

from bip375_interop.errors import CapabilityError
from bip375_interop.worker import WorkerClient, WorkerProtocolError


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


@pytest.mark.parametrize("partial_reply", [False, True])
def test_start_times_out_and_reaps_silent_worker(tmp_path: Path, monkeypatch, partial_reply: bool):
    monkeypatch.setattr(WorkerClient, "RESPONSE_TIMEOUT", 0.1)
    script = (
        "import sys, threading\n"
        "sys.stdin.readline()\n"
        f"sys.stdout.write({('partial' if partial_reply else '')!r})\n"
        "sys.stdout.flush()\n"
        "threading.Event().wait()\n"
    )
    processes = []

    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    worker = WorkerClient([sys.executable, "-c", script], cwd=tmp_path, process_factory=spawn)
    with pytest.raises(WorkerProtocolError, match="timed out"):
        worker.start("bip375", "test-a", tmp_path / "instance")

    assert worker._process is None
    assert processes[0].poll() is not None


@pytest.mark.parametrize("reply, error", [
    ('{"ok": true, "result": []}', AttributeError),
    ('{"ok": true, "result": {"protocol_version": 1, "plain_bip375": true}}', KeyError),
    ('not json', WorkerProtocolError),
])
def test_failed_start_reaps_worker(tmp_path: Path, reply: str, error: type[Exception]):
    script = (
        "import sys, threading\n"
        "sys.stdin.readline()\n"
        f"print({reply!r}, flush=True)\n"
        "threading.Event().wait()\n"
    )
    processes = []

    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    worker = WorkerClient([sys.executable, "-c", script], cwd=tmp_path, process_factory=spawn)
    with pytest.raises(error):
        worker.start("bip375", "test-a", tmp_path / "instance")

    assert worker._process is None
    assert processes[0].poll() is not None


def test_failed_stderr_thread_start_reaps_worker(tmp_path: Path, monkeypatch):
    class FailedThread:
        ident = None

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread failed to start")

    monkeypatch.setattr("bip375_interop.worker.threading.Thread", FailedThread)
    processes = []

    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    worker = WorkerClient(
        [sys.executable, "-c", "import threading; threading.Event().wait()"],
        cwd=tmp_path,
        process_factory=spawn,
    )
    with pytest.raises(RuntimeError, match="thread failed to start"):
        worker.start("bip375", "test-a", tmp_path / "instance")

    assert worker._process is None
    assert processes[0].poll() is not None


def test_process_request_times_out_with_stdout_open(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(WorkerClient, "RESPONSE_TIMEOUT", 0.1)
    capabilities = json.dumps({
        "ok": True,
        "result": {"protocol_version": 1, "plain_bip375": True, "backend": "fake"},
    })
    script = (
        "import sys, threading\n"
        "sys.stdin.readline()\n"
        f"print({capabilities!r}, flush=True)\n"
        "sys.stdin.readline()\n"
        "threading.Event().wait()\n"
    )
    worker = WorkerClient([sys.executable, "-c", script], cwd=tmp_path)
    try:
        worker.start("bip375", "test-a", tmp_path / "instance")
        with pytest.raises(WorkerProtocolError, match="timed out"):
            worker.process_psbt(b"psbt\xfffixture", "shares")
    finally:
        worker.stop()
