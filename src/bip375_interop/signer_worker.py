"""Persistent JSON-lines workers for software signer checkouts.

The protocol intentionally keeps one request and one response on each line so a
coordinator can retain signer-local nonce state across MuSig2 rounds without
embedding either SeedSigner fork in the coordinator process.
"""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, TextIO


PROTOCOL_VERSION = 1


class WorkerRequestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuntimeCapabilities:
    backend: str
    plain_bip375: bool
    musig2_sp: bool
    persistent: bool
    unavailable_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "backend": self.backend,
            "plain_bip375": self.plain_bip375,
            "musig2_sp": self.musig2_sp,
            "persistent": self.persistent,
            "unavailable_reason": self.unavailable_reason,
        }


Loader = Callable[[str], Any]


class SeedSignerWorker:
    """Use upstream SeedSigner's existing embit signer for plain BIP-375."""

    backend = "seedsigner"

    def __init__(self, loader: Loader = importlib.import_module) -> None:
        self._loader = loader
        self._modules: tuple[Any, Any, Any] | None = None
        self._load_error: str | None = None
        try:
            self._modules = self._load_modules()
        except (ImportError, AttributeError) as exc:
            self._load_error = str(exc)

    def _load_modules(self) -> tuple[Any, Any, Any]:
        sp = self._loader("embit.silent_payments")
        bip32 = self._loader("embit.bip32")
        bip39 = self._loader("embit.bip39")
        getattr(sp, "SilentPaymentsPSBT")
        getattr(bip32, "HDKey")
        getattr(bip39, "mnemonic_to_seed")
        return sp, bip32, bip39

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            backend=self.backend,
            plain_bip375=self._modules is not None,
            musig2_sp=False,
            persistent=True,
            unavailable_reason=self._load_error,
        )

    def process(self, request: Mapping[str, Any]) -> dict[str, Any]:
        suite = _required_string(request, "suite")
        if suite != "bip375":
            raise WorkerRequestError(
                "unsupported", "upstream SeedSigner does not support MuSig2-SP"
            )
        if self._modules is None:
            raise WorkerRequestError(
                "unsupported", self._load_error or "BIP-375 runtime is unavailable"
            )
        sp, bip32, bip39 = self._modules
        psbt = _parse_psbt(request, sp.SilentPaymentsPSBT)
        root = _root_from_request(request, bip32, bip39)
        signatures_added = psbt.sign_with(root)
        return {
            "psbt": _encode_psbt(psbt),
            "signatures_added": int(signatures_added),
            "stage": "signed",
        }


class BitSagaWorker:
    """Use BitSaga's persistent ``musig2_psbt.Session`` implementation."""

    backend = "bitsaga"

    def __init__(self, loader: Loader = importlib.import_module) -> None:
        self._loader = loader
        self._modules: tuple[Any, Any, Any, Any] | None = None
        self._load_error: str | None = None
        self._sessions: dict[str, Any] = {}
        try:
            self._modules = self._load_modules()
        except (ImportError, AttributeError) as exc:
            self._load_error = str(exc)

    def _load_modules(self) -> tuple[Any, Any, Any, Any]:
        sp = self._loader("embit.silent_payments.psbt")
        bip32 = self._loader("embit.bip32")
        bip39 = self._loader("embit.bip39")
        musig = self._loader("seedsigner.helpers.musig2_psbt")
        getattr(sp, "SilentPaymentsPSBT")
        getattr(musig, "Session")
        return sp, bip32, bip39, musig

    def capabilities(self) -> RuntimeCapabilities:
        available = self._modules is not None
        return RuntimeCapabilities(
            backend=self.backend,
            plain_bip375=available,
            musig2_sp=available,
            persistent=True,
            unavailable_reason=self._load_error,
        )

    def process(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if self._modules is None:
            raise WorkerRequestError(
                "unsupported", self._load_error or "BitSaga runtime is unavailable"
            )
        suite = _required_string(request, "suite")
        sp, bip32, bip39, musig = self._modules
        psbt = _parse_psbt(request, sp.SilentPaymentsPSBT)
        root = _root_from_request(request, bip32, bip39)
        if suite == "bip375":
            signatures_added = psbt.sign_with(root)
            return {
                "psbt": _encode_psbt(psbt),
                "signatures_added": int(signatures_added),
                "stage": "signed",
            }
        if suite != "musig2-sp":
            raise WorkerRequestError("unsupported", f"unsupported suite: {suite}")
        architecture = request.get("key_architecture", "aggregate-then-derive")
        if architecture != "aggregate-then-derive":
            raise WorkerRequestError(
                "unsupported",
                "BitSaga MuSig2-SP supports aggregate-then-derive only",
            )
        session_id = _required_string(request, "session_id")
        session = self._sessions.get(session_id)
        if session is None:
            session = musig.Session()
            self._sessions[session_id] = session
        progress = session.advance(psbt, root)
        stage = _stage_name(progress.stage, musig)
        if stage == "signed":
            self._sessions.pop(session_id, None)
        return {"psbt": _encode_psbt(psbt), "stage": stage}


def _required_string(request: Mapping[str, Any], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value:
        raise WorkerRequestError("invalid_request", f"{name} must be a non-empty string")
    return value


def _parse_psbt(request: Mapping[str, Any], psbt_type: Any) -> Any:
    encoded = _required_string(request, "psbt")
    try:
        raw = base64.b64decode(encoded, validate=True)
        return psbt_type.parse(raw)
    except Exception as exc:
        raise WorkerRequestError("invalid_psbt", f"failed to parse PSBT: {exc}") from exc


def _root_from_request(request: Mapping[str, Any], bip32: Any, bip39: Any) -> Any:
    mnemonic = _required_string(request, "mnemonic")
    try:
        return bip32.HDKey.from_seed(bip39.mnemonic_to_seed(mnemonic))
    except Exception as exc:
        raise WorkerRequestError("invalid_signer", f"failed to derive signer seed: {exc}") from exc


def _encode_psbt(psbt: Any) -> str:
    return base64.b64encode(psbt.serialize()).decode("ascii")


def _stage_name(stage: Any, musig: Any) -> str:
    for name, label in (("SHARES", "shares"), ("SIGNED", "signed")):
        if stage == getattr(musig, name, object()):
            return label
    return str(stage)


def handle_request(worker: Any, request: Any) -> dict[str, Any]:
    request_id = request.get("id") if isinstance(request, Mapping) else None
    try:
        if not isinstance(request, Mapping):
            raise WorkerRequestError("invalid_request", "request must be a JSON object")
        operation = _required_string(request, "op")
        if operation == "capabilities":
            result = worker.capabilities().as_dict()
        elif operation == "process_psbt":
            result = worker.process(request)
        else:
            raise WorkerRequestError("unsupported", f"unsupported operation: {operation}")
        return {"id": request_id, "ok": True, "result": result}
    except WorkerRequestError as exc:
        return {
            "id": request_id,
            "ok": False,
            "error": {"code": exc.code, "message": str(exc)},
        }
    except Exception as exc:
        return {
            "id": request_id,
            "ok": False,
            "error": {"code": "processing_error", "message": str(exc)},
        }


def serve(worker: Any, input_stream: TextIO, output_stream: TextIO) -> None:
    for line in input_stream:
        try:
            request = json.loads(line)
            response = handle_request(worker, request)
        except json.JSONDecodeError as exc:
            response = {
                "id": None,
                "ok": False,
                "error": {"code": "invalid_json", "message": str(exc)},
            }
        output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
        output_stream.flush()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bip375-signer-worker")
    parser.add_argument("--backend", choices=("seedsigner", "bitsaga"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    worker = SeedSignerWorker() if args.backend == "seedsigner" else BitSagaWorker()
    serve(worker, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
