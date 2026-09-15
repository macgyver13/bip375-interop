"""Persistent Coldcard simulator worker for externally supplied BIP-375 PSBTs."""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from .signer_worker import RuntimeCapabilities, WorkerRequestError, _required_string


class ColdcardPsbtWorker:
    """Own one isolated Coldcard simulator and its signer state."""

    backend = "coldcard"

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        device_factory: Callable[..., Any] | None = None,
        packer: Any | None = None,
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._device_factory = device_factory
        self._packer = packer
        self._process_factory = process_factory
        self._sleep = sleeper
        self._simulator: subprocess.Popen[bytes] | None = None
        self._device: Any | None = None
        self._mnemonic: str | None = None
        self._seed_script: Path | None = None
        self._load_error: str | None = None
        if self._device_factory is None:
            try:
                self._device_factory = importlib.import_module("ckcc.client").ColdcardDevice
            except (ImportError, AttributeError) as exc:
                self._load_error = str(exc)

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            backend=self.backend,
            plain_bip375=self._device_factory is not None,
            musig2_sp=False,
            persistent=True,
            unavailable_reason=self._load_error,
        )

    def process(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if self._device_factory is None:
            raise WorkerRequestError(
                "unsupported", self._load_error or "Coldcard runtime is unavailable"
            )
        if _required_string(request, "suite") != "bip375":
            raise WorkerRequestError("unsupported", "Coldcard worker supports BIP-375 only")
        network = request.get("network", "regtest")
        if network not in {"regtest", "testnet"}:
            raise WorkerRequestError(
                "unsupported", "Coldcard worker supports regtest or testnet only"
            )
        mnemonic = _required_string(request, "mnemonic")
        psbt = _decode_psbt(request)
        device = self._open(mnemonic)
        try:
            length, digest = device.upload_file(psbt)
            packer = self._protocol_packer()
            device.send_recv(packer.sign_transaction(length, digest, False))
            device.send_recv(packer.sim_keypress(b"y"), timeout=None)
            signed = self._download_signed_psbt(device, packer)
            device.send_recv(packer.sim_keypress(b"x"), timeout=None)
        except Exception as exc:
            raise WorkerRequestError("coldcard_signing_failed", str(exc)) from exc
        return {
            "psbt": base64.b64encode(signed).decode("ascii"),
            "stage": "device-processed",
        }

    def close(self) -> None:
        if self._simulator is not None:
            if self._simulator.poll() is None:
                self._simulator.terminate()
                try:
                    self._simulator.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._simulator.kill()
            self._simulator = None
        self._device = None

    def _open(self, mnemonic: str) -> Any:
        if self._device is None:
            self._device = self._start_simulator()
        if self._mnemonic is None:
            try:
                self._set_mnemonic(self._device, mnemonic)
            except Exception as exc:
                raise WorkerRequestError("coldcard_setup_failed", str(exc)) from exc
            self._mnemonic = mnemonic
        elif mnemonic != self._mnemonic:
            raise WorkerRequestError(
                "invalid_signer", "a persistent Coldcard worker cannot change mnemonic"
            )
        return self._device

    def _start_simulator(self) -> Any:
        checkout = self._environ.get("BIP375_COLDCARD_CHECKOUT")
        python = self._environ.get("BIP375_COLDCARD_PYTHON")
        if not checkout or not python:
            raise WorkerRequestError(
                "coldcard_configuration_missing",
                "BIP375_COLDCARD_CHECKOUT and BIP375_COLDCARD_PYTHON are required",
            )
        source = Path(checkout)
        if not (source / "unix" / "simulator.py").is_file():
            raise WorkerRequestError("coldcard_configuration_missing", "Coldcard simulator is missing")
        self._seed_script = source / "testing" / "devtest" / "set_seed.py"
        env = self._environ.copy()
        env["PATH"] = str(Path(python).parent) + os.pathsep + env.get("PATH", "")
        self._simulator = self._process_factory(
            [python, "simulator.py", "--headless", "--segregate"],
            cwd=source / "unix",
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        socket_path = Path(f"/tmp/ckcc-simulator-{self._simulator.pid}.sock")
        deadline = time.monotonic() + float(
            self._environ.get("BIP375_COLDCARD_STARTUP_SECONDS", "60")
        )
        error: Exception | None = None
        while time.monotonic() < deadline:
            if self._simulator.poll() is not None:
                break
            if socket_path.exists():
                try:
                    device = self._device_factory(sn=str(socket_path), is_simulator=True)
                    device.send_recv(self._protocol_packer().version())
                    return device
                except Exception as exc:
                    error = exc
            self._sleep(0.1)
        detail = f": {error}" if error is not None else ""
        self.close()
        raise WorkerRequestError("coldcard_unavailable", f"Coldcard simulator did not become ready{detail}")

    def _protocol_packer(self) -> Any:
        if self._packer is None:
            self._packer = importlib.import_module("ckcc.protocol").CCProtocolPacker
        return self._packer

    def _set_mnemonic(self, device: Any, mnemonic: str) -> None:
        words = mnemonic.split()
        if not words:
            raise ValueError("mnemonic must not be empty")
        device.send_recv(
            b"EXEC" + f"import main; main.WORDS = {words!r}".encode("utf-8"),
            encrypt=False,
        )
        device.send_recv(
            b"EXEC" + f"execfile({str(self._seed_script)!r})".encode("utf-8"),
            encrypt=False,
            timeout=None,
        )
        device.start_encryption()
        device.check_mitm()

    @staticmethod
    def _download_signed_psbt(device: Any, packer: Any) -> bytes:
        done = device.send_recv(packer.get_signed_txn(), timeout=None)
        while done is None:
            done = device.send_recv(packer.get_signed_txn(), timeout=None)
        if not isinstance(done, tuple) or len(done) != 2:
            raise ValueError("Coldcard did not return a signed PSBT")
        length, digest = done
        return bytes(device.download_file(length, digest))


def _decode_psbt(request: Mapping[str, Any]) -> bytes:
    encoded = _required_string(request, "psbt")
    try:
        psbt = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise WorkerRequestError("invalid_request", "psbt must be base64") from exc
    if not psbt.startswith(b"psbt\xff"):
        raise WorkerRequestError("invalid_request", "psbt payload is invalid")
    return psbt


def handle_request(worker: ColdcardPsbtWorker, request: Mapping[str, Any]) -> dict[str, Any]:
    request_id = request.get("id")
    try:
        if request.get("op") == "capabilities":
            result = worker.capabilities().as_dict()
        elif request.get("op") == "process_psbt":
            result = worker.process(request)
        else:
            raise WorkerRequestError("invalid_request", "unknown worker operation")
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


def serve(worker: ColdcardPsbtWorker, input_stream: TextIO, output_stream: TextIO) -> None:
    try:
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
    finally:
        worker.close()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="bip375-coldcard-worker").parse_args(argv)
    serve(ColdcardPsbtWorker(), sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
