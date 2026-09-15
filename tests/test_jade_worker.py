from __future__ import annotations

import base64
from pathlib import Path

from bip375_interop.jade_worker import JadeWorker


class FakeQemu:
    def __init__(self) -> None:
        self.terminated = False

    def poll(self):
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> None:
        return None


class FakeJade:
    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self.mnemonic: tuple[str, bool] | None = None
        self.calls: list[tuple[str, bytes]] = []
        self.registered_descriptors: list[tuple[str, str, str, dict]] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True

    def get_version_info(self) -> dict[str, str]:
        return {"version": "test"}

    def set_mnemonic(self, mnemonic: str, temporary_wallet: bool) -> None:
        self.mnemonic = (mnemonic, temporary_wallet)

    def sign_psbt(self, network: str, psbt: bytes) -> bytes:
        self.calls.append((network, psbt))
        return psbt + b"-jade"

    def register_descriptor(
        self, network: str, descriptor_name: str, descriptor_script: str, datavalues: dict
    ) -> bool:
        self.registered_descriptors.append((network, descriptor_name, descriptor_script, datavalues))
        return True


def test_jade_worker_keeps_one_seeded_connection_and_maps_regtest() -> None:
    jade = FakeJade()
    worker = JadeWorker(
        environ={"BIP375_JADE_ENDPOINT": "tcp:127.0.0.1:30121"},
        api_factory=lambda **_kwargs: jade,
    )
    request = {
        "suite": "bip375",
        "mnemonic": "published test mnemonic",
        "network": "regtest",
        "psbt": base64.b64encode(b"psbt\xfffixture").decode(),
    }

    first = worker.process(request)
    second = worker.process(request)

    assert jade.connected
    assert jade.mnemonic == ("published test mnemonic", True)
    assert jade.calls == [
        ("localtest", b"psbt\xfffixture"),
        ("localtest", b"psbt\xfffixture"),
    ]
    assert base64.b64decode(first["psbt"]) == b"psbt\xfffixture-jade"
    assert base64.b64decode(second["psbt"]) == b"psbt\xfffixture-jade"
    worker.close()
    assert jade.disconnected


def test_jade_worker_registers_descriptor_once_for_musig2_sp() -> None:
    jade = FakeJade()
    worker = JadeWorker(
        environ={"BIP375_JADE_ENDPOINT": "tcp:127.0.0.1:30121"},
        api_factory=lambda **_kwargs: jade,
    )
    request = {
        "suite": "musig2-sp",
        "mnemonic": "published test mnemonic",
        "network": "regtest",
        "descriptor": "tr(musig(...)/<0;1>/*)",
        "psbt": base64.b64encode(b"psbt\xfffixture").decode(),
    }

    worker.process(request)
    worker.process(request)

    assert jade.registered_descriptors == [
        ("localtest", "bip375-interop", "tr(musig(...)/<0;1>/*)", {}),
    ]
    assert jade.calls == [
        ("localtest", b"psbt\xfffixture"),
        ("localtest", b"psbt\xfffixture"),
    ]


def test_jade_worker_rejects_a_second_signer_seed() -> None:
    worker = JadeWorker(
        environ={"BIP375_JADE_ENDPOINT": "tcp:127.0.0.1:30121"},
        api_factory=lambda **_kwargs: FakeJade(),
    )
    request = {
        "suite": "bip375",
        "mnemonic": "first published mnemonic",
        "psbt": base64.b64encode(b"psbt\xfffixture").decode(),
    }
    worker.process(request)
    request["mnemonic"] = "second published mnemonic"

    try:
        worker.process(request)
    except Exception as exc:
        assert str(exc) == "a persistent Jade worker cannot change mnemonic"
    else:
        raise AssertionError("second mnemonic was accepted")


def test_jade_worker_starts_and_stops_native_qemu(tmp_path: Path) -> None:
    checkout = tmp_path / "jade"
    (checkout / "build").mkdir(parents=True)
    (checkout / "build" / "flash_image.bin").touch()
    (checkout / "build" / "qemu_efuse.bin").touch()
    qemu = FakeQemu()
    observed = {}

    def process_factory(argv, **kwargs):
        observed["argv"] = argv
        observed["cwd"] = kwargs["cwd"]
        return qemu

    worker = JadeWorker(
        environ={
            "BIP375_JADE_CHECKOUT": str(checkout),
            "BIP375_JADE_QEMU": "/tools/qemu-system-xtensa",
        },
        process_factory=process_factory,
    )

    endpoint = worker._start_qemu()

    assert endpoint.startswith("tcp:127.0.0.1:")
    assert observed["argv"][0] == "/tools/qemu-system-xtensa"
    assert any(argument.startswith("user,model=open_eth") for argument in observed["argv"])
    assert observed["cwd"] == checkout
    worker.close()
    assert qemu.terminated
