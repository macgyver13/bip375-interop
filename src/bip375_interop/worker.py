"""Persistent JSON-lines transport for isolated virtual signer processes."""

from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import threading
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


@dataclass(frozen=True)
class WorkerStepResult:
    psbt: bytes
    story: Mapping[str, str] | None = None
    signatures_added: int | None = None
    stage: str | None = None


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
        self._descriptor: str | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lock = threading.Lock()
        self._stderr_tail_lines: list[str] = []

    def start(
        self,
        suite: str,
        seed_id: str,
        instance_dir: Path,
        network: str | None = None,
        descriptor: str | None = None,
    ) -> WorkerHello:
        if self._process is not None:
            raise WorkerProtocolError("worker is already started")
        instance_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(self.env)
        environment["BIP375_WORKER_INSTANCE_DIR"] = str(instance_dir)
        self._process = self._factory(
            list(self.argv), cwd=self.cwd, env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            start_new_session=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._process, instance_dir / "worker.stderr.log"),
            daemon=True,
        )
        self._stderr_thread.start()
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
        self._descriptor = descriptor
        self.hello = WorkerHello(self.PROTOCOL, str(result["backend"]), suites)
        return self.hello

    def process_psbt(self, psbt: bytes, phase: str) -> WorkerStepResult:
        request = {
            "id": phase, "op": "process_psbt", "phase": phase,
            "suite": self._suite, "mnemonic": self._mnemonic,
            "session_id": self._session_id,
            "psbt": base64.b64encode(psbt).decode("ascii"),
        }
        if self._network is not None:
            request["network"] = self._network
        if self._descriptor is not None:
            request["descriptor"] = self._descriptor
        response = self._request(request)
        result = response.get("result", {})
        try:
            returned_psbt = base64.b64decode(result["psbt"], validate=True)
        except (KeyError, ValueError) as exc:
            raise WorkerProtocolError("worker returned an invalid PSBT payload") from exc
        return WorkerStepResult(
            psbt=returned_psbt,
            story=result.get("story"),
            signatures_added=result.get("signatures_added"),
            stage=result.get("stage"),
        )

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            self._signal_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._signal_group(process, signal.SIGKILL)
            process.wait()
        thread, self._stderr_thread = self._stderr_thread, None
        if thread is not None:
            thread.join(timeout=5)

    def _drain_stderr(self, process, log_path: Path) -> None:
        stream = process.stderr
        if stream is None:
            return
        with log_path.open("w") as log_file:
            for line in stream:
                log_file.write(line)
                log_file.flush()
                with self._stderr_lock:
                    self._stderr_tail_lines.append(line)
                    del self._stderr_tail_lines[:-40]

    def _stderr_tail(self) -> str:
        with self._stderr_lock:
            return "".join(self._stderr_tail_lines)

    @staticmethod
    def _signal_group(process: subprocess.Popen, sig: int) -> None:
        """Signal the worker's whole process group, not just the worker itself.

        Device workers spawn their own child (a Coldcard simulator, a Jade QEMU
        instance) without starting a session of their own, so it stays in the
        worker's process group. Signaling only the worker leaves that child
        orphaned: a bare SIGTERM bypasses Python's `finally` blocks entirely,
        so the worker's own `close()` (which would stop its child) never runs.
        `start()` puts the worker in a new session so its group can be signaled
        without also hitting the harness's own process.
        """
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            try:
                os.killpg(os.getpgid(process.pid), sig)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass
        if sig == signal.SIGKILL:
            process.kill()
        else:
            process.terminate()

    def _request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if self._process is None:
            raise WorkerProtocolError("worker is not started")
        return self._request_on(self._process, request)

    def _request_on(self, process, request: Mapping[str, Any]) -> dict[str, Any]:
        if process.stdin is None or process.stdout is None:
            raise WorkerProtocolError("worker pipes are unavailable")
        process.stdin.write(json.dumps(dict(request), separators=(",", ":")) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            raise WorkerProtocolError(
                f"worker exited without a response (code={process.poll()}): "
                f"{self._stderr_tail().strip()}"
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
