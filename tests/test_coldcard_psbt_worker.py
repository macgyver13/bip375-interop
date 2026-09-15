from __future__ import annotations

import base64
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


def test_coldcard_worker_rejects_unsupported_network() -> None:
    worker = ColdcardPsbtWorker(device_factory=lambda **_kwargs: FakeDevice(), packer=FakePacker)
    request = _request()
    request["network"] = "mainnet"

    try:
        worker.process(request)
    except Exception as exc:
        assert str(exc) == "Coldcard worker supports regtest or testnet only"
    else:
        raise AssertionError("mainnet was accepted")
