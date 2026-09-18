# Scenario runbook

Every scenario file under `scenarios/`, what it actually exercises, how to run it, and
its current status as of the most recent artifact evidence in `artifacts/`. Companion to
`docs/roadmap.md` (the delivery plan); this is the "how do I actually run thing X" doc.

## Prerequisites

- Python venv at `.venv` with this package installed (`pip install -e .`).
- `interop.yaml` checkouts must exist locally and point at real source trees (see
  `interop.yaml` at repo root for current paths: `silent-pay`, `bip375-test-generator`,
  `coldcard` (coldcard-firmware), `jade` (Jade), `seedsigner`, `bitsaga-seedsigner`,
  `caravan`, `spdk`).
- Validators (`caravan`, `spdk`) never sign and never join a round; see "Validators
  (caravan, spdk)" below for what each needs built before a scenario that opts into it
  (or `check --exhaustive`) can pass.
- `qemu-system-xtensa` on `PATH`, or under `~/.espressif/tools/qemu-xtensa/*/qemu/bin/`
  (auto-discovered) -- required for any Jade scenario.
- Coldcard's own venv at `<coldcard checkout>/ENV/bin/python` -- required for any
  Coldcard scenario; the harness's own `.venv` does not need Coldcard's dependencies
  (`ckcc`, `hid`), only Coldcard's own `ENV` does, since the persistent Coldcard worker
  runs as a separate subprocess using that interpreter.
- `bip375-interop doctor` prints each checkout's VCS and tip and checks cleanliness;
  pass `--allow-dirty` if a checkout (e.g. `silent-pay`, which accumulates `target/`
  build output) is expected to be dirty. See "Pinned versions" under "Regression groups".
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
| `bip375-coldcard-jade-two-way-taproot-sighash-default` | bip375 | coldcard, jade | **Finding, not working** -- neither device rejects a non-ALL sighash with an SP output present (see below) |
| `bip375-coldcard-jade-two-way-redundant-sign` | bip375 | coldcard, jade | **Finding, not working** -- Coldcard rejects a second `sign` pass, Jade accepts it (see below) |
| `bip375-caravan-coldcard-jade-two-way` | bip375 | coldcard, jade | **Working** (also validated by the `caravan` validator, see below) |
| `bip375-spdk-coldcard-jade-two-way` | bip375 | coldcard, jade | **Working** (also validated by the `spdk` validator, see below) |
| `bip375-seedsigner-jade-two-way` | bip375 | seedsigner, jade | Blocked (SeedSigner cannot co-own a plain BIP-375 send, see below) |
| `bip375-seedsigner-coldcard-two-way` | bip375 | seedsigner, coldcard | Blocked, same root cause, confirmed against a second backend |
| `bip376-coldcard-sp-spend-single` | bip375 (BIP-376) | coldcard | **Working** (spends its own previously-received SP UTXO) |
| `bip376-jade-two-way-sp-spend-to-p2wpkh` | bip375 (BIP-376) | jade x2 | **Working end to end** |
| `bip376-jade-two-way-sp-spend-to-p2tr` | bip375 (BIP-376) | jade x2 | **Working end to end** |
| `bip376-jade-two-way-sp-spend-to-sp` | bip375 (BIP-376) | jade x2 | **Working end to end** (chained: spends an SP UTXO into a new SP output) |
| `bip376-coldcard-jade-sp-spend-to-p2wpkh` | bip375 (BIP-376) | coldcard, jade | **Working end to end** |
| `bip376-coldcard-jade-sp-spend-to-p2tr` | bip375 (BIP-376) | coldcard, jade | **Working end to end** |
| `bip376-coldcard-jade-sp-spend-to-sp` | bip375 (BIP-376) | coldcard, jade | **Working end to end** (chained: spends an SP UTXO into a new SP output) |
| `bip376-seedsigner-sp-spend-single` | bip375 (BIP-376) | seedsigner | **Working** |
| `bip376-seedsigner-jade-sp-spend-to-p2wpkh` | bip375 (BIP-376) | seedsigner, jade | **Working end to end** (SeedSigner *can* co-own a BIP-376 spend, see below) |
| `bip376-seedsigner-jade-sp-spend-to-p2tr` | bip375 (BIP-376) | seedsigner, jade | **Working end to end** |
| `bip376-seedsigner-coldcard-sp-spend-to-p2wpkh` | bip375 (BIP-376) | seedsigner, coldcard | **Working end to end** |
| `bip376-seedsigner-jade-sp-spend-to-sp` | bip375 (BIP-376) | seedsigner, jade | Blocked, same SP-*output* limitation as `bip375-seedsigner-jade-two-way` -- not a new finding |
| `musig2-sp-coldcard-jade-two-way` | musig2-sp | coldcard, jade | **Working end to end** (signed, broadcast, confirmed, recipient-verified on regtest) |
| `musig2-sp-jade-derive-first-two-way` | musig2-sp | jade x2 | **Working end to end** (derive-then-aggregate; signed, broadcast, confirmed, recipient-verified) |
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

### `bip375-coldcard-jade-two-way-taproot-sighash-default` -- finding: neither device enforces the SIGHASH_ALL requirement

BIP-375 requires a signer to reject a PSBT that carries a sighash type other than
SIGHASH_ALL on any input while a Silent Payment output is present. `coldcard-a`'s input
0 is generated with `sighash: default` (see `fixtures.py`) to probe this:

```bash
bip375-interop run-generated scenarios/bip375-coldcard-jade-two-way-taproot-sighash-default.yaml
```

Neither device performs the required rejection. Instead the run fails on an unrelated
harness invariant -- strict merge's "no field may disappear" rule -- once `jade-b`'s
`resolve-sign` round returns a contribution with input 0's `sighash_type` (0x03) *entirely
absent*, not merely rewritten:

```
error: contribution omits input 0 sighash_type (03)
```

Confirmed against `artifacts/20260917T000348.221541Z-bip375-coldcard-jade-two-way-taproot-sighash-default-b8261853/`:
`01-contribute-coldcard-a-merged.psbt` still carries input 0's SIGHASH_DEFAULT unchanged
(Coldcard's own `contribute` round on its own SIGHASH_DEFAULT input does not fail as
BIP-375 requires -- it happily contributes its Silent Payment ECDH share and proof
instead), and `02-resolve-sign-jade-b-returned.psbt` has *no* sighash_type key at all on
input 0 (confirmed by direct inspection, not just the diff summary). Jade's own input 1
already carried SIGHASH_ALL from generation, so this run does not show whether Jade
rewrites a SIGHASH_DEFAULT input of its own -- only that it silently drops the field on
an input it does not own, rather than either preserving it or rejecting the PSBT.
Coldcard's `sign` round (where it would sign its own SIGHASH_DEFAULT input) was never
reached, since the harness aborts at the first violation. Findings, not fixture
accommodations: BIP-375 compliance gaps on both Coldcard and Jade, worth reporting
upstream.

### `bip375-coldcard-jade-two-way-redundant-sign` -- finding: Coldcard rejects, Jade accepts a second `sign` pass

`redundant_sign_round: true` appends a `redundant-sign` round (all signers, after the
scenario's normal rounds) that hands every signer the already-fully-signed PSBT:

```bash
bip375-interop run-generated scenarios/bip375-coldcard-jade-two-way-redundant-sign.yaml
```

Coldcard rejects immediately:

```
error: Coldcard Error: Transaction looks completely signed already?
```

Jade does not: the same redundant pass against a same-backend two-Jade scenario
(`bip375-jade-two-way`, same `redundant_sign_round: true` addition) completes without
error -- Jade accepts and re-returns an already-complete PSBT rather than rejecting it.
Both behaviors are spec-legal (BIP-375 does not mandate rejecting a redundant sign
request); recorded here as a device-behavior finding, not something the harness works
around.

## Validators (caravan, spdk)

Independent implementations that never sign and never join a round -- they re-check a
run's own PSBT snapshots after the real signers are done. A scenario opts in with
`validators: [caravan]` / `validators: [spdk]` (or both); `check --exhaustive` forces
every `bip375`-suite scenario to run with all of `KNOWN_VALIDATORS`
(`src/bip375_interop/models.py`) regardless of what it declares. Currently opt-in only,
via the two dedicated scenarios below -- no existing scenario runs both.

### `bip375-caravan-coldcard-jade-two-way` -- working

Caravan's own TypeScript `PsbtV2` re-parses **every** PSBT snapshot the run wrote
(`CaravanAdapter.snapshot_glob = "*.psbt"`): its constructor rejects malformed silent
payment fields, bad DLEQ proofs, and output scripts that don't match its own BIP-352
derivation. Structural/cryptographic-share checking only -- it has no signer, so it
cannot verify a taproot/ECDSA signature.

```bash
bip375-interop run-generated scenarios/bip375-caravan-coldcard-jade-two-way.yaml
```

Needs the checkout built first (`npm ci && npx turbo build --filter=@caravan/psbt...`
in `<caravan checkout>`). Last confirmed passing:
`artifacts/20260917T021302.450963Z-bip375-caravan-coldcard-jade-two-way-2e50a3f0/` --
`validated: 8` (every snapshot the round dance wrote: `00-initial.psbt` through
`final.psbt`).

### `bip375-spdk-coldcard-jade-two-way` -- working

spdk-cli (`spdk-cli/` at this repo's root -- see below) runs rust-psbt's own
`Finalizer`, then `interpreter_check` (the actual Schnorr/ECDSA signature
verification step -- `finalize()` alone only *assembles* the witness from whatever
signature bytes are present, it doesn't check them), then `Extractor`. No embit
anywhere in this path, so a bug shared between fixture generation and verification
(both of which do use embit) can't hide from it. Requires a fully signed PSBT, so
unlike Caravan it only validates `final.psbt` (`SpdkAdapter.snapshot_glob =
"final.psbt"`), not every intermediate snapshot.

```bash
bip375-interop run-generated scenarios/bip375-spdk-coldcard-jade-two-way.yaml
```

Needs `spdk-cli/` built first: `cd spdk-cli && cargo build --release`. Its `Cargo.toml`
depends on spdk's `psbt` crate by git URL pinned to a `rev` (the same commit silent-pay
depends on), and the committed `Cargo.lock` fixes everything else, so the build needs no
local checkout. To test an uncommitted spdk change, override the dependency for one build:
`cargo build --release --config 'patch."https://github.com/macgyver13/spdk.git".psbt.path="<spdk checkout>/psbt"'`.
Moving the pin means bumping `rev` in `spdk-cli/Cargo.toml` and the `spdk` lock entry
together. The `spdk`
`interop.yaml` checkout entry itself is only used for dirty-checkout tracking and to
confirm it's really the spdk repo (`psbt/Cargo.toml` marker); the binary is built and run
from this repo's own `spdk-cli/`, not from that checkout -- same split as
`caravan_validate.cjs` (in-repo script) vs. Caravan's own checkout. Last confirmed
passing: `artifacts/20260917T031027.444777Z-bip375-spdk-coldcard-jade-two-way-70dc8619/` --
`validated: 1` (just `final.psbt`).

**Gotcha found while building this**: an input that already carries
`PSBT_IN_FINAL_SCRIPTWITNESS` (key `0x08`) -- SeedSigner's embit fork sets this directly
at signing time, real Coldcard/Jade output never does -- is treated by `finalize()` as
already finalized and passed through unchanged, rather than rebuilt from the raw
`tap_key_sig`/`partial_sig` field. Doesn't weaken the check (`interpreter_check` still
verifies whatever ends up in the witness either way), but it means hand-tampering a
SeedSigner-signed PSBT for a negative test has to corrupt *both* fields, not just the
raw signature record, or `finalize()` silently uses the untouched pre-built witness.

## BIP-376 (Silent Payment spend) scenarios

BIP-376 ("Spending Silent Payment outputs with PSBTs") is the spend-side counterpart to
BIP-375: `PSBT_IN_SP_TWEAK` (0x20) and `PSBT_IN_SP_SPEND_BIP32_DERIVATION` (0x1f) let a
signer spend a UTXO that is itself a previously-received Silent Payment, without
per-input BIP-341 taproot derivation. `build_bip375_fixture` (`fixtures.py`) generates
these with a new `sp-spend` input type, and now also accepts `p2wpkh`/`p2tr` (in
addition to `silent-payment`) output types, so a scenario can spend an SP-received UTXO
onward to a plain address or into a new Silent Payment. All scenarios below use
`run-generated` (in-process fixture, no external `--psbt` needed).

Every combination -- single Coldcard, two independent Jades, and mixed Coldcard+Jade --
works end to end for all three destination types, live-verified via real signature
fields (`PSBT_IN_TAP_KEY_SIG`) added by the actual simulator/QEMU firmware:

```bash
bip375-interop run-generated scenarios/bip376-coldcard-sp-spend-single.yaml
bip375-interop run-generated scenarios/bip376-jade-two-way-sp-spend-to-p2wpkh.yaml
bip375-interop run-generated scenarios/bip376-jade-two-way-sp-spend-to-p2tr.yaml
bip375-interop run-generated scenarios/bip376-jade-two-way-sp-spend-to-sp.yaml
bip375-interop run-generated scenarios/bip376-coldcard-jade-sp-spend-to-p2wpkh.yaml
bip375-interop run-generated scenarios/bip376-coldcard-jade-sp-spend-to-p2tr.yaml
bip375-interop run-generated scenarios/bip376-coldcard-jade-sp-spend-to-sp.yaml
```

**Real Coldcard derivation-path requirement found along the way**: BIP-376 spend keys
use one fixed key per account (`352h/coin_type'/account'/0h/0`, not varied per UTXO
like a receive path -- both Coldcard's `validate_silent_payment_inputs`
(`shared/silentpayments.py`) and Jade's `wallet_is_expected_sp_spend_path` (`wallet.c`)
enforce exactly this shape). The fixture's first attempt varied the last two path
components per input index, which Coldcard correctly rejected
(`"SP spend path key type must be 0h"`); fixed in `fixtures.py` to use the fixed
5-component path for every `sp-spend` input owned by the same signer, distinguishing
UTXOs by their individual `sp_tweak` instead, per spec.

**Real harness round-scheduling gap found and fixed**: the `bip375` suite's per-input
round schedule (`contribute` -> `resolve-sign` -> `sign`, in `suites.py`) exists so every
owner's ECDH share can be collected before a Silent Payment *output* is resolved. A
spend-only scenario settling to a plain P2WPKH/P2TR output has no such output to
resolve, so both owners were already fully signed after only two of the three rounds --
the redundant third round then handed a signer an already-complete PSBT. Jade silently
tolerated this; Coldcard correctly rejected it (`"Transaction looks completely signed
already?"`), which is what actually surfaced the bug. Fixed by making `scenario_rounds`
check whether any scenario output is `type: silent-payment` and, if none is, scheduling
a single round for all signers -- every existing scenario has such an output already, so
this only changes behavior for the new spend-only case.

**SeedSigner's earlier multi-owner blocker is narrower than first documented**:
`bip375-seedsigner-jade-two-way` and `bip375-three-way` are blocked because upstream
SeedSigner's embit dependency's `sign_with()` unconditionally calls the single-signer-only
`derive_sp_outputs()` whenever the PSBT has an unresolved Silent Payment *output* -- that
is a BIP-375 **send**-side limitation. It does not apply to BIP-376 **spend** inputs:
`sign_with()` only calls `derive_sp_outputs()` `if self.has_sp_outputs`, and separately
always calls `_sign_sp_spends()`, which resolves `sp_tweak` inputs generically via
`resolve_input_privkey`/`match_sp_spend_base` regardless of how many other inputs are
present or who owns them. Verified directly: `bip376-seedsigner-jade-sp-spend-to-p2wpkh`,
`-to-p2tr`, and `bip376-seedsigner-coldcard-sp-spend-to-p2wpkh` all sign correctly with
SeedSigner as a genuine co-owner alongside Jade/Coldcard -- real `PSBT_IN_TAP_KEY_SIG` on
every input. `bip376-seedsigner-jade-sp-spend-to-sp` (same signers, but the output is a
*new* Silent Payment) fails with the identical `SPValidationError` as before, confirming
the boundary is precisely "can SeedSigner co-own SP *output* construction" (no), not "can
SeedSigner co-own BIP-376 *input* spending" (yes).

```bash
bip375-interop run-generated scenarios/bip376-seedsigner-sp-spend-single.yaml
bip375-interop run-generated scenarios/bip376-seedsigner-jade-sp-spend-to-p2wpkh.yaml
bip375-interop run-generated scenarios/bip376-seedsigner-jade-sp-spend-to-p2tr.yaml
bip375-interop run-generated scenarios/bip376-seedsigner-coldcard-sp-spend-to-p2wpkh.yaml
```

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

## Regression groups

`check` selects every scenario that names a backend and runs its generated
BIP-375 cases sequentially. It writes a JSON manifest and an HTML report under
`artifacts/batches/`. The score only counts selected cases that could run:
a MuSig2-SP case with no PSBT is shown as blocked rather than passing. A PSBT stored
next to a scenario (`scenarios/<name>.psbt`, regenerated with `just musig2-psbt`) is
bound automatically; `--psbt` overrides it.

```bash
bip375-interop check --project jade --dry-run  # review local changes, selection, prerequisites
bip375-interop check --project jade            # run the selected generated cases
bip375-interop check --project jade \
  --psbt musig2-sp-coldcard-jade-two-way=out-cj/initial.psbt \
  --psbt musig2-sp-jade-derive-first-two-way=out-dfa/initial.psbt
bip375-interop check --project harness          # all scenarios, including blocked entries
```

By default the change audit compares the project checkout with `HEAD`, including
untracked files. Pass `--since <revision>` to use a different baseline. The
initial selection rule is intentionally conservative: any change to a backend
selects all scenarios that use that backend.

Supplying a MuSig2-SP PSBT includes that scenario in the batch and preserves
its final PSBT artifact. It is reported as **completed**, rather than counted
as structurally verified, until the separate `silent-pay` finalization,
broadcast, confirmation, and recipient scan complete.

The completion check requires resolved output scripts and a signature record on
every generated input. This is structural evidence only: a passing score does
not yet claim independent signature validation or regtest chain acceptance.

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

The whole sequence below is scripted, including cleanup of the throwaway node:

```bash
just musig2-regtest aggregate-then-derive   # musig2-sp-coldcard-jade-two-way
just musig2-regtest derive-then-aggregate   # musig2-sp-jade-derive-first-two-way
```

It needs `bitcoind` and `bitcoin-cli` on `PATH` (or `BITCOIND` / `BITCOIN_CLI`), and
prints `PASS` with the txid and artifact directory only after the on-chain scan finds the
recipient's output. Set `ALLOW_DIRTY=1` for a development run. The manual steps follow.

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

### Pinned versions: `interop.lock`

`interop.yaml` is gitignored because it holds local paths, so versions are pinned in the
committed `interop.lock`: one commit id per checkout name, like `Cargo.lock`.

```bash
bip375-interop pin    # write interop.lock from the current tip of every checkout
```

- A lock entry fills a checkout's unset `revision`. An explicit `revision` in
  `interop.yaml` that disagrees with the lock is a configuration error.
- A checkout whose tip differs from its pin fails with `expected X, found Y`.
- `pin` refuses a dirty git checkout even with `--allow-dirty`, because HEAD would omit
  the uncommitted changes.
- `vcs:` is optional per checkout (`git`, `jj`, `gitbutler`). When unset it is detected:
  a `.jj` directory means jj. A jj checkout reports its working-copy commit
  (`jj log -r @`), not git's HEAD, which is the working copy's parent. It is never dirty,
  because the commit id captures the files on disk. Reading it snapshots the working
  copy. `gitbutler` is accepted but currently read as plain git, which gives the workspace
  merge commit; that is not a stable pin (FIXME in `checkouts.py`).
- `artifacts/` is not committed. Each run manifest and batch report records the state of
  the checkouts it used, so a run can be recreated from `interop.lock` plus its manifest.

### Preflight

Before any scenario runs, `check` inspects every checkout the runnable scenarios and their
validators need, and confirms embit still exports the Silent Payment functions the
harness imports. All problems are reported together and nothing runs:

```
error: preflight failed:
  - jade: checkout is dirty (use --allow-dirty for development)
  - seedsigner: no checkout configured
```

The exit code is 2 and no batch is written. `--dry-run` and blocked scenarios skip it.
The embit check confirms the imported embit lives under the configured `embit` checkout
and that `group_sp_outputs_by_scan_key` returns the two-value shape in use. It cannot probe
every behavior. The `.venv` must therefore install embit editable from that checkout
(`.venv/bin/pip install -e <embit checkout>`); a copied embit in site-packages has an older
API and fails preflight.

### Expectations and labels

`expectations.yaml` (next to `interop.yaml`) holds the expected status of every scenario
for the pinned versions, with a reason and a reference. A test keeps it in step with
`scenarios/`, so a new scenario needs an entry.

| Status | Meaning |
|---|---|
| `supported` | passes end to end |
| `finding` | runs, but a device behaves in a way recorded as a finding |
| `unsupported` | a known limitation blocks it |
| `needs-external-psbt` | cannot run in `check` without a silent-pay PSBT and has no verified run |
| `unclassified` | not yet triaged; resolve on a pinned baseline run |

When the file exists, `check` compares each case with its expectation and with the newest
finalized batch of the same project, and adds a label to `report.json`, `report.html` and
the printed summary. The summary lists only the variances.

| Label | Meaning |
|---|---|
| `REGRESSION` | expected `supported`, failed |
| `FIXED` | expected not supported, passed |
| `CHANGED` | matches its expectation class but its status or reason differs from the previous run |
| `STEADY` | as expected and unchanged |
| `NOT-RUN` | expected `supported` but blocked, for example a MuSig2-SP scenario given no `--psbt` |
| `NEW` | no expectation exists |
| `UNCLASSIFIED` | its expectation is `unclassified` |

`expectations.yaml` is what `check` trusts. The Quick reference table above is narrative
and predates it: it lists four scenarios as working that currently fail verification and
are `unclassified` (`bip376-jade-two-way-sp-spend-to-p2tr`, `bip376-jade-two-way-sp-spend-to-p2wpkh`,
`bip376-seedsigner-jade-sp-spend-to-p2tr`, `bip376-seedsigner-jade-sp-spend-to-p2wpkh`).
Change a pin and its expectations in the same commit.

### Exit codes

| Result | Exit |
|---|---|
| preflight failure or other configuration error | 2 |
| every selected scenario blocked | 1 |
| with expectations: any `REGRESSION`, `UNCLASSIFIED` or `NEW` | 1 |
| with expectations: everything else, including expected findings | 0 |
| without `expectations.yaml`: any failed case | 1 |

`FAILING_LABELS` in `regression.py` decides which labels fail a run.

### Running a regression

1. `bip375-interop doctor` to see each checkout's VCS and tip.
2. `bip375-interop pin` when establishing a new baseline, then review `interop.lock`.
3. `python -m pytest -q tests` for the harness's own tests.
4. `bip375-interop check --project harness --exhaustive`. The two 2-of-2 MuSig2-SP
   scenarios use their stored `scenarios/<name>.psbt`; one with no stored PSBT and no
   `--psbt` binding is `NOT-RUN`.
5. Read the variances. A `REGRESSION` or `UNCLASSIFIED` needs a decision; a `FIXED` or
   `CHANGED` usually means `expectations.yaml` should be updated.

## Known gotchas

- **Coldcard reports regtest addresses with a `tb1p...` HRP, not `bcrt1p...`.** The
  simulator's `testing/devtest/set_seed.py` hard-codes `settings.set('chain', 'XTN')`
  (testnet) on every seed load, so the simulator always runs on testnet regardless of
  what `network` a scenario requests; the harness accepts and ignores that parameter
  for the Coldcard worker rather than validate a value it cannot apply. The underlying
  witness program is identical to what regtest would produce (confirmed by decoding
  both); this is a cosmetic simulator quirk, not a derivation bug. Compare raw
  witness-program bytes, not address strings, when cross-checking Coldcard against
  another backend.
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
