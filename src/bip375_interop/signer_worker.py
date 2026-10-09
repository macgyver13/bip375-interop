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


class BtclibWorker:
    """Sign plain BIP-375 sends with btclib-wallet's PSBT Signer role.

    Runs in btclib-wallet's own venv. btclib's `sign` does not write ECDH
    shares, so this does what a device does per phase: shares and DLEQ proofs
    for the inputs it owns (contribute, resolve-sign), the silent payment
    output scripts once every input is covered (resolve-sign), and signatures
    (resolve-sign, sign). One owner of every eligible input writes the global
    share instead, as the harness expects of a single-owner send.
    """

    backend = "btclib"

    def __init__(self, loader: Loader = importlib.import_module) -> None:
        self._loader = loader
        self._modules: dict[str, Any] | None = None
        self._load_error: str | None = None
        try:
            self._modules = {
                name: loader(module) for name, module in (
                    ("psbt", "btclib_wallet.psbt.psbt"),
                    ("sp", "btclib_wallet.psbt.silent_payments"),
                    ("bip32", "btclib_wallet.bip32.bip32"),
                    ("bip39", "btclib_wallet.mnemonic.bip39"),
                    ("signer", "btclib_wallet.psbt_signer"),
                    ("taproot", "btclib.script.taproot"),
                    ("curves", "btclib_ecc.curves"),
                )
            }
        except ImportError as exc:
            self._load_error = str(exc)

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            backend=self.backend,
            plain_bip375=self._modules is not None,
            musig2_sp=False,
            persistent=True,
            unavailable_reason=self._load_error,
        )

    def process(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if _required_string(request, "suite") != "bip375":
            raise WorkerRequestError("unsupported", "the btclib signer supports plain BIP-375 only")
        if self._modules is None:
            raise WorkerRequestError("unsupported", self._load_error or "btclib-wallet is unavailable")
        m = self._modules
        phase = _required_string(request, "phase")
        psbt = _parse_psbt(request, m["psbt"].Psbt)
        try:
            root = m["bip39"].mxprv_from_mnemonic(_required_string(request, "mnemonic"))
        except Exception as exc:
            raise WorkerRequestError("invalid_signer", f"failed to derive signer seed: {exc}") from exc
        keys = self._owned_keys(psbt, root)
        if any(output.sp_v0_info for output in psbt.outputs) and phase in {"contribute", "resolve-sign"}:
            eligible = m["sp"].eligible_pub_keys(psbt)
            if phase == "resolve-sign" and eligible and eligible.keys() <= keys.keys():
                m["sp"].set_global_share(psbt, [keys[vin] for vin in eligible])
            else:
                for vin in eligible.keys() & keys.keys():
                    m["sp"].set_input_share(psbt, vin, keys[vin])
            if phase == "resolve-sign":
                m["sp"].set_output_scripts(psbt)
        signed: list[int] = []
        if phase != "contribute":
            # The harness's fixtures carry witness UTXOs only.
            psbt, signed = m["psbt"].sign(
                psbt, m["signer"].SoftwareSigner(root), require_non_witness_utxo=False
            )
        return {
            "psbt": base64.b64encode(psbt.serialize()).decode("ascii"),
            "signatures_added": len(signed),
            "stage": "shares" if phase == "contribute" else "signed",
        }

    def _owned_keys(self, psbt: Any, root: str) -> dict[int, int]:
        """Private key of each input this seed owns: the tweaked output key for taproot."""

        m = self._modules
        bip32 = m["bip32"]
        mine = bip32.fingerprint(root)
        keys = {}
        for vin, psbt_in in enumerate(psbt.inputs):
            origins = [(pub, origin) for pub, origin in psbt_in.hd_key_paths.items()]
            origins += [(pub, origin) for pub, (_, origin) in psbt_in.taproot_hd_key_paths.items()]
            for pub, origin in origins:
                if origin.master_fingerprint != mine:
                    continue
                xprv = bip32.derive(root, origin.der_path)
                prv = bip32.prv_keyinfo_from_xprv(xprv)[0]
                xpub = bip32.BIP32KeyData.b58decode(bip32.xpub_from_xprv(xprv)).key
                if pub == xpub:
                    keys[vin] = prv
                elif pub == xpub[1:] and pub == psbt_in.taproot_internal_key:
                    tweaked = m["taproot"].output_prvkey_from_merkle_root(
                        prv, psbt_in.taproot_merkle_root or b""
                    )
                    # BIP-352 counts the x-only output key, so the even-y one.
                    curves = m["curves"]
                    if curves.mult(tweaked)[1] % 2:
                        tweaked = curves.secp256k1.n - tweaked
                    keys[vin] = tweaked
        return keys


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
    parser.add_argument("--backend", choices=("seedsigner", "bitsaga", "btclib"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    worker = {"seedsigner": SeedSignerWorker, "bitsaga": BitSagaWorker, "btclib": BtclibWorker}[args.backend]()
    serve(worker, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
