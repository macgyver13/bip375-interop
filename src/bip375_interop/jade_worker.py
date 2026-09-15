"""Persistent Jade QEMU worker for externally supplied BIP-375 PSBTs.

The worker owns one QEMU container and one JadeAPI connection.  This is
important for collaborative Silent Payments and is also the lifecycle needed
for the later MuSig2 adapter, whose nonce state lives in the emulator.
"""

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
from typing import Any, TextIO

from .signer_worker import RuntimeCapabilities, WorkerRequestError, _required_string


_NETWORKS = {"regtest": "localtest"}


class JadeWorker:
    """Sign BIP-375 PSBTs through an isolated Jade QEMU instance."""

    backend = "jade"

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        api_factory: Callable[..., Any] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._runner = runner
        self._sleep = sleeper
        self._container: str | None = None
        self._jade: Any | None = None
        self._mnemonic: str | None = None
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
            musig2_sp=False,
            persistent=True,
            unavailable_reason=self._load_error,
        )

    def process(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if self._api_factory is None:
            raise WorkerRequestError(
                "unsupported", self._load_error or "Jade runtime is unavailable"
            )
        if _required_string(request, "suite") != "bip375":
            raise WorkerRequestError("unsupported", "Jade worker supports BIP-375 only")
        mnemonic = _required_string(request, "mnemonic")
        network = request.get("network", "regtest")
        if not isinstance(network, str):
            raise WorkerRequestError("invalid_request", "network must be a string")
        network = _NETWORKS.get(network, network)
        raw_psbt = _decode_psbt(request)
        jade = self._open(mnemonic)
        try:
            signed = bytes(jade.sign_psbt(network, raw_psbt))
        except Exception as exc:
            raise WorkerRequestError("jade_signing_failed", str(exc)) from exc
        return {
            "psbt": base64.b64encode(signed).decode("ascii"),
            "stage": "device-processed",
        }

    def close(self) -> None:
        if self._jade is not None:
            try:
                self._jade.disconnect()
            except Exception:
                pass
            self._jade = None
        if self._container is not None:
            try:
                self._runner(
                    [self._docker(), "rm", "-f", self._container],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except OSError:
                pass
            self._container = None

    def _open(self, mnemonic: str) -> Any:
        if self._jade is None:
            endpoint = self._environ.get("BIP375_JADE_ENDPOINT")
            if not endpoint:
                endpoint = self._start_container()
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

    def _start_container(self) -> str:
        image = self._environ.get("BIP375_JADE_IMAGE", "bip375-interop-jade")
        docker = self._docker()
        inspected = self._runner(
            [docker, "image", "inspect", image], check=False, capture_output=True, text=True
        )
        if inspected.returncode:
            checkout = self._environ.get("BIP375_JADE_CHECKOUT")
            if not checkout:
                raise WorkerRequestError(
                    "jade_configuration_missing",
                    "BIP375_JADE_CHECKOUT is required to build the Jade QEMU image",
                )
            built = self._runner(
                [docker, "build", "-t", image, "-f", "Dockerfile.qemu", ".",
                 "--build-arg", "QEMU_CONFIG_ARGS=--dev --ci --psram"],
                cwd=checkout,
                check=False,
                capture_output=True,
                text=True,
            )
            if built.returncode:
                raise WorkerRequestError("jade_build_failed", built.stderr.strip())
        launched = self._runner(
            [docker, "run", "-d", "--rm", "-p", "127.0.0.1::30121", image],
            check=False,
            capture_output=True,
            text=True,
        )
        if launched.returncode:
            raise WorkerRequestError("jade_start_failed", launched.stderr.strip())
        self._container = launched.stdout.strip()
        mapped = self._runner(
            [docker, "port", self._container, "30121/tcp"],
            check=False,
            capture_output=True,
            text=True,
        )
        if mapped.returncode or not mapped.stdout.strip():
            self.close()
            raise WorkerRequestError("jade_start_failed", mapped.stderr.strip() or "Jade port was not mapped")
        host_port = mapped.stdout.strip().rsplit(":", 1)[-1]
        return f"tcp:127.0.0.1:{host_port}"

    def _docker(self) -> str:
        return self._environ.get("BIP375_JADE_DOCKER", "docker")


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
