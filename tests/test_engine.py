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


def _psbt(
    input_entries: list[tuple[bytes, bytes]],
    global_entries: list[tuple[bytes, bytes]] = (),
) -> bytes:
    globals_map = [
        (b"\xfb", (2).to_bytes(4, "little")), (b"\x02", (2).to_bytes(4, "little")),
        (b"\x04", b"\x01"), (b"\x05", b"\x01"), *global_entries,
    ]
    input_map = [(b"\x0e", b"\x11" * 32), (b"\x0f", b"\x00" * 4), *input_entries]
    output_map = [(b"\x03", (50_000).to_bytes(8, "little")), (b"\x04", b"\x00")]
    return b"psbt\xff" + _map(globals_map) + _map(input_map) + _map(output_map)


def _psbt_many(per_input: list[list[tuple[bytes, bytes]]]) -> bytes:
    globals_map = [
        (b"\xfb", (2).to_bytes(4, "little")), (b"\x02", (2).to_bytes(4, "little")),
        (b"\x04", bytes([len(per_input)])), (b"\x05", b"\x01"),
    ]
    body = _map(globals_map)
    for index, entries in enumerate(per_input):
        body += _map([
            (b"\x0e", bytes([index + 1]) * 32),
            (b"\x0f", index.to_bytes(4, "little")),
            *entries,
        ])
    body += _map([(b"\x03", (50_000).to_bytes(8, "little")), (b"\x04", b"\x00")])
    return b"psbt\xff" + body


def _share() -> list[tuple[bytes, bytes]]:
    return [
        (b"\x1d" + _SCAN_KEY, b"\x02" * 33),
        (b"\x1e" + _SCAN_KEY, b"\x03" * 64),
    ]


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
    contributed = _psbt(_share())
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


def test_contribute_phase_rejects_signature_on_an_unowned_input(tmp_path: Path):
    scenario = _scenario(
        signers=[
            {"name": "a", "backend": "jade", "seed_id": "test-a"},
            {"name": "b", "backend": "jade", "seed_id": "test-b"},
        ],
        inputs=[
            {"owner": "a", "type": "p2wpkh", "amount_sat": 50_000},
            {"owner": "b", "type": "p2wpkh", "amount_sat": 40_000},
        ],
    )
    returned = _psbt_many([
        [(b"\x02" + b"\x03" * 33, b"sig")],
        _share(),
    ])
    artifacts = ArtifactRun(tmp_path, "case")
    with pytest.raises(VerificationError, match="signer 'b'.*unowned input 0"):
        run_rounds(
            _psbt_many([[], []]),
            (Round("contribute", ("b",)),),
            {"b": _FakeWorker({"contribute": returned})},
            artifacts,
            scenario=scenario,
        )


def test_contribute_phase_keeps_a_proprietary_field(tmp_path: Path):
    from bip375_interop.psbt_maps import parse_psbt

    rounds = (Round("contribute", ("a",)),)
    contributed = _psbt([*_share(), (b"\xfc" + b"test", b"ok")])
    final_psbt, _ = _run(tmp_path, rounds, {"contribute": contributed})
    parsed = parse_psbt(final_psbt)
    assert any(entry.key_type == 0xFC for entry in parsed.inputs[0].entries)


def test_contribute_phase_rejects_added_witness_utxo(tmp_path: Path):
    rounds = (Round("contribute", ("a",)),)
    added = _psbt([
        (b"\x01", b"\x00" * 8 + b"\x16\x00\x14" + b"\x11" * 20),
        *_share(),
    ])
    with pytest.raises(VerificationError, match="signer 'a'.*witness UTXO"):
        _run(tmp_path, rounds, {"contribute": added})


def test_resolve_sign_rejects_added_witness_utxo(tmp_path: Path):
    rounds = (Round("resolve-sign", ("a",)),)
    added = _psbt([
        (b"\x01", b"\x00" * 8 + b"\x16\x00\x14" + b"\x11" * 20),
        (b"\x02" + b"\x03" * 33, b"sig"),
    ])
    with pytest.raises(VerificationError, match="signer 'a'.*witness UTXO.*resolve-sign"):
        _run(tmp_path, rounds, {"resolve-sign": added})


def test_sign_phase_rejects_added_witness_utxo(tmp_path: Path):
    rounds = (Round("sign", ("a",)),)
    added = _psbt([
        (b"\x01", b"\x00" * 8 + b"\x16\x00\x14" + b"\x11" * 20),
        (b"\x02" + b"\x03" * 33, b"sig"),
    ])
    with pytest.raises(VerificationError, match="signer 'a'.*witness UTXO.*sign phase"):
        _run(tmp_path, rounds, {"sign": added})


def test_resolve_sign_accepts_a_global_share_with_the_signature(tmp_path: Path):
    rounds = (Round("resolve-sign", ("a",)),)
    signed = _psbt(
        [(b"\x02" + b"\x03" * 33, b"sig")],
        [(b"\x07" + _SCAN_KEY, b"\x02" * 33), (b"\x08" + _SCAN_KEY, b"\x03" * 64)],
    )
    final_psbt, _ = _run(tmp_path, rounds, {"resolve-sign": signed})
    assert final_psbt == signed


def _taproot_scenario():
    return _scenario(inputs=[{"owner": "a", "type": "p2tr", "amount_sat": 50_000}])


def test_resolve_sign_accepts_a_final_witness_carrying_the_added_signature(tmp_path: Path):
    # SeedSigner's embit finalizes its own taproot input as it signs.
    rounds = (Round("resolve-sign", ("a",)),)
    sig = b"\x05" * 64
    signed = _psbt([(b"\x13", sig), (b"\x08", b"\x01\x40" + sig)])
    final_psbt, _ = _run(tmp_path, rounds, {"resolve-sign": signed}, scenario=_taproot_scenario())
    assert final_psbt == signed


def test_resolve_sign_accepts_a_p2wpkh_final_witness_carrying_the_added_signature(tmp_path: Path):
    rounds = (Round("resolve-sign", ("a",)),)
    pubkey, sig = b"\x03" * 33, b"\x30" * 71
    witness = b"\x02" + bytes([len(sig)]) + sig + bytes([len(pubkey)]) + pubkey
    signed = _psbt([(b"\x02" + pubkey, sig), (b"\x08", witness)])
    final_psbt, _ = _run(tmp_path, rounds, {"resolve-sign": signed})
    assert final_psbt == signed


def test_resolve_sign_rejects_a_final_witness_that_differs_from_the_signature(tmp_path: Path):
    rounds = (Round("resolve-sign", ("a",)),)
    signed = _psbt([(b"\x13", b"\x05" * 64), (b"\x08", b"\x01\x40" + b"\x06" * 64)])
    with pytest.raises(VerificationError, match="signer 'a'.*input 0 final_scriptwitness"):
        _run(tmp_path, rounds, {"resolve-sign": signed}, scenario=_taproot_scenario())


def test_resolve_sign_rejects_a_final_witness_on_an_unowned_input(tmp_path: Path):
    scenario = _scenario(
        signers=[
            {"name": "a", "backend": "jade", "seed_id": "test-a"},
            {"name": "b", "backend": "jade", "seed_id": "test-b"},
        ],
        inputs=[
            {"owner": "a", "type": "p2tr", "amount_sat": 50_000},
            {"owner": "b", "type": "p2tr", "amount_sat": 40_000},
        ],
    )
    sig = b"\x05" * 64
    returned = _psbt_many([
        [(b"\x13", sig), (b"\x08", b"\x01\x40" + sig)],
        [(b"\x13", b"\x07" * 64)],
    ])
    artifacts = ArtifactRun(tmp_path, "case")
    with pytest.raises(VerificationError, match="signer 'b'.*input 0 final_scriptwitness"):
        run_rounds(
            _psbt_many([[], []]),
            (Round("resolve-sign", ("b",)),),
            {"b": _FakeWorker({"resolve-sign": returned})},
            artifacts,
            scenario=scenario,
        )


def test_merge_omission_names_the_signer(tmp_path: Path):
    from bip375_interop.psbt_maps import PsbtMergeError

    base = _psbt([(b"\x03", (1).to_bytes(4, "little"))])
    omitted = _psbt([])
    artifacts = ArtifactRun(tmp_path, "case")
    with pytest.raises(PsbtMergeError, match="signer 'jade-b'") as caught:
        run_rounds(
            base,
            (Round("resolve-sign", ("jade-b",)),),
            {"jade-b": _FakeWorker({"resolve-sign": omitted})},
            artifacts,
        )
    assert str(caught.value) == (
        "step 1 phase resolve-sign signer 'jade-b': "
        "contribution omits input 0 sighash_type (03)"
    )
