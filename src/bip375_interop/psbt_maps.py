"""Dependency-light PSBTv2 binary maps and strict contribution merging.

The harness does not need to understand signatures or proprietary records.  It
does need to preserve their bytes, retain map order, and ensure a signer only
adds records to the transaction it was given.  This module implements exactly
that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .errors import InteropError


PSBT_MAGIC = b"psbt\xff"


class PsbtFormatError(InteropError):
    """The input is not a structurally valid PSBTv2."""


class PsbtMergeError(InteropError):
    """A contribution is not an additive update of the base PSBT."""


@dataclass(frozen=True)
class PsbtEntry:
    key: bytes
    value: bytes

    @property
    def key_type(self) -> int:
        key_type, _ = _read_compact_size(self.key, 0, "PSBT key type")
        return key_type

    @property
    def key_data(self) -> bytes:
        _, offset = _read_compact_size(self.key, 0, "PSBT key type")
        return self.key[offset:]


@dataclass(frozen=True)
class PsbtMap:
    entries: tuple[PsbtEntry, ...]

    def get(self, key: bytes) -> bytes | None:
        for entry in self.entries:
            if entry.key == key:
                return entry.value
        return None

    def serialize(self) -> bytes:
        result = bytearray()
        for entry in self.entries:
            result += _encode_compact_size(len(entry.key))
            result += entry.key
            result += _encode_compact_size(len(entry.value))
            result += entry.value
        result.append(0)
        return bytes(result)


@dataclass(frozen=True)
class PsbtV2:
    globals: PsbtMap
    inputs: tuple[PsbtMap, ...]
    outputs: tuple[PsbtMap, ...]

    def serialize(self) -> bytes:
        maps: Iterable[PsbtMap] = (self.globals, *self.inputs, *self.outputs)
        return PSBT_MAGIC + b"".join(item.serialize() for item in maps)


@dataclass(frozen=True)
class FieldChange:
    scope: str
    index: int | None
    field: str
    key_hex: str
    before_hex: str | None
    after_hex: str | None


@dataclass(frozen=True)
class DiffSummary:
    added: tuple[FieldChange, ...]
    removed: tuple[FieldChange, ...]
    modified: tuple[FieldChange, ...]

    @property
    def is_additive(self) -> bool:
        return not self.removed and not self.modified


def parse_psbt(data: bytes) -> PsbtV2:
    """Parse PSBTv2 maps, retaining exact key/value bytes and entry order."""

    if not isinstance(data, bytes):
        raise TypeError("PSBT must be bytes")
    if not data.startswith(PSBT_MAGIC):
        raise PsbtFormatError("invalid PSBT magic")

    globals_map, offset = _parse_map(data, len(PSBT_MAGIC), "global")
    _validate_global_map(globals_map)
    input_count = _count_value(globals_map, b"\x04", "global input count")
    output_count = _count_value(globals_map, b"\x05", "global output count")

    inputs = []
    for index in range(input_count):
        item, offset = _parse_map(data, offset, f"input {index}")
        _validate_input_map(item, index)
        inputs.append(item)

    outputs = []
    for index in range(output_count):
        item, offset = _parse_map(data, offset, f"output {index}")
        _validate_output_map(item, index)
        outputs.append(item)

    if offset != len(data):
        raise PsbtFormatError(f"trailing data after PSBT maps at offset {offset}")
    return PsbtV2(globals_map, tuple(inputs), tuple(outputs))


def merge_psbts(base: bytes, contribution: bytes) -> bytes:
    """Merge a signer contribution without permitting any mutation.

    Existing records must agree byte-for-byte.  Missing records from the
    contribution are harmless; new records are appended in contribution order.
    Required PSBTv2 transaction fields must exist in both documents and match.
    """

    merged = merge_parsed_psbts(parse_psbt(base), parse_psbt(contribution))
    return merged.serialize()


def merge_parsed_psbts(base: PsbtV2, contribution: PsbtV2) -> PsbtV2:
    """Object-level equivalent of :func:`merge_psbts`."""

    _require_same_transaction(base, contribution)
    globals_map = _merge_map(base.globals, contribution.globals, "global", None)
    inputs = tuple(
        _merge_map(left, right, "input", index)
        for index, (left, right) in enumerate(zip(base.inputs, contribution.inputs))
    )
    outputs = tuple(
        _merge_map(left, right, "output", index)
        for index, (left, right) in enumerate(zip(base.outputs, contribution.outputs))
    )
    return PsbtV2(globals_map, inputs, outputs)


def semantic_diff(before: bytes | PsbtV2, after: bytes | PsbtV2) -> DiffSummary:
    """Summarize record additions, removals, and byte-level modifications."""

    left = parse_psbt(before) if isinstance(before, bytes) else before
    right = parse_psbt(after) if isinstance(after, bytes) else after
    added: list[FieldChange] = []
    removed: list[FieldChange] = []
    modified: list[FieldChange] = []

    _diff_map(left.globals, right.globals, "global", None, added, removed, modified)
    for index in range(max(len(left.inputs), len(right.inputs))):
        _diff_map(
            left.inputs[index] if index < len(left.inputs) else PsbtMap(()),
            right.inputs[index] if index < len(right.inputs) else PsbtMap(()),
            "input",
            index,
            added,
            removed,
            modified,
        )
    for index in range(max(len(left.outputs), len(right.outputs))):
        _diff_map(
            left.outputs[index] if index < len(left.outputs) else PsbtMap(()),
            right.outputs[index] if index < len(right.outputs) else PsbtMap(()),
            "output",
            index,
            added,
            removed,
            modified,
        )
    return DiffSummary(tuple(added), tuple(removed), tuple(modified))


def _parse_map(data: bytes, offset: int, label: str) -> tuple[PsbtMap, int]:
    entries: list[PsbtEntry] = []
    keys: set[bytes] = set()
    while True:
        key_length, offset = _read_compact_size(data, offset, f"{label} key length")
        if key_length == 0:
            return PsbtMap(tuple(entries)), offset
        key, offset = _take(data, offset, key_length, f"{label} key")
        if key in keys:
            raise PsbtFormatError(f"duplicate key {key.hex()} in {label} map")
        keys.add(key)
        # Every PSBT key starts with a canonically encoded compact-size key type.
        _read_compact_size(key, 0, f"{label} key type")
        value_length, offset = _read_compact_size(data, offset, f"{label} value length")
        value, offset = _take(data, offset, value_length, f"{label} value")
        entries.append(PsbtEntry(key, value))


def _read_compact_size(data: bytes, offset: int, label: str) -> tuple[int, int]:
    if offset >= len(data):
        raise PsbtFormatError(f"truncated {label}")
    first = data[offset]
    offset += 1
    if first < 0xFD:
        return first, offset
    size = {0xFD: 2, 0xFE: 4, 0xFF: 8}[first]
    raw, offset = _take(data, offset, size, label)
    value = int.from_bytes(raw, "little")
    minimum = {0xFD: 0xFD, 0xFE: 0x10000, 0xFF: 0x100000000}[first]
    if value < minimum:
        raise PsbtFormatError(f"non-canonical compact size in {label}")
    return value, offset


def _encode_compact_size(value: int) -> bytes:
    if value < 0:
        raise ValueError("compact size cannot be negative")
    if value < 0xFD:
        return bytes([value])
    if value <= 0xFFFF:
        return b"\xfd" + value.to_bytes(2, "little")
    if value <= 0xFFFFFFFF:
        return b"\xfe" + value.to_bytes(4, "little")
    if value <= 0xFFFFFFFFFFFFFFFF:
        return b"\xff" + value.to_bytes(8, "little")
    raise ValueError("compact size exceeds uint64")


def _take(data: bytes, offset: int, size: int, label: str) -> tuple[bytes, int]:
    end = offset + size
    if end > len(data):
        raise PsbtFormatError(f"truncated {label}")
    return data[offset:end], end


def _validate_global_map(item: PsbtMap) -> None:
    version = _required(item, b"\xfb", "global PSBT version")
    if len(version) != 4 or int.from_bytes(version, "little") != 2:
        raise PsbtFormatError("PSBT global version must be 2")
    tx_version = _required(item, b"\x02", "global transaction version")
    if len(tx_version) != 4:
        raise PsbtFormatError("global transaction version must be four bytes")
    _count_value(item, b"\x04", "global input count")
    _count_value(item, b"\x05", "global output count")


def _validate_input_map(item: PsbtMap, index: int) -> None:
    txid = _required(item, b"\x0e", f"input {index} previous txid")
    if len(txid) != 32:
        raise PsbtFormatError(f"input {index} previous txid must be 32 bytes")
    output_index = _required(item, b"\x0f", f"input {index} output index")
    if len(output_index) != 4:
        raise PsbtFormatError(f"input {index} output index must be four bytes")


def _validate_output_map(item: PsbtMap, index: int) -> None:
    amount = _required(item, b"\x03", f"output {index} amount")
    if len(amount) != 8:
        raise PsbtFormatError(f"output {index} amount must be eight bytes")
    script = item.get(b"\x04")
    sp_info = item.get(b"\x09")
    if script is None and sp_info is None:
        raise PsbtFormatError(
            f"output {index} requires script or silent payment information"
        )
    if sp_info is not None and len(sp_info) != 66:
        raise PsbtFormatError(
            f"output {index} silent payment information must be 66 bytes"
        )


def _required(item: PsbtMap, key: bytes, label: str) -> bytes:
    value = item.get(key)
    if value is None:
        raise PsbtFormatError(f"missing {label}")
    return value


def _count_value(item: PsbtMap, key: bytes, label: str) -> int:
    raw = _required(item, key, label)
    value, offset = _read_compact_size(raw, 0, label)
    if offset != len(raw):
        raise PsbtFormatError(f"{label} contains trailing bytes")
    return value


_PROTECTED_KEYS = {
    # tx_modifiable may only transition by clearing bits during BIP-375
    # resolution, so it is checked by _merge_map rather than byte equality.
    "global": (b"\xfb", b"\x02", b"\x03", b"\x04", b"\x05"),
    "input": (b"\x0e", b"\x0f", b"\x10", b"\x11", b"\x12"),
    # An unresolved SP output intentionally acquires its script while signing.
    "output": (b"\x03",),
}


def _require_same_transaction(base: PsbtV2, contribution: PsbtV2) -> None:
    if len(base.inputs) != len(contribution.inputs):
        raise PsbtMergeError("contribution changes transaction input count")
    if len(base.outputs) != len(contribution.outputs):
        raise PsbtMergeError("contribution changes transaction output count")
    _compare_protected(base.globals, contribution.globals, "global", None)
    for index, (left, right) in enumerate(zip(base.inputs, contribution.inputs)):
        _compare_protected(left, right, "input", index)
    for index, (left, right) in enumerate(zip(base.outputs, contribution.outputs)):
        _compare_protected(left, right, "output", index)


def _compare_protected(
    left: PsbtMap, right: PsbtMap, scope: str, index: int | None
) -> None:
    location = scope if index is None else f"{scope} {index}"
    for key in _PROTECTED_KEYS[scope]:
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value != right_value:
            field = _field_name(scope, PsbtEntry(key, b""))
            raise PsbtMergeError(f"contribution changes {location} {field}")


def _merge_map(
    base: PsbtMap, contribution: PsbtMap, scope: str, index: int | None
) -> PsbtMap:
    entries = list(base.entries)
    known = {entry.key: entry.value for entry in base.entries}
    location = scope if index is None else f"{scope} {index}"
    for entry in contribution.entries:
        existing = known.get(entry.key)
        if existing is not None:
            if existing != entry.value:
                if scope == "global" and entry.key == b"\x06":
                    if len(existing) != 1 or len(entry.value) != 1:
                        raise PsbtMergeError("invalid global tx_modifiable value")
                    if entry.value[0] | existing[0] != existing[0]:
                        raise PsbtMergeError(
                            "contribution enables global tx_modifiable flags"
                        )
                    position = next(i for i, value in enumerate(entries) if value.key == entry.key)
                    entries[position] = entry
                    known[entry.key] = entry.value
                    continue
                field = _field_name(scope, entry)
                raise PsbtMergeError(
                    f"conflicting {location} {field} ({entry.key.hex()})"
                )
            continue
        entries.append(entry)
        known[entry.key] = entry.value
    return PsbtMap(tuple(entries))


_FIELD_NAMES = {
    "global": {
        0x00: "unsigned_tx",
        0x01: "xpub",
        0x02: "tx_version",
        0x03: "fallback_locktime",
        0x04: "input_count",
        0x05: "output_count",
        0x06: "tx_modifiable",
        0xFB: "psbt_version",
        0xFC: "proprietary",
    },
    "input": {
        0x00: "non_witness_utxo",
        0x01: "witness_utxo",
        0x02: "partial_signature",
        0x06: "bip32_derivation",
        0x0E: "previous_txid",
        0x0F: "output_index",
        0x10: "sequence",
        0x11: "required_time_locktime",
        0x12: "required_height_locktime",
        0x13: "tap_key_signature",
        0x16: "tap_bip32_derivation",
        0x17: "tap_internal_key",
        0xFC: "proprietary",
    },
    "output": {
        0x02: "bip32_derivation",
        0x03: "amount",
        0x04: "script",
        0x05: "tap_internal_key",
        0x07: "tap_bip32_derivation",
        0x09: "sp_v0_info",
        0x0A: "sp_v0_label",
        0xFC: "proprietary",
    },
}


def _field_name(scope: str, entry: PsbtEntry) -> str:
    return _FIELD_NAMES[scope].get(entry.key_type, f"type_0x{entry.key_type:x}")


def _diff_map(
    before: PsbtMap,
    after: PsbtMap,
    scope: str,
    index: int | None,
    added: list[FieldChange],
    removed: list[FieldChange],
    modified: list[FieldChange],
) -> None:
    left = {entry.key: entry for entry in before.entries}
    right = {entry.key: entry for entry in after.entries}
    for key in sorted(left.keys() | right.keys()):
        old = left.get(key)
        new = right.get(key)
        entry = old or new
        assert entry is not None
        change = FieldChange(
            scope=scope,
            index=index,
            field=_field_name(scope, entry),
            key_hex=key.hex(),
            before_hex=None if old is None else old.value.hex(),
            after_hex=None if new is None else new.value.hex(),
        )
        if old is None:
            added.append(change)
        elif new is None:
            removed.append(change)
        elif old.value != new.value:
            modified.append(change)
