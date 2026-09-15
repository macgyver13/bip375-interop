from __future__ import annotations

import base64

from bip375_interop.jade_worker import JadeWorker


class FakeJade:
    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self.mnemonic: tuple[str, bool] | None = None
        self.calls: list[tuple[str, bytes]] = []

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
