from pathlib import Path

import pytest

from bip375_interop.artifacts import ArtifactRun
from bip375_interop.engine import Round, run_rounds
from bip375_interop.models import Scenario
from bip375_interop.verification import VerificationError
from bip375_interop.worker import WorkerStepResult

_SCAN_KEY = b"\x02" + b"\x11" * 32


def _compact(value: int) -> bytes:
    return bytes([value])


def _map(entries: list[tuple[bytes, bytes]]) -> bytes:
    return b"".join(
        _compact(len(key)) + key + _compact(len(value)) + value for key, value in entries
    ) + b"\x00"


def _psbt(input_entries: list[tuple[bytes, bytes]]) -> bytes:
    globals_map = [
        (b"\xfb", (2).to_bytes(4, "little")), (b"\x02", (2).to_bytes(4, "little")),
        (b"\x04", b"\x01"), (b"\x05", b"\x01"),
    ]
    input_map = [(b"\x0e", b"\x11" * 32), (b"\x0f", b"\x00" * 4), *input_entries]
    output_map = [(b"\x03", (50_000).to_bytes(8, "little")), (b"\x04", b"\x00")]
    return b"psbt\xff" + _map(globals_map) + _map(input_map) + _map(output_map)


def _scenario(**overrides) -> Scenario:
    return Scenario.from_dict({
        "name": "case", "suite": "bip375", "network": "regtest",
        "signers": [{"name": "a", "backend": "jade", "seed_id": "test-a"}],
        "inputs": [{"owner": "a", "type": "p2wpkh", "amount_sat": 50_000}],
        "outputs": [{"type": "silent-payment", "amount_sat": 49_000}],
        **overrides,
    })


class _FakeWorker:
    def __init__(self, responses: dict[str, bytes], stories: dict[str, dict] | None = None):
        self._responses = responses
        self._stories = stories or {}

    def process_psbt(self, psbt: bytes, phase: str) -> WorkerStepResult:
        return WorkerStepResult(psbt=self._responses[phase], story=self._stories.get(phase))


def _run(tmp_path: Path, rounds, responses: dict[str, bytes], *, scenario=None, stories=None):
    artifacts = ArtifactRun(tmp_path, "case")
    return run_rounds(
        _psbt([]), rounds, {"a": _FakeWorker(responses, stories)}, artifacts,
        scenario=scenario or _scenario(),
    )


def test_contribute_phase_rejects_noop(tmp_path: Path):
    rounds = (Round("contribute", ("a",)),)
    with pytest.raises(VerificationError, match="did not add a Silent Payment ECDH share"):
        _run(tmp_path, rounds, {"contribute": _psbt([])})


def test_contribute_phase_rejects_early_signature(tmp_path: Path):
    rounds = (Round("contribute", ("a",)),)
    signed = _psbt([(b"\x02" + b"\x03" * 33, b"sig")])
    with pytest.raises(VerificationError, match="added a signature during the contribute phase"):
        _run(tmp_path, rounds, {"contribute": signed})


def test_contribute_phase_accepts_share(tmp_path: Path):
    rounds = (Round("contribute", ("a",)),)
    contributed = _psbt([(b"\x1d" + _SCAN_KEY, b"\x02" * 33)])
    final_psbt, _ = _run(tmp_path, rounds, {"contribute": contributed})
    assert final_psbt == contributed


def test_sign_phase_rejects_noop(tmp_path: Path):
    rounds = (Round("sign", ("a",)),)
    with pytest.raises(VerificationError, match="did not add a signature during the sign phase"):
        _run(tmp_path, rounds, {"sign": _psbt([])})


def test_sign_phase_accepts_signature(tmp_path: Path):
    rounds = (Round("sign", ("a",)),)
    signed = _psbt([(b"\x02" + b"\x03" * 33, b"sig")])
    final_psbt, _ = _run(tmp_path, rounds, {"sign": signed})
    assert final_psbt == signed


def test_story_is_written_and_included_in_diff_json(tmp_path: Path):
    import json

    rounds = (Round("sign", ("a",)),)
    signed = _psbt([(b"\x02" + b"\x03" * 33, b"sig")])
    story = {"title": "Sign Transaction", "body": "Details go here"}
    artifacts_root = tmp_path
    _run(tmp_path, rounds, {"sign": signed}, stories={"sign": story})

    run_dir = next(artifacts_root.glob("*-case-*"))
    story_text = (run_dir / "01-sign-a-story.txt").read_text()
    assert "Sign Transaction" in story_text
    assert "Details go here" in story_text

    diff = json.loads((run_dir / "01-sign-a-diff.json").read_text())
    assert diff["story"] == story


def test_unexpected_warning_fails_the_step(tmp_path: Path):
    rounds = (Round("sign", ("a",)),)
    signed = _psbt([(b"\x02" + b"\x03" * 33, b"sig")])
    story = {"title": "WARNING", "body": "something risky"}
    with pytest.raises(VerificationError, match="unexpected Coldcard warning"):
        _run(tmp_path, rounds, {"sign": signed}, stories={"sign": story})


def test_expect_warning_opts_in_to_the_warning_screen(tmp_path: Path):
    rounds = (Round("sign", ("a",)),)
    signed = _psbt([(b"\x02" + b"\x03" * 33, b"sig")])
    story = {"title": "WARNING", "body": "something risky"}
    scenario = _scenario(expect_warning=True)
    final_psbt, _ = _run(tmp_path, rounds, {"sign": signed}, scenario=scenario, stories={"sign": story})
    assert final_psbt == signed
