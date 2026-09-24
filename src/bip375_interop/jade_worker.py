"""Persistent Jade QEMU worker for externally supplied BIP-375 PSBTs.

The worker owns one native QEMU process and one JadeAPI connection. This is
important for collaborative Silent Payments and is also the lifecycle needed
for the later MuSig2 adapter, whose nonce state lives in the emulator.
"""

from __future__ import annotations

import argparse
import base64
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from .signer_worker import RuntimeCapabilities, WorkerRequestError, _required_string


_NETWORKS = {"regtest": "localtest"}


class JadeWorker:
    """Sign BIP-375 PSBTs through an isolated native Jade QEMU instance."""

    backend = "jade"

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        api_factory: Callable[..., Any] | None = None,
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._sleep = sleeper
        self._process_factory = process_factory
        self._qemu: subprocess.Popen[bytes] | None = None
        self._qemu_stdout: Any | None = None
        self._qemu_stderr: Any | None = None
        self._runtime_dir: tempfile.TemporaryDirectory[str] | None = None
        self._jade: Any | None = None
        self._mnemonic: str | None = None
        self._registered_descriptor: str | None = None
        self._load_error: str | None = None
        self._api_factory = api_factory
        if self._api_factory is None:
            try:
                jade_api = importlib.import_module("jadepy.jade").JadeAPI
                self._api_factory = jade_api.create_serial
            except (ImportError, AttributeError) as exc:
                self._load_error = str(exc)

    def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            backend=self.backend,
            plain_bip375=self._api_factory is not None,
            musig2_sp=self._api_factory is not None,
            persistent=True,
            unavailable_reason=self._load_error,
        )

    def process(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if self._api_factory is None:
            raise WorkerRequestError(
                "unsupported", self._load_error or "Jade runtime is unavailable"
            )
        suite = _required_string(request, "suite")
        if suite not in {"bip375", "musig2-sp"}:
            raise WorkerRequestError("unsupported", "Jade worker supports BIP-375 only")
        mnemonic = _required_string(request, "mnemonic")
        network = request.get("network", "regtest")
        if not isinstance(network, str):
            raise WorkerRequestError("invalid_request", "network must be a string")
        network = _NETWORKS.get(network, network)
        raw_psbt = _decode_psbt(request)
        jade = self._open(mnemonic)
        if suite == "musig2-sp":
            descriptor = _required_string(request, "descriptor")
            self._register_descriptor(jade, network, descriptor)
        try:
            signed = bytes(jade.sign_psbt(network, raw_psbt))
        except Exception as exc:
            raise WorkerRequestError("jade_signing_failed", str(exc)) from exc
        return {
            "psbt": base64.b64encode(signed).decode("ascii"),
            "stage": "device-processed",
        }

    def _register_descriptor(self, jade: Any, network: str, descriptor: str) -> None:
        if self._registered_descriptor == descriptor:
            return
        try:
            jade.register_descriptor(network, "bip375-interop", descriptor, {})
        except Exception as exc:
            raise WorkerRequestError("jade_setup_failed", str(exc)) from exc
        self._registered_descriptor = descriptor

    def close(self) -> None:
        if self._jade is not None:
            try:
                self._jade.disconnect()
            except Exception:
                pass
            self._jade = None
        if self._qemu is not None:
            if self._qemu.poll() is None:
                self._qemu.terminate()
                try:
                    self._qemu.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._qemu.kill()
            self._qemu = None
        for stream in (self._qemu_stdout, self._qemu_stderr):
            if stream is not None:
                stream.close()
        self._qemu_stdout = None
        self._qemu_stderr = None
        if self._runtime_dir is not None:
            self._runtime_dir.cleanup()
            self._runtime_dir = None

    def _open(self, mnemonic: str) -> Any:
        if self._jade is None:
            endpoint = self._environ.get("BIP375_JADE_ENDPOINT")
            if not endpoint:
                endpoint = self._start_qemu()
            deadline = time.monotonic() + float(self._environ.get("BIP375_JADE_STARTUP_SECONDS", "90"))
            error: Exception | None = None
            while time.monotonic() < deadline:
                jade = self._api_factory(device=endpoint, timeout=10)
                try:
                    jade.connect()
                    jade.get_version_info()
                    self._jade = jade
                    break
                except Exception as exc:
                    error = exc
                    try:
                        jade.disconnect()
                    except Exception:
                        pass
                    self._sleep(1)
            if self._jade is None:
                self.close()
                raise WorkerRequestError(
                    "jade_unavailable", f"Jade QEMU did not become ready: {error}"
                )
        if self._mnemonic is None:
            try:
                self._jade.set_mnemonic(mnemonic, temporary_wallet=True)
            except Exception as exc:
                raise WorkerRequestError("jade_setup_failed", str(exc)) from exc
            self._mnemonic = mnemonic
        elif mnemonic != self._mnemonic:
            raise WorkerRequestError(
                "invalid_signer", "a persistent Jade worker cannot change mnemonic"
            )
        return self._jade

    def _start_qemu(self) -> str:
        checkout = self._environ.get("BIP375_JADE_CHECKOUT")
        if not checkout:
            raise WorkerRequestError(
                "jade_configuration_missing",
                "BIP375_JADE_CHECKOUT is required to start Jade QEMU",
            )
        checkout_path = Path(checkout)
        flash_image = checkout_path / "build" / "flash_image.bin"
        efuse_image = checkout_path / "build" / "qemu_efuse.bin"
        missing = [str(path) for path in (flash_image, efuse_image) if not path.is_file()]
        if missing:
            raise WorkerRequestError(
                "jade_build_missing", f"Jade QEMU build artifacts are missing: {', '.join(missing)}"
            )

        self._runtime_dir = tempfile.TemporaryDirectory(prefix="bip375-jade-qemu-")
        runtime_path = Path(self._runtime_dir.name)
        runtime_flash = runtime_path / flash_image.name
        runtime_efuse = runtime_path / efuse_image.name
        shutil.copy2(flash_image, runtime_flash)
        shutil.copy2(efuse_image, runtime_efuse)

        port = _unused_port()
        command = [
            _qemu_executable(self._environ),
            "-nographic",
            "-machine",
            "esp32",
            "-m",
            "4M",
            "-drive",
            f"file={runtime_flash},if=mtd,format=raw",
            "-nic",
            f"user,model=open_eth,id=lo0,hostfwd=tcp:127.0.0.1:{port}-:30121",
            "-drive",
            f"file={runtime_efuse},if=none,format=raw,id=efuse",
            "-global",
            "driver=nvram.esp32.efuse,property=drive,value=efuse",
            "-serial",
            "null",
        ]
        stdout_target: Any = subprocess.DEVNULL
        stderr_target: Any = subprocess.DEVNULL
        instance_dir = self._environ.get("BIP375_WORKER_INSTANCE_DIR")
        if instance_dir:
            log_dir = Path(instance_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            self._qemu_stdout = (log_dir / "qemu.stdout.log").open("wb")
            self._qemu_stderr = (log_dir / "qemu.stderr.log").open("wb")
            stdout_target = self._qemu_stdout
            stderr_target = self._qemu_stderr
        try:
            self._qemu = self._process_factory(
                command,
                cwd=checkout_path,
                stdin=subprocess.DEVNULL,
                stdout=stdout_target,
                stderr=stderr_target,
            )
        except OSError as exc:
            self.close()
            raise WorkerRequestError("jade_start_failed", str(exc)) from exc
        return f"tcp:127.0.0.1:{port}"


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _qemu_executable(environ: Mapping[str, str]) -> str:
    configured = environ.get("BIP375_JADE_QEMU")
    if configured:
        return configured
    discovered = shutil.which("qemu-system-xtensa")
    if discovered:
        return discovered
    # ESP-IDF installs its tools under IDF_TOOLS_PATH (default ~/.espressif) on every host.
    tools = Path(environ.get("IDF_TOOLS_PATH") or Path.home() / ".espressif")
    candidates = sorted(
        tools.glob("tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa"),
        reverse=True,
    )
    if candidates:
        return str(candidates[0])
    raise WorkerRequestError(
        "jade_configuration_missing",
        "BIP375_JADE_QEMU is required when qemu-system-xtensa is not on PATH",
    )


def _decode_psbt(request: Mapping[str, Any]) -> bytes:
    try:
        return base64.b64decode(_required_string(request, "psbt"), validate=True)
    except ValueError as exc:
        raise WorkerRequestError("invalid_psbt", "psbt is not canonical base64") from exc


def _response(worker: JadeWorker, request: Any) -> dict[str, Any]:
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
        return {"id": request_id, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except Exception as exc:
        return {"id": request_id, "ok": False, "error": {"code": "processing_error", "message": str(exc)}}


def serve(worker: JadeWorker, input_stream: TextIO, output_stream: TextIO) -> None:
    try:
        for line in input_stream:
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                request = None
            output_stream.write(json.dumps(_response(worker, request), separators=(",", ":")) + "\n")
            output_stream.flush()
    finally:
        worker.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bip375-jade-worker")
    parser.parse_args(argv)
    serve(JadeWorker(), sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
