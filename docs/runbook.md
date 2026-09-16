# Scenario runbook

Every scenario file under `scenarios/`, what it actually exercises, how to run it, and
its current status as of the most recent artifact evidence in `artifacts/`. Companion to
`docs/roadmap.md` (the delivery plan); this is the "how do I actually run thing X" doc.

## Prerequisites

- Python venv at `.venv` with this package installed (`pip install -e .`).
- `interop.yaml` checkouts must exist locally and point at real source trees (see
  `interop.yaml` at repo root for current paths: `silent-pay`, `bip375-test-generator`,
  `coldcard` (coldcard-firmware), `jade` (Jade), `seedsigner`, `bitsaga-seedsigner`).
- `qemu-system-xtensa` on `PATH`, or under `~/.espressif/tools/qemu-xtensa/*/qemu/bin/`
  (auto-discovered) -- required for any Jade scenario.
- Coldcard's own venv at `<coldcard checkout>/ENV/bin/python` -- required for any
  Coldcard scenario; the harness's own `.venv` does not need Coldcard's dependencies
  (`ckcc`, `hid`), only Coldcard's own `ENV` does, since the persistent Coldcard worker
  runs as a separate subprocess using that interpreter.
- `bip375-interop doctor` checks checkout cleanliness; pass `--allow-dirty` if a
  checkout (e.g. `silent-pay`, which accumulates `target/` build output) is expected to
  be dirty.
- MuSig2-SP scenarios that take `--psbt` need a real externally-built PSBT -- see
  "Building a MuSig2-SP treasury and initial PSBT" below. Plain BIP-375 scenarios can
  either take `--psbt` or use `run-generated`, which synthesizes one in-process.

All commands below assume `cd /Users/macgyver/src/bip375-interop && source .venv/bin/activate`.

## Quick reference

| Scenario | Suite | Backends | Status |
|---|---|---|---|
| `bip375-seedsigner-single` | bip375 | seedsigner | **Working** |
| `bip375-seedsigner-single-taproot` | bip375 | seedsigner | **Working** (P2TR key-path input) |
| `bip375-coldcard-jade-two-way` | bip375 | coldcard, jade | **Working** |
| `bip375-coldcard-jade-two-way-taproot` | bip375 | coldcard, jade | **Working** (both inputs P2TR) |
| `bip375-jade-two-way` | bip375 | jade x2 | **Working** (same-backend) |
| `bip375-coldcard-two-way` | bip375 | coldcard x2 | **Working** (same-backend) |
| `bip375-three-way` | bip375 | coldcard, jade, seedsigner | Blocked at `seedsigner-c` (see below); `coldcard-a`/`jade-b` contribute cleanly |
| `bip375-seedsigner-jade-two-way` | bip375 | seedsigner, jade | Blocked (SeedSigner cannot co-own a plain BIP-375 send, see below) |
| `bip375-seedsigner-coldcard-two-way` | bip375 | seedsigner, coldcard | Blocked, same root cause, confirmed against a second backend |
| `musig2-sp-coldcard-jade-two-way` | musig2-sp | coldcard, jade | **Working end to end** (signed, broadcast, confirmed, recipient-verified on regtest) |
| `musig2-sp-jade-derive-first-two-way` | musig2-sp | jade x2 | **Working end to end**, verified via direct script only -- `cli.py run` doesn't build this descriptor shape yet, see below |
| `musig2-sp-signet-treasury` | musig2-sp | bitsaga-seedsigner x3 | Blocked (BitSaga bug, see below) |
| `musig2-sp-three-way` | musig2-sp | coldcard, jade, bitsaga-seedsigner | Partially working: round1 succeeds for coldcard+jade, blocked at bitsaga-c |
| `frost-sp-reserved` | frost-sp | n/a | Intentionally unsupported (reserved suite identifier only) |

## Plain BIP-375 scenarios

### `bip375-seedsigner-single` -- working

Single signer, global contribution mode. Fixture is generated in-process (no external
PSBT needed):

```bash
bip375-interop run-generated scenarios/bip375-seedsigner-single.yaml
```

Last confirmed passing: `artifacts/20260916T024327Z-bip375-seedsigner-single/`. (Was
briefly broken by a `build_bip375_fixture` bug that rejected `global` contribution mode
outright -- see `docs/roadmap.md` Milestone 2 -- now fixed.)

### `bip375-seedsigner-single-taproot` -- working

Same shape as above, with a P2TR key-path input instead of P2WPKH. Confirms SeedSigner's
software signer resolves and signs a taproot SP input correctly when it is the sole
owner:

```bash
bip375-interop run-generated scenarios/bip375-seedsigner-single-taproot.yaml
```

Last confirmed passing: `artifacts/20260916T024327Z-bip375-seedsigner-single-taproot/`.

### `bip375-coldcard-jade-two-way` -- working

Two owners, per-input contribution mode, real Coldcard simulator + Jade QEMU:

```bash
bip375-interop run-generated scenarios/bip375-coldcard-jade-two-way.yaml
```

Last confirmed passing: `artifacts/20260915T174401Z-bip375-coldcard-jade-two-way/`.

### `bip375-coldcard-jade-two-way-taproot` -- working

Same pairing, both inputs P2TR key-path instead of P2WPKH:

```bash
bip375-interop run-generated scenarios/bip375-coldcard-jade-two-way-taproot.yaml
```

Last confirmed passing:
`artifacts/20260916T024607Z-bip375-coldcard-jade-two-way-taproot/`; `semantic_diff`
against `00-initial.psbt` shows real `PSBT_IN_SP_ECDH_SHARE`/`PSBT_IN_SP_DLEQ`
(`0x1d`/`0x1e`) per-input contributions from both devices plus a `tap_key_signature` on
each input -- both firmwares implement BIP-375's real per-input multi-party protocol
natively. First attempt failed with `conflicting input 1 type_0x3 (03)`: Jade's
`resolve-sign` contribution rewrote its own taproot input's sighash type from
`SIGHASH_DEFAULT` (what the fixture pre-set) to explicit `SIGHASH_ALL`. Both values are
valid per BIP-341, but the strict-merge policy correctly flags any field mutation it
didn't allow-list; fixed by having `build_bip375_fixture` pre-set `SIGHASH_ALL` for P2TR
inputs too, matching what P2WPKH inputs already used and what Jade itself produces.

### `bip375-jade-two-way` -- working (same-backend)

Two independent Jade QEMU instances (seed_ids `test-a`/`test-b`), confirming the
same-backend lane in addition to the coldcard/jade mixed pairing:

```bash
bip375-interop run-generated scenarios/bip375-jade-two-way.yaml
```

Last confirmed passing: `artifacts/20260916T024340Z-bip375-jade-two-way/`.

### `bip375-coldcard-two-way` -- working (same-backend)

Two segregated Coldcard simulator instances. `coldcard_psbt_worker.py` already keys each
simulator's control socket by the simulator subprocess's own PID
(`/tmp/ckcc-simulator-<pid>.sock`), so two concurrent instances don't collide:

```bash
bip375-interop run-generated scenarios/bip375-coldcard-two-way.yaml
```

Last confirmed passing: `artifacts/20260916T024410Z-bip375-coldcard-two-way/`.

### `bip375-seedsigner-jade-two-way` / `bip375-seedsigner-coldcard-two-way` -- blocked

```bash
bip375-interop run-generated scenarios/bip375-seedsigner-jade-two-way.yaml
bip375-interop run-generated scenarios/bip375-seedsigner-coldcard-two-way.yaml
```

Both fail identically and immediately (before any device is even contacted, since
`seedsigner-a` is the first signer in both scenarios):

```
error: Silent Payment signing failed: input(s) 1 belong to another signer; multi-party Silent Payment sends are not supported.
```

This is not a QEMU/harness defect -- it reproduces deterministically regardless of which
other backend SeedSigner is paired with. Root cause: SeedSigner's plain BIP-375 software
signer (`SeedSignerWorker` in `signer_worker.py`) calls upstream SeedSigner's
`embit.silent_payments.psbt.SilentPaymentsPSBT.sign_with` for every round, which always
resolves and signs as if it were the sole owner of every eligible input, and this
particular embit fork has no per-input multi-party contribution primitives
(`PSBT_IN_SP_ECDH_SHARE`/`PSBT_IN_SP_DLEQ`) implemented at all. See `docs/roadmap.md`
Milestone 2 for the full writeup. This is a genuine upstream limitation, not something
to fix inside this harness (which would mean reimplementing BIP-375's per-input
ECDH-share/DLEQ scheme from scratch) -- treat as blocked pending upstream SeedSigner/
embit multi-party send support, the same way the BitSaga `_sp_groups` bug is treated in
Milestone 3.

### `bip375-three-way` -- blocked at the SeedSigner leg

Coldcard (P2WPKH) + Jade (P2TR) + SeedSigner (P2WPKH), per-input mode:

```bash
bip375-interop run-generated scenarios/bip375-three-way.yaml
```

`coldcard-a` and `jade-b` both complete their `contribute` round cleanly (real per-input
multi-party contribution, same as the taproot two-way scenario above); the run then
fails at `seedsigner-c`'s `resolve-sign` round with `input(s) 0, 1 belong to another
signer`, the same SeedSigner limitation as above, just reached one round later because
SeedSigner is the last signer here instead of the first. Last attempt:
`artifacts/20260916T024612Z-bip375-three-way/` has both `01-contribute-coldcard-a-*` and
`02-contribute-jade-b-*`, nothing for `seedsigner-c`. (The scenario originally could not
even be generated: it declared a P2TR input before `build_bip375_fixture` supported one,
and a second `change` output, which the fixture builder's single-SP-output contract
rejects -- simplified to one SP payment sized to leave a 1,000 sat fee.)

## MuSig2-SP scenarios

All of these need an externally-built PSBT (`--psbt <path>`) rather than
`run-generated` -- `fixtures.py`'s `build_bip375_fixture` only supports the `bip375`
suite today, so a musig2-sp scenario's `inputs`/`outputs` fields in the YAML are
currently unused/aspirational. See "Building a treasury and initial PSBT" below for how
to produce that PSBT.

### `musig2-sp-coldcard-jade-two-way` -- working end to end

The flagship scenario: 2-of-2, aggregate-then-derive, real Coldcard + Jade devices.
This is the only scenario in the repo that has been signed, finalized, broadcast, and
confirmed on-chain, with the recipient's output independently re-detected via a real
BIP-352 scan against live chain state.

```bash
bip375-interop run scenarios/musig2-sp-coldcard-jade-two-way.yaml --psbt <initial.psbt>
```

Last confirmed passing: `artifacts/20260915T212834Z-musig2-sp-coldcard-jade-two-way/`.
Reversing signer order also works (see "Signer order" below):

```bash
bip375-interop run scenarios/musig2-sp-coldcard-jade-two-way.yaml --psbt <initial.psbt> \
  --signer-order jade-b,coldcard-a
```

### `musig2-sp-jade-derive-first-two-way` -- working end to end

2-of-2, **derive-then-aggregate** (`tr(musig(A/<0;1>/*,B/<0;1>/*))` -- each participant
derived before aggregating, instead of the aggregate derived after), two independent
real Jade QEMU instances. Verified with the same rigor as the aggregate-then-derive
flagship above: signed, finalized, broadcast, confirmed on regtest, recipient output
independently re-verified on-chain. See `docs/roadmap.md` Milestone 3 for the full
writeup, including the Jade firmware commit (`32a020c3`) that made this possible and the
firmware-rebuild step that was needed before QEMU actually picked it up.

Build the treasury with `--key-architecture derive-then-aggregate`, then run the scenario
normally -- `cli.py` reads the scenario's declared `key_architecture` and registers the
matching descriptor shape with each device:

```bash
bip375-interop treasury-wallet test-a test-b --network regtest \
  --key-architecture derive-then-aggregate --out wallet.toml
# ...build initial.psbt from that wallet as in the recipe below...
bip375-interop run scenarios/musig2-sp-jade-derive-first-two-way.yaml --psbt <initial.psbt>
```

Last confirmed passing:
`artifacts/20260916T141309Z-musig2-sp-jade-derive-first-two-way/` -- CLI-generated
treasury throughout (no hand-written descriptor), both Jade instances contributing
pubnonce/partial-sig/ECDH-share/DLEQ on the input, then broadcast and confirmed
(`ad8a806cc690ce1bd593b2a0d42a5927aabc7dee2f30f3bfd763432a5627ad2c`) with the
recipient's output re-detected on-chain.

Earlier runs of this scenario predate `treasury.py` supporting this shape, and had to
hand-write the wallet TOML and drive the two `JadeWorker`s directly; that workaround is
no longer needed.

### `musig2-sp-three-way` -- partially working

Adds `bitsaga-c` (bitsaga-seedsigner) as a third signer, 3-of-3. Coldcard and Jade both
complete round1 correctly against a real 3-of-3 treasury; the run then fails at
`bitsaga-c`'s round1 with `error: 0`. This is a real, reproduced bug in
`bitsaga-seedsigner`'s own `musig2_psbt.py` (`_sp_groups`/`sp_scan_keys`, `KeyError: 0`
whenever a real silent-payment output is present) -- not a bug in this harness. See
`docs/roadmap.md` Milestone 3 for the full root cause and fix location.

```bash
bip375-interop run scenarios/musig2-sp-three-way.yaml --psbt <initial.psbt>
```

Last attempt: `artifacts/20260915T212859Z-musig2-sp-three-way/` -- has both
`01-round1-coldcard-a-*.psbt` and `02-round1-jade-b-*.psbt`, nothing for `bitsaga-c`.

### `musig2-sp-signet-treasury` -- blocked

3-of-3, all `bitsaga-seedsigner`, on signet against a real funded UTXO. Hits the same
BitSaga bug as above, on the very first signer this time (since every signer here is
BitSaga). Kept as the historically-first MuSig2-SP scenario attempted and as the
signet-specific live-verified-artifacts record in `docs/roadmap.md`.

```bash
bip375-interop run scenarios/musig2-sp-signet-treasury.yaml --psbt <initial.psbt>
```

## Native single-device smoke tests

Not full interop scenarios -- single-device, plain BIP-375 only, no cross-device
merging:

```bash
bip375-interop smoke coldcard
bip375-interop smoke jade
```

## Utility commands

```bash
bip375-interop doctor [--allow-dirty]        # checkout cleanliness
bip375-interop validate <scenario.yaml>      # schema + suite validation only
bip375-interop plan <scenario.yaml>          # print the round/signer schedule
bip375-interop treasury-wallet <seed_ids...> --network <net> [--out wallet.toml]
                                              # build a TreasuryWalletConfig descriptor
                                              # from the harness's published test seeds
```

## Signer order (debug / fuzzing)

`plan`, `run`, and `run-generated` all accept:

- `--signer-order name1,name2,...` -- explicit override of each round's processing
  order. Must name every scenario signer exactly once. Does **not** change the
  descriptor's participant order (which fixes the MuSig2 aggregate key) -- only which
  device is asked to sign first, second, etc. This means the same treasury/PSBT can be
  reused across different signer orderings.
- `--shuffle-signers [--shuffle-seed N]` -- randomizes the order; the seed used is
  always printed to stderr so a run that finds something can be reproduced exactly.

```bash
bip375-interop plan scenarios/musig2-sp-coldcard-jade-two-way.yaml \
  --signer-order jade-b,coldcard-a
bip375-interop plan scenarios/musig2-sp-coldcard-jade-two-way.yaml --shuffle-signers
```

## Building a MuSig2-SP treasury and initial PSBT

MuSig2-SP scenarios need a real externally-built PSBT. The published test seeds are
`test-a` / `test-b` / `test-c` (see `src/bip375_interop/test_seeds.py`); use whichever
subset matches the scenario's signers. Their **order does not matter**: silent-pay sorts
participant pubkeys before aggregating (`payroll.rs`), and Jade/Coldcard's descriptor
parsers sort internally too, so listing the same signers in any order yields a
byte-identical treasury. Verified by generating a descriptor with reversed seed order and
confirming `treasury_address` derives the identical receive and change addresses, for
both key architectures.

Pass `--key-architecture derive-then-aggregate` to `treasury-wallet` for the
`tr(musig(A/<0;1>/*,...))` shape; the default is aggregate-then-derive.

### Regtest (self-funding, no external node needed)

1. Start a throwaway regtest `bitcoind` on a fresh datadir:
   ```bash
   bitcoind -regtest -datadir=<datadir> -daemon -fallbackfee=0.0001 -rpcport=<port>
   ```
2. Build the treasury descriptor:
   ```bash
   bip375-interop treasury-wallet test-a test-b --network regtest --out wallet.toml
   ```
3. Build `recipients.toml` (production schema: `label`/`amount_sat`/`address` only).
   The recipient address must use regtest's `sprt1` HRP, not signet/testnet's `tsp1` --
   embit's own `encode_silent_payment_address` only knows `sp`/`tsp`, so re-encode an
   existing `tsp1...` address's scan/spend keys directly:
   ```python
   from embit import bech32
   from embit.silent_payments.sp import decode_silent_payment_address
   scan_pk, spend_pk = decode_silent_payment_address("tsp1...")
   data = bech32.convertbits(scan_pk.sec() + spend_pk.sec(), 8, 5)
   regtest_addr = bech32.bech32_encode(bech32.Encoding.BECH32M, "sprt", [0] + data)
   ```
4. Build the initial PSBT -- `build_round1` self-mines and matures the treasury deposit
   automatically on regtest, no manual funding step needed:
   ```bash
   cd /Users/macgyver/src/silent-pay
   cargo run -p sp-demo --bin build_round1 -- --wallet wallet.toml \
     --recipients recipients.toml --out-dir <dir> --rpc-url http://127.0.0.1:<port> \
     --rpc-cookie <datadir>/regtest/.cookie
   ```
5. Run the scenario against the resulting `<dir>/initial.psbt`.

### Signet (real funded UTXO required)

Same shape, but skip step 1 (use a real signet node) and fund the treasury's receive
address externally first (`sp-demo`'s `treasury_address --chain receive --index 0` bin
prints where to send funds). See `docs/roadmap.md`'s "Live-verified artifacts" section
for the exact commands and a concrete worked example.

### Closing the loop: finalize, broadcast, verify on-chain

Once a scenario produces a fully-signed `final.psbt`:

```bash
cd /Users/macgyver/src/silent-pay
cargo run -p sp-demo --bin finalize -- <final.psbt>
cargo run -p sp-demo --bin broadcast_final -- --wallet wallet.toml \
  --tx-hex-file <musig2-sp-final-hex.txt> --rpc-url http://127.0.0.1:<port> \
  --rpc-cookie <datadir>/regtest/.cookie
cargo run -p sp-demo --bin verify_onchain -- <musig2-sp-final.psbt> \
  --recipients <recipients-with-scan-key.toml> --txid <txid> \
  --rpc-url http://127.0.0.1:<port> --rpc-cookie <datadir>/regtest/.cookie
```

`verify_onchain`/`scan_recipients` need the **demo** recipient schema (`scan_key_hex`
present, since actually scanning needs the recipient's own private scan key) -- this is
a different file from the **production** schema `build_round1` requires (which rejects
`scan_key_hex` via `deny_unknown_fields`). Keep both `recipients.toml` files around.

## Descriptor conformance gap check

`treasury.py` builds the treasury descriptor in Python; silent-pay's
`descriptor_from_signers` / `parse_descriptor_signers` (`wallet.rs`) build and parse the
same string in Rust, and the *devices* derive keys from whatever string this harness
registers with them. Three implementations, one string format -- so it is worth
re-checking they still agree after touching either side. This needs no node and no new
code; `treasury_address` only loads the wallet and derives.

```bash
bip375-interop treasury-wallet test-a test-b test-c --network regtest --out atd.toml
bip375-interop treasury-wallet test-a test-b --network regtest \
  --key-architecture derive-then-aggregate --out dta.toml
cd <silent-pay checkout>
for w in atd dta; do for c in receive change; do
  cargo run -q -p sp-demo --bin treasury_address -- --wallet <dir>/$w.toml --chain $c --index 0
done; done
```

Expected (all four are device-verified values, not just self-consistent ones -- the
aggregate-then-derive pair was matched byte-for-byte against Jade's and Coldcard's own
address derivation, and the derive-then-aggregate pair backs a treasury that was actually
signed by two Jade instances and broadcast on regtest):

| Wallet | chain | address |
|---|---|---|
| `atd` (test-a/b/c) | receive[0] | `bcrt1p83upesz8rpnwperlnvs494kf3za07d586a942p87x5298hxdvqesr0njw9` |
| `atd` (test-a/b/c) | change[0] | `bcrt1pjv06l60c7zqsqs36n0ex94866rcknvrtj5ssw3w8gghqc5f3c7cs9eszu8` |
| `dta` (test-a/b) | receive[0] | `bcrt1pxel5t3l2xsztnx7rv6tapca84adprd96skphtx7j5q3x3s67tv5qxmuheu` |
| `dta` (test-a/b) | change[0] | `bcrt1pnvv728yyhpdtqhfk95lhee6kqksdr3gxusk8u8k69af2czu0pj5sv6efmj` |

A mismatch means the Python and Rust builders have drifted (or a device has), which would
otherwise surface much later as a device rejecting a PSBT that "looks right". Note that
`build_round1` also echoes back the descriptor it parsed: since silent-pay's
`normalized()` *regenerates* the string from the parsed signers, seeing your own
descriptor come back unchanged is a free fixed-point check that the two builders format
identically, not just parse compatibly.

### Known gaps in `treasury.py` (deliberate, not bugs)

- **Only the harness's own published test seeds.** `derive_signer_xpub` goes from a
  `seed_id` to an xpub; there is no way to include an externally supplied cosigner xpub
  (a real physical device, say). Milestone 4's guided-hardware-handoff work would need
  that, since a real device will not hand over a test mnemonic.
- **One fixed account path** (`m/48'/1'/0'/3'`, testnet coin type). silent-pay parses
  whatever origin the descriptor carries, so it is strictly more general here.

## Known gotchas

- **Coldcard reports regtest addresses with a `tb1p...` HRP, not `bcrt1p...`.** The
  underlying witness program is identical (confirmed by decoding both); this is a
  cosmetic simulator quirk, not a derivation bug. Compare raw witness-program bytes,
  not address strings, when cross-checking Coldcard against another backend.
- **`bitsaga-seedsigner` crashes signing any PSBT with a real silent-payment output**
  (`KeyError: 0` in `musig2_psbt.py`'s `_sp_groups`/`sp_scan_keys`). Affects
  `musig2-sp-three-way` and `musig2-sp-signet-treasury`. Fix belongs upstream in
  bitsaga-seedsigner, not in this harness.
- **Participant pubkeys must be sorted before aggregating**, regardless of the order
  written in a descriptor string -- silent-pay's own PSBT-building code
  (`payroll.rs`) does this, and Jade/Coldcard's descriptor parsers do it internally too.
  Only matters if you're hand-constructing a MuSig2 PSBT field yourself (e.g. for a
  future fixture generator) rather than using silent-pay's tooling.
- **Worker subprocess cleanup**: fixed as of the `musig2-sp-coldcard-jade` branch --
  `WorkerClient` now starts each worker in its own process group and signals the whole
  group on `stop()`. Before that fix, Coldcard's `simulator.py` and Jade's
  `qemu-system-xtensa` would orphan on every run.
- **A Jade firmware source change is not a Jade firmware capability change until
  `build/flash_image.bin` is rebuilt.** QEMU boots the compiled image, not the source
  tree; `jade_worker.py`'s `_start_qemu()` will happily boot a stale image with no
  error. Check `git log`/commit timestamps against the image's mtime before trusting a
  "Jade now supports X" claim. Rebuild with (needs an ESP-IDF checkout):
  ```bash
  source <esp-idf checkout>/export.sh   # puts idf.py and esptool.py on PATH
  cd <jade checkout> && idf.py build
  ./main/qemu/make_flash_img.sh "$PWD/build/flash_image.bin" "$PWD/build/qemu_efuse.bin"
  ```
