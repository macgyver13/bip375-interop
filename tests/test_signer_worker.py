from __future__ import annotations

import base64
import io
import json
from types import SimpleNamespace

from bip375_interop.signer_worker import (
    BitSagaWorker,
    SeedSignerWorker,
    handle_request,
    serve,
)


class FakePSBT:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    @classmethod
    def parse(cls, payload: bytes):
        if payload == b"bad":
            raise ValueError("bad psbt")
        return cls(payload)

    def serialize(self) -> bytes:
        return self.payload

    def sign_with(self, root) -> int:
        self.payload += b"-signed-" + root
        return 1


class FakeHDKey:
    @classmethod
    def from_seed(cls, seed: bytes) -> bytes:
        return b"root-" + seed


class FakeSession:
    created = 0

    def __init__(self) -> None:
        type(self).created += 1
        self.round = 0

    def advance(self, psbt: FakePSBT, root: bytes):
        self.round += 1
        psbt.payload += b"-r" + str(self.round).encode() + b"-" + root
        return SimpleNamespace(stage=FakeMusig.SHARES if self.round == 1 else FakeMusig.SIGNED)


class FakeMusig:
    SHARES = 1
    SIGNED = 2
    Session = FakeSession


def fake_loader(name: str):
    modules = {
        "embit.silent_payments": SimpleNamespace(SilentPaymentsPSBT=FakePSBT),
        "embit.silent_payments.psbt": SimpleNamespace(SilentPaymentsPSBT=FakePSBT),
        "embit.bip32": SimpleNamespace(HDKey=FakeHDKey),
        "embit.bip39": SimpleNamespace(mnemonic_to_seed=lambda words: words.encode()),
        "seedsigner.helpers.musig2_psbt": FakeMusig,
    }
    return modules[name]


def _request(**values):
    request = {
        "id": "request-1",
        "op": "process_psbt",
        "suite": "bip375",
        "psbt": base64.b64encode(b"psbt").decode(),
        "mnemonic": "test words",
    }
    request.update(values)
    return request


def test_json_lines_server_emits_one_correlated_response_per_line() -> None:
    input_stream = io.StringIO(
        "not-json\n" + json.dumps({"id": 7, "op": "capabilities"}) + "\n"
    )
    output_stream = io.StringIO()

    serve(SeedSignerWorker(fake_loader), input_stream, output_stream)

    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert responses[0]["error"]["code"] == "invalid_json"
    assert responses[1]["id"] == 7
    assert responses[1]["result"]["protocol_version"] == 1
    assert responses[1]["result"]["plain_bip375"] is True
    assert responses[1]["result"]["musig2_sp"] is False


def test_upstream_worker_processes_plain_bip375_and_rejects_musig2() -> None:
    worker = SeedSignerWorker(fake_loader)

    signed = handle_request(worker, _request())
    unsupported = handle_request(worker, _request(suite="musig2-sp"))

    assert signed["ok"] is True
    assert base64.b64decode(signed["result"]["psbt"]) == b"psbt-signed-root-test words"
    assert signed["result"]["signatures_added"] == 1
    assert unsupported["ok"] is False
    assert unsupported["error"]["code"] == "unsupported"


def test_bitsaga_worker_retains_session_across_musig2_rounds() -> None:
    FakeSession.created = 0
    worker = BitSagaWorker(fake_loader)
    first = handle_request(
        worker,
        _request(suite="musig2-sp", session_id="alice"),
    )
    second = handle_request(
        worker,
        _request(
            suite="musig2-sp",
            session_id="alice",
            psbt=first["result"]["psbt"],
        ),
    )

    assert first["result"]["stage"] == "shares"
    assert second["result"]["stage"] == "signed"
    assert FakeSession.created == 1
    assert "alice" not in worker._sessions


def test_bitsaga_worker_rejects_derive_then_aggregate_explicitly() -> None:
    response = handle_request(
        BitSagaWorker(fake_loader),
        _request(
            suite="musig2-sp",
            session_id="alice",
            key_architecture="derive-then-aggregate",
        ),
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "unsupported"
    assert "aggregate-then-derive" in response["error"]["message"]


def test_missing_sibling_runtime_is_discovered_not_faked() -> None:
    def missing(_name: str):
        raise ImportError("embit extension unavailable")

    worker = BitSagaWorker(missing)
    capabilities = handle_request(worker, {"id": 1, "op": "capabilities"})
    processing = handle_request(
        worker, _request(suite="musig2-sp", session_id="alice")
    )

    assert capabilities["result"]["plain_bip375"] is False
    assert capabilities["result"]["musig2_sp"] is False
    assert "unavailable" in capabilities["result"]["unavailable_reason"]
    assert processing["error"]["code"] == "unsupported"


def test_invalid_psbt_returns_protocol_error_without_traceback() -> None:
    response = handle_request(
        SeedSignerWorker(fake_loader),
        _request(psbt=base64.b64encode(b"bad").decode()),
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_psbt"
    assert "traceback" not in response["error"]["message"].lower()
