"""Persistent JSON-lines transport for isolated virtual signer processes."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Mapping, Sequence

from .errors import CapabilityError, InteropError
from .test_seeds import mnemonic_for


@dataclass(frozen=True)
class WorkerHello:
    protocol: int
    backend: str
    suites: tuple[str, ...]


class WorkerProtocolError(InteropError):
    pass


class WorkerClient:
    """One long-lived signer process; state survives every protocol phase."""

    PROTOCOL = 1

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        process_factory=subprocess.Popen,
    ) -> None:
        self.argv = tuple(argv)
        self.cwd = cwd
        self.env = dict(env or {})
        self._factory = process_factory
        self._process = None
        self.hello: WorkerHello | None = None
        self._suite: str | None = None
        self._mnemonic: str | None = None
        self._session_id: str | None = None
        self._network: str | None = None

    def start(
        self,
        suite: str,
        seed_id: str,
        instance_dir: Path,
        network: str | None = None,
    ) -> WorkerHello:
        if self._process is not None:
            raise WorkerProtocolError("worker is already started")
        instance_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(self.env)
        self._process = self._factory(
            list(self.argv), cwd=self.cwd, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        response = self._request({"id": "capabilities", "op": "capabilities"})
        result = response.get("result", {})
        if result.get("protocol_version") != self.PROTOCOL:
            self.stop()
            raise WorkerProtocolError("worker protocol version mismatch")
        suites = tuple(
            name for name, key in (("bip375", "plain_bip375"), ("musig2-sp", "musig2_sp"))
            if result.get(key, False)
        )
        if suite not in suites:
            self.stop()
            reason = result.get("unavailable_reason")
            detail = f": {reason}" if isinstance(reason, str) and reason else ""
            raise CapabilityError(
                f"{result.get('backend', 'worker')} does not support {suite}{detail}"
            )
        self._suite = suite
        self._mnemonic = mnemonic_for(seed_id)
        self._session_id = instance_dir.name
        self._network = network
        self.hello = WorkerHello(self.PROTOCOL, str(result["backend"]), suites)
        return self.hello

    def process_psbt(self, psbt: bytes, phase: str) -> bytes:
        request = {
            "id": phase, "op": "process_psbt", "phase": phase,
            "suite": self._suite, "mnemonic": self._mnemonic,
            "session_id": self._session_id,
            "psbt": base64.b64encode(psbt).decode("ascii"),
        }
        if self._network is not None:
            request["network"] = self._network
        response = self._request(request)
        try:
            return base64.b64decode(response["result"]["psbt"], validate=True)
        except (KeyError, ValueError) as exc:
            raise WorkerProtocolError("worker returned an invalid PSBT payload") from exc

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if self._process is None:
            raise WorkerProtocolError("worker is not started")
        return self._request_on(self._process, request)

    @staticmethod
    def _request_on(process, request: Mapping[str, Any]) -> dict[str, Any]:
        if process.stdin is None or process.stdout is None:
            raise WorkerProtocolError("worker pipes are unavailable")
        process.stdin.write(json.dumps(dict(request), separators=(",", ":")) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise WorkerProtocolError(
                f"worker exited without a response (code={process.poll()}): {stderr.strip()}"
            )
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkerProtocolError("worker emitted non-JSON output") from exc
        if not isinstance(response, dict):
            raise WorkerProtocolError("worker response must be an object")
        if not response.get("ok", False):
            error = response.get("error", "worker request failed")
            if isinstance(error, dict):
                error = error.get("message", error)
            raise WorkerProtocolError(str(error))
        return response

    def __enter__(self) -> "WorkerClient":
        return self

    def __exit__(self, *_args) -> None:
        self.stop()
