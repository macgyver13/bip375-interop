from __future__ import annotations

import pytest

from bip375_interop.psbt_maps import (
    PsbtFormatError,
    PsbtMergeError,
    merge_psbts,
    parse_psbt,
    semantic_diff,
)


def compact(value: int) -> bytes:
    if value < 0xFD:
        return bytes([value])
    if value <= 0xFFFF:
        return b"\xfd" + value.to_bytes(2, "little")
    raise ValueError(value)


def psbt_map(entries: list[tuple[bytes, bytes]]) -> bytes:
    return b"".join(
        compact(len(key)) + key + compact(len(value)) + value
        for key, value in entries
    ) + b"\x00"


def fixture(
    *,
    globals_extra: list[tuple[bytes, bytes]] | None = None,
    input_extra: list[tuple[bytes, bytes]] | None = None,
    output_extra: list[tuple[bytes, bytes]] | None = None,
    txid: bytes = b"\x11" * 32,
    amount: int = 50_000,
    unresolved_sp: bool = False,
) -> bytes:
    globals_map = [
        (b"\xfb", (2).to_bytes(4, "little")),
        (b"\x02", (2).to_bytes(4, "little")),
        (b"\x04", compact(1)),
        (b"\x05", compact(1)),
        *(globals_extra or []),
    ]
    input_map = [
        (b"\x0e", txid),
        (b"\x0f", (3).to_bytes(4, "little")),
        *(input_extra or []),
    ]
    output_map = [(b"\x03", amount.to_bytes(8, "little"))]
    output_map.append(
        (b"\x09", b"\x02" + b"\x22" * 32 + b"\x03" + b"\x33" * 32)
        if unresolved_sp
        else (b"\x04", bytes.fromhex("225120") + b"\x22" * 32)
    )
    output_map.extend(output_extra or [])
    return b"psbt\xff" + psbt_map(globals_map) + psbt_map(input_map) + psbt_map(output_map)


def test_parse_round_trip_preserves_map_and_entry_order() -> None:
    raw = fixture(
        globals_extra=[(b"\xfcglobal", b"g")],
        input_extra=[(b"\xfcsecond", b"2"), (b"\xfcfirst", b"1")],
        output_extra=[(b"\xfcoutput", b"o")],
    )
    parsed = parse_psbt(raw)
    assert parsed.serialize() == raw
    assert [entry.key for entry in parsed.inputs[0].entries[-2:]] == [
        b"\xfcsecond",
        b"\xfcfirst",
    ]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not-psbt", "magic"),
        (b"psbt\xff", "truncated"),
        (fixture() + b"extra", "trailing data"),
        (fixture()[:-1], "truncated"),
    ],
)
def test_parse_rejects_invalid_container(raw: bytes, message: str) -> None:
    with pytest.raises(PsbtFormatError, match=message):
        parse_psbt(raw)


def test_parse_rejects_duplicate_keys() -> None:
    raw = fixture(input_extra=[(b"\x0e", b"\x22" * 32)])
    with pytest.raises(PsbtFormatError, match="duplicate key"):
        parse_psbt(raw)


def test_parse_rejects_noncanonical_compact_size() -> None:
    raw = b"psbt\xff\xfd\x01\x00" + fixture()[6:]
    with pytest.raises(PsbtFormatError, match="non-canonical"):
        parse_psbt(raw)


@pytest.mark.parametrize(
    ("key", "message"),
    [
        (b"\xfb", "PSBT version"),
        (b"\x02", "transaction version"),
        (b"\x04", "input count"),
        (b"\x05", "output count"),
    ],
)
def test_parse_requires_psbtv2_global_fields(key: bytes, message: str) -> None:
    raw = fixture()
    parsed = parse_psbt(raw)
    globals_without_key = [
        (entry.key, entry.value) for entry in parsed.globals.entries if entry.key != key
    ]
    rebuilt = (
        b"psbt\xff"
        + psbt_map(globals_without_key)
        + parsed.inputs[0].serialize()
        + parsed.outputs[0].serialize()
    )
    with pytest.raises(PsbtFormatError, match=message):
        parse_psbt(rebuilt)


@pytest.mark.parametrize(
    ("scope", "key", "message"),
    [
        ("input", b"\x0e", "previous txid"),
        ("input", b"\x0f", "output index"),
        ("output", b"\x03", "amount"),
        ("output", b"\x04", "script"),
    ],
)
def test_parse_requires_psbtv2_transaction_fields(
    scope: str, key: bytes, message: str
) -> None:
    parsed = parse_psbt(fixture())
    input_entries = [(entry.key, entry.value) for entry in parsed.inputs[0].entries]
    output_entries = [(entry.key, entry.value) for entry in parsed.outputs[0].entries]
    target = input_entries if scope == "input" else output_entries
    target[:] = [entry for entry in target if entry[0] != key]
    raw = (
        b"psbt\xff"
        + parsed.globals.serialize()
        + psbt_map(input_entries)
        + psbt_map(output_entries)
    )
    with pytest.raises(PsbtFormatError, match=message):
        parse_psbt(raw)


def test_merge_appends_only_new_records_and_preserves_base_order() -> None:
    base = fixture(input_extra=[(b"\xfcbase", b"base")])
    contribution = fixture(
        globals_extra=[(b"\xfcglobal", b"global")],
        input_extra=[(b"\xfcnew-a", b"a"), (b"\xfcnew-b", b"b")],
        output_extra=[(b"\xfcout", b"out")],
    )
    merged = parse_psbt(merge_psbts(base, contribution))
    assert [entry.key for entry in merged.inputs[0].entries[-3:]] == [
        b"\xfcbase",
        b"\xfcnew-a",
        b"\xfcnew-b",
    ]
    assert merged.globals.get(b"\xfcglobal") == b"global"
    assert merged.outputs[0].get(b"\xfcout") == b"out"


def test_merge_is_idempotent() -> None:
    base = fixture()
    contribution = fixture(input_extra=[(b"\xfcshare", b"share")])
    once = merge_psbts(base, contribution)
    assert merge_psbts(once, contribution) == once


def test_merge_allows_bip375_output_resolution_and_flag_clearing() -> None:
    base = fixture(
        unresolved_sp=True,
        globals_extra=[(b"\x06", b"\x03")],
    )
    contribution = fixture(
        unresolved_sp=True,
        globals_extra=[(b"\x06", b"\x00")],
        output_extra=[(b"\x04", bytes.fromhex("225120") + b"\x44" * 32)],
    )
    merged = parse_psbt(merge_psbts(base, contribution))
    assert merged.globals.get(b"\x06") == b"\x00"
    assert merged.outputs[0].get(b"\x04") is not None


def test_merge_rejects_enabling_modifiable_flags() -> None:
    base = fixture(globals_extra=[(b"\x06", b"\x00")])
    contribution = fixture(globals_extra=[(b"\x06", b"\x01")])
    with pytest.raises(PsbtMergeError, match="enables"):
        merge_psbts(base, contribution)


def test_merge_allows_contribution_to_omit_non_transaction_records() -> None:
    base = fixture(input_extra=[(b"\xfckept", b"value")])
    assert parse_psbt(merge_psbts(base, fixture())).inputs[0].get(b"\xfckept") == b"value"


def test_merge_rejects_conflicting_existing_record() -> None:
    base = fixture(input_extra=[(b"\xfcshare", b"one")])
    contribution = fixture(input_extra=[(b"\xfcshare", b"two")])
    with pytest.raises(PsbtMergeError, match="conflicting input 0 proprietary"):
        merge_psbts(base, contribution)


@pytest.mark.parametrize(
    ("base", "contribution", "message"),
    [
        (fixture(), fixture(txid=b"\x22" * 32), "previous_txid"),
        (fixture(), fixture(amount=49_999), "amount"),
        (
            fixture(globals_extra=[(b"\x03", (0).to_bytes(4, "little"))]),
            fixture(globals_extra=[(b"\x03", (1).to_bytes(4, "little"))]),
            "fallback_locktime",
        ),
    ],
)
def test_merge_rejects_transaction_mutation(
    base: bytes, contribution: bytes, message: str
) -> None:
    with pytest.raises(PsbtMergeError, match=message):
        merge_psbts(base, contribution)


def test_semantic_diff_reports_additions_by_scope_and_field() -> None:
    before = fixture()
    after = fixture(
        input_extra=[(b"\x13", b"signature")],
        output_extra=[(b"\x05", b"\x33" * 32)],
    )
    summary = semantic_diff(before, after)
    assert summary.is_additive
    assert [(change.scope, change.index, change.field) for change in summary.added] == [
        ("input", 0, "tap_key_signature"),
        ("output", 0, "tap_internal_key"),
    ]
    assert not summary.removed
    assert not summary.modified


def test_semantic_diff_reports_removals_and_modifications() -> None:
    before = fixture(input_extra=[(b"\xfcshare", b"old")])
    after = fixture(amount=40_000)
    summary = semantic_diff(before, after)
    assert not summary.is_additive
    assert [(change.scope, change.field) for change in summary.removed] == [
        ("input", "proprietary")
    ]
    assert [(change.scope, change.field) for change in summary.modified] == [
        ("output", "amount")
    ]
