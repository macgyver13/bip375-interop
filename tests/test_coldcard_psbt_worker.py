from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from bip375_interop.coldcard_psbt_worker import ColdcardPsbtWorker


class FakePacker:
    @staticmethod
    def sign_transaction(length: int, digest: bytes, finalize: bool) -> tuple:
        return ("sign", length, digest, finalize)

    @staticmethod
    def sim_keypress(key: bytes) -> tuple:
        return ("key", key)

    @staticmethod
    def get_signed_txn() -> tuple:
        return ("get-signed",)

    @staticmethod
    def version() -> tuple:
        return ("version",)

    @staticmethod
    def miniscript_enroll(length: int, digest: bytes) -> tuple:
        return ("enroll", length, digest)


class FakeDevice:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.encrypted = False

    def upload_file(self, psbt: bytes) -> tuple[int, bytes]:
        self.calls.append(("upload", psbt))
        return len(psbt), b"digest"

    def send_recv(self, command, **kwargs):
        self.calls.append(("send", command, kwargs))
        if command == ("get-signed",):
            return (17, b"signed-digest")
        if isinstance(command, bytes) and b"sim_display.story" in command:
            return "Sign Transaction\0Details go here".encode()
        return None

    def download_file(self, length: int, digest: bytes) -> bytes:
        self.calls.append(("download", length, digest))
        return b"psbt\xffcoldcard-signed"

    def start_encryption(self) -> None:
        self.encrypted = True

    def check_mitm(self) -> None:
        return None


def _request(mnemonic: str = "published test mnemonic") -> dict[str, str]:
    return {
        "suite": "bip375",
        "mnemonic": mnemonic,
        "network": "regtest",
        "psbt": base64.b64encode(b"psbt\xfffixture").decode(),
    }


def test_coldcard_worker_reuses_one_seeded_device() -> None:
    device = FakeDevice()
    worker = ColdcardPsbtWorker(device_factory=lambda **_kwargs: device, packer=FakePacker)
    worker._device = device
    worker._seed_script = Path("/tmp/set_seed.py")

    first = worker.process(_request())
    second = worker.process(_request())

    assert device.encrypted
    assert sum(call[0] == "upload" for call in device.calls) == 2
    assert sum(call[1] == ("key", b"y") for call in device.calls if call[0] == "send") == 2
    assert sum(call[1] == ("key", b"x") for call in device.calls if call[0] == "send") == 2
    assert base64.b64decode(first["psbt"]) == b"psbt\xffcoldcard-signed"
    assert base64.b64decode(second["psbt"]) == b"psbt\xffcoldcard-signed"


def test_coldcard_worker_enrolls_descriptor_once_for_musig2_sp() -> None:
    device = FakeDevice()
    worker = ColdcardPsbtWorker(device_factory=lambda **_kwargs: device, packer=FakePacker)
    worker._device = device
    worker._seed_script = Path("/tmp/set_seed.py")
    request = _request()
    request["suite"] = "musig2-sp"
    request["descriptor"] = "tr(musig(...)/<0;1>/*)"

    worker.process(request)
    worker.process(request)

    enroll_uploads = [call for call in device.calls if call[0] == "upload" and b"bip375-interop" in call[1]]
    assert len(enroll_uploads) == 1
    assert sum(call[1][0] == "enroll" for call in device.calls if call[0] == "send") == 1


def test_coldcard_worker_rejects_a_second_signer_seed() -> None:
    device = FakeDevice()
    worker = ColdcardPsbtWorker(device_factory=lambda **_kwargs: device, packer=FakePacker)
    worker._device = device
    worker._seed_script = Path("/tmp/set_seed.py")
    worker.process(_request())

    try:
        worker.process(_request("another published mnemonic"))
    except Exception as exc:
        assert str(exc) == "a persistent Coldcard worker cannot change mnemonic"
    else:
        raise AssertionError("second mnemonic was accepted")


def test_coldcard_worker_ignores_network_the_simulator_cannot_apply() -> None:
    device = FakeDevice()
    worker = ColdcardPsbtWorker(device_factory=lambda **_kwargs: device, packer=FakePacker)
    worker._device = device
    worker._seed_script = Path("/tmp/set_seed.py")
    request = _request()
    request["network"] = "mainnet"

    result = worker.process(request)

    assert base64.b64decode(result["psbt"]) == b"psbt\xffcoldcard-signed"


def test_coldcard_worker_captures_story_before_approving() -> None:
    device = FakeDevice()
    worker = ColdcardPsbtWorker(device_factory=lambda **_kwargs: device, packer=FakePacker)
    worker._device = device
    worker._seed_script = Path("/tmp/set_seed.py")

    result = worker.process(_request())

    assert result["story"] == {"title": "Sign Transaction", "body": "Details go here"}
    story_call_index = next(
        i for i, call in enumerate(device.calls)
        if call[0] == "send" and isinstance(call[1], bytes) and b"sim_display.story" in call[1]
    )
    approve_call_index = next(
        i for i, call in enumerate(device.calls) if call[1] == ("key", b"y")
    )
    assert story_call_index < approve_call_index


def test_coldcard_worker_redirects_simulator_io_to_instance_dir(tmp_path: Path) -> None:
    checkout = tmp_path / "coldcard"
    (checkout / "unix").mkdir(parents=True)
    (checkout / "unix" / "simulator.py").touch()
    (checkout / "testing" / "devtest").mkdir(parents=True)
    (checkout / "testing" / "devtest" / "set_seed.py").touch()
    python = tmp_path / "python"
    python.touch()
    instance_dir = tmp_path / "instance"
    device = FakeDevice()

    class FakeProcess:
        pid = 999991

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            pass

    socket_path = Path(f"/tmp/ckcc-simulator-{FakeProcess.pid}.sock")
    socket_path.touch()
    observed: dict = {}

    def process_factory(argv, **kwargs):
        observed.update(kwargs)
        return FakeProcess()

    worker = ColdcardPsbtWorker(
        environ={
            "BIP375_COLDCARD_CHECKOUT": str(checkout),
            "BIP375_COLDCARD_PYTHON": str(python),
            "BIP375_WORKER_INSTANCE_DIR": str(instance_dir),
        },
        device_factory=lambda **_kwargs: device,
        packer=FakePacker,
        process_factory=process_factory,
    )
    try:
        worker._start_simulator()
        assert observed["stdout"] not in (subprocess.DEVNULL, None)
        assert observed["stderr"] not in (subprocess.DEVNULL, None)
        assert (instance_dir / "simulator.stdout.log").is_file()
        assert (instance_dir / "simulator.stderr.log").is_file()
    finally:
        worker.close()
        socket_path.unlink(missing_ok=True)
