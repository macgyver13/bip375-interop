# Scenario runbook

Every scenario file under `scenarios/`, what it actually exercises, how to run it, and
its current status as of the most recent artifact evidence in `artifacts/`. Companion to
`docs/roadmap.md` (the delivery plan); this is the "how do I actually run thing X" doc.

## Prerequisites

- Python venv at `.venv` with this package installed (`pip install -e .`).
- `interop.yaml` checkouts must exist locally and point at real source trees (see
  `interop.yaml` at repo root for current paths: `silent-pay`, `coldcard`
  (coldcard-firmware), `jade` (Jade), `seedsigner`, `caravan`, `spdk`, `embit`). Add a
  `bitsaga-seedsigner` checkout only to run its two MuSig2 scenarios with a PSBT.
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

All commands below run from the repo root with the venv active (`source .venv/bin/activate`).

### Setting up a baseline

Each baseline profile (`baseline/interop.yaml`, `baseline-musig2/interop.yaml`) names a
path for every checkout, and its `interop.lock` pins each one's commit. To create and
build those checkouts:

1. **Fetch.** Use **Fetch pinned sources** in the desktop app, or:
   ```bash
   bip375-interop --config baseline/interop.yaml fetch --in-place
   ```
   Each checkout is cloned at its pinned commit into the path `interop.yaml` names. A
   path already at that commit is skipped. A path at another commit is an error and is
   never overwritten. The two baselines pin different `spdk` and `embit` commits, so
   give each profile its own directory.
2. **Set up the shell.** Jade's build needs ESP-IDF on `PATH`. On macOS, current clang
   rejects the micropython Coldcard builds unless its warnings stay warnings, and Jade's
   `switch_to.sh` needs GNU sed (`brew install gnu-sed`):
   ```bash
   source <esp-idf checkout>/export.sh
   export CFLAGS_EXTRA=-Wno-error
   export PATH="$(brew --prefix gnu-sed)/libexec/gnubin:$PATH"
   ```
   If `CARGO_TARGET_DIR` is set, the Rust builds (`spdk-cli`, silent-pay's `sp-demo`)
   put their binaries there and the harness looks for them there, so keep it the same
   when building and running.
3. **Build.** This runs every checkout's build steps. `--dry-run` lists them, and names
   (`build embit caravan`) limit it to those checkouts:
   ```bash
   bip375-interop --config baseline/interop.yaml build
   ```
4. **Check.** Run **Check setup**, then **Verify now**. A `preflight failed` error names
   whatever is still missing or unbuilt.

The `embit` step installs the profile's embit into `.venv`, which every profile shares,
and preflight requires the imported embit to be the selected profile's checkout. After
switching profiles, run `build embit` with the new profile's `--config`.

## Desktop app and CLI workflow

The Rust desktop app is a guided front end to this CLI. Start it with
`cargo run --manifest-path gui/Cargo.toml` from the repository root. It uses
`.venv/bin/python` when present, otherwise `python3`, and runs the CLI from the
repository root. A compiled binary also accepts the repository path as its first
argument. It does not bundle the Python harness or the external checkouts.

1. Select a profile. **BIP-375 baseline** is the default and covers that pinned line;
   **BIP-375 + MuSig2 baseline** runs the full gate; **Live development** uses the root
   `interop.yaml`. Select **harness** to check all selected scenarios, or one changed
   backend for a narrower regression group. A new worktree has no `interop.yaml`
   because it is gitignored. Use **Create live profile** to copy the main checkout's
   local settings, or the example config when there is no main checkout profile;
   review the checkout paths before continuing. If a baseline names checkout paths you
   do not have, use **Fetch pinned sources**. It clones each checkout at its locked
   revision into the path the profile's `interop.yaml` names, so keep the same profile
   selected. The CLI equivalent is `bip375-interop --config
   baseline-musig2/interop.yaml fetch --in-place`. Without `--in-place`, `fetch`
   clones under `.checkouts/` and writes `interop.fetched.yaml`, which the **fetched
   baseline** profiles use. Build the fetched checkouts as described in Prerequisites
   before verification.
2. With **Live development**, **Compare with** defaults to **BIP-375 baseline**.
   Choose the baseline and use **Compare with
   baseline** to compare the live checkout paths with the selected baseline's
   `interop.lock`. Baseline checkout directories need not exist for this comparison.
   The table separates revision differences from uncommitted changes and reports
   missing checkouts or missing pins individually. **Review delta** shows local
   commit history, committed file differences, and staged/unstaged/untracked files.
   History is limited to the latest 50 live-only commits. If the pinned commit is
   unavailable locally, the revision difference remains visible with an explanation;
   comparison does not fetch sources.
   **Preview affected cases** selects the one differing supported backend, or
   **harness** for multiple differences or a codebase without a dedicated selector.
   Comparing works with dirty checkouts even when **Include uncommitted checkout
   changes** is off; enable that option to verify dirty checkouts.
   The comparison baseline only controls the audit. It does not select a different
   run profile, scenario suite, expectation file, or release gate.
   **Check setup** refreshes the comparison, then calls `doctor` and lists checkout revisions and dirty state.
   **Preview cases** calls `check --dry-run` with the same validator/release selection
   as the actual run and refreshes the live comparison. A blocked case names its missing PSBT or unsupported suite.
3. **Verify now** runs the check in the background. With **BIP-375 + MuSig2 baseline**
   (or its fetched profile) and **harness**, it starts the full `check --release` gate,
   including the Interop Lab stage and both MuSig2 regtest legs. The progress bar counts cases;
   the active case or MuSig2 regtest leg is named. At completion, read the coverage,
   individual labels, and each case's reason. **Open full HTML report** opens the
   saved report in the default browser; its path remains visible for archival review.
   A `completed` MuSig2 signing round is not end-to-end proof;
   only a regtest leg that prints `PASS` is counted as such.
   Development verification records the selected baseline lock and live checkout
   comparison in its JSON manifest and refreshes the table. Refreshing after further
   edits flags differences from the snapshot recorded for that verification; the
   report retains the original snapshot. Use **Compare with baseline** or **Check
   setup** to refresh after editing sources or moving the baseline lock.
4. To accept development changes into a baseline, first review and verify them,
   then put the accepted commits in the baseline profile's checkout paths.
   Select that original baseline profile and use **Preview pin changes** to compare its
   clean current revision with the profile's lock. **Update pins** then writes that
   lock. Rerun the full check and review `expectations.yaml`; commit the lock and
   expectation changes together. `pin` refuses dirty checkouts.

The equivalent CLI sequence for the full gate is:

```sh
bip375-interop --config baseline-musig2/interop.yaml doctor
bip375-interop --config baseline-musig2/interop.yaml check --project harness --release --dry-run
bip375-interop --config baseline-musig2/interop.yaml check --project harness --release
```

Use `--allow-dirty` before the subcommand for a development run. The result then
records a non-reproducible checkout state. `check --progress-json` emits JSON lines
on stderr for preflight, case start/completion, and each MuSig2 leg; stdout keeps
the single final JSON summary. Exit 2 means setup/preflight stopped execution,
exit 1 means a failing label, no verified case, or a failed release leg, and exit 0
means the current expectations were met. Exit 0 can still include known findings
or blocked scenarios, so inspect the report's coverage and reasons.

The CLI equivalents for live comparison and a preview with a comparison snapshot are:

```sh
bip375-interop --config interop.yaml compare --baseline-lock baseline-musig2/interop.lock
bip375-interop --config interop.yaml --allow-dirty check --project harness --exhaustive --baseline-lock baseline-musig2/interop.lock --dry-run
```

Remove `--dry-run` to verify and save the comparison in the batch's JSON manifest.

For a baseline change, preview before writing:

```sh
bip375-interop --config baseline-musig2/interop.yaml pin --dry-run
bip375-interop --config baseline-musig2/interop.yaml pin
```

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
| `bip375-coldcard-jade-three-way` | bip375 | coldcard, jade, coldcard | **Working**: the three-way flow with a second Coldcard in place of SeedSigner (see below) |
| `bip375-coldcard-jade-two-way-taproot-sighash-default` | bip375 | coldcard, jade | **Working**: SIGHASH_DEFAULT on a taproot input, accepted by both devices and both validators (see below) |
| `bip375-coldcard-jade-two-way-redundant-sign` | bip375 | coldcard, jade | **Working**: both devices return a fully signed PSBT unchanged (see below) |
| `bip375-caravan-coldcard-jade-two-way` | bip375 | coldcard, jade | **Working** (also validated by the `caravan` validator, see below) |
| `bip375-spdk-coldcard-jade-two-way` | bip375 | coldcard, jade | **Working** (also validated by the `spdk` validator, see below) |
| `bip375-seedsigner-jade-two-way` | bip375 | seedsigner, jade | Blocked (SeedSigner cannot co-own a plain BIP-375 send, see below) |
| `bip375-seedsigner-coldcard-two-way` | bip375 | seedsigner, coldcard | Blocked, same root cause, confirmed against a second backend |
| `bip376-coldcard-sp-spend-single` | bip375 (BIP-376) | coldcard | **Working** (spends its own previously-received SP UTXO) |
| `bip376-jade-two-way-sp-spend-to-p2wpkh` | bip375 (BIP-376) | jade x2 | **Working** -- fixed, see below (was: Jade never cleared inputs/outputs-modifiable for a plain-destination spend) |
| `bip376-jade-two-way-sp-spend-to-p2tr` | bip375 (BIP-376) | jade x2 | **Working** -- same fix as the P2WPKH variant, see below |
| `bip376-jade-two-way-sp-spend-to-sp` | bip375 (BIP-376) | jade x2 | **Working end to end** (chained: spends an SP UTXO into a new SP output) |
| `bip376-coldcard-jade-sp-spend-to-p2wpkh` | bip375 (BIP-376) | coldcard, jade | **Working end to end** |
| `bip376-coldcard-jade-sp-spend-to-p2tr` | bip375 (BIP-376) | coldcard, jade | **Working end to end** |
| `bip376-coldcard-jade-sp-spend-to-sp` | bip375 (BIP-376) | coldcard, jade | **Working end to end** (chained: spends an SP UTXO into a new SP output) |
| `bip376-seedsigner-sp-spend-single` | bip375 (BIP-376) | seedsigner | **Working** |
| `bip376-seedsigner-jade-sp-spend-to-p2wpkh` | bip375 (BIP-376) | seedsigner, jade | **Working** -- fixed, see below (was: Jade dropped SeedSigner's input sighash_type) |
| `bip376-seedsigner-jade-sp-spend-to-p2tr` | bip375 (BIP-376) | seedsigner, jade | **Working** -- same fix as the P2WPKH variant, see below |
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

### `bip375-coldcard-jade-three-way`: working

The same three inputs and SP output as `bip375-three-way`, with a second Coldcard
(`coldcard-c`, seed `test-c`) as the third signer, so the three-party flow is covered
while SeedSigner cannot co-own a send:

```bash
bip375-interop run-generated scenarios/bip375-coldcard-jade-three-way.yaml
```

`coldcard-a` and `jade-b` contribute their shares, `coldcard-c` resolves the output and
signs its input, then `coldcard-a` and `jade-b` sign theirs.

### `bip375-coldcard-jade-two-way-taproot-sighash-default`: working

`coldcard-a`'s input 0 is generated with `sighash: default` (see `fixtures.py`), an
explicit PSBT_IN_SIGHASH_TYPE of 0 on a P2TR input:

```bash
bip375-interop run-generated scenarios/bip375-coldcard-jade-two-way-taproot-sighash-default.yaml
```

BIP-375 0.1.4 accepts SIGHASH_DEFAULT on taproot inputs, as BIP-352 recommends, so this
is no longer a device compliance probe. This scenario checks Coldcard signing with
SIGHASH_DEFAULT and Jade preserving that field on an input it does not own. It does not
test Jade signing its own SIGHASH_DEFAULT input:

- Coldcard accepts ALL or DEFAULT.
- Jade keeps the field on the input it does not own. It used to drop it, which failed
  strict merge with `contribution omits input 0 sighash_type (03)`: libwally stored a
  sighash of 0 the same as "not given". Fixed by libwally's `has_sighash`
  (macgyver13/libwally-core `sp-core`, first commit) and Jade `sp-musig` 80b94ce5.
- Caravan used to reject every snapshot with `PsbtV2 input 0 uses non-SIGHASH_ALL (0)
  with silent payments`. Fixed in `fix/sp-sighash-default-taproot` (1293c653).
- SPDK's finalizer, from rust-psbt, used to fail with `Finalizer sighash type error`:
  `check_partial_sigs_sighash_type` converted every input's sighash to an ECDSA type,
  and 0 is not one. Fixed in rust-psbt `fix/taproot-default-sighash-finalize`
  (2a0cb75a), which spdk f93de4da and `spdk-cli` now pin.

### `bip375-coldcard-jade-two-way-redundant-sign`: working

`redundant_sign_round: true` appends a `redundant-sign` round (all signers, after the
scenario's normal rounds) that hands every signer the already-fully-signed PSBT:

```bash
bip375-interop run-generated scenarios/bip375-coldcard-jade-two-way-redundant-sign.yaml
```

Both devices treat it as a no-op and return the PSBT unchanged, so the redundant round
adds no field. Coldcard used to refuse with `Coldcard Error: Transaction looks
completely signed already?`, an upstream guard that ran before any Silent Payment
check. For a PSBT with SP outputs, Coldcard now skips that refusal, still verifies the
ECDH shares, DLEQ proofs and output scripts, and signs nothing (`sp-core-pinned`
733fe43b). A PSBT without SP outputs keeps the upstream refusal.

## Validators (caravan, spdk)

Independent implementations that never sign and never join a round -- they re-check a
run's own PSBT snapshots after the real signers are done. A scenario opts in with
`validators: [caravan]` / `validators: [spdk]` (or both). `check --exhaustive` and
`check --release` force every `bip375`-suite scenario to run with all of
`KNOWN_VALIDATORS` (`src/bip375_interop/models.py`) regardless of what it declares.
`check --release` also runs both MuSig2 regtest architectures and fails unless each
script prints `PASS`. A missing Caravan dist or `spdk-cli` binary is a preflight
error, not a skipped validator. Opt-in scenarios are the two dedicated cases below.

### Interop Lab release stage

`bip375-interop validate-psbt path/to/file.psbt` checks one file through the local
parser, Caravan, SPDK (when the file is fully signed), and the pinned PSBT Interop Lab
`0.11.0` parser/native-adapter matrix. It prints `passed`, `failed`, or `not run` for
each stage and an overall `passed`, `failed`, or `partial` verdict. A missing Docker
daemon, image, or `npx` makes the Lab stage `not run` with a reason. SPDK is `not run`
for an intermediate PSBT. A failed stage makes the command exit nonzero.

`check --release` runs Interop Lab on every PSBT snapshot produced by each BIP-375
scenario, alongside Caravan on every snapshot and SPDK on the final PSBT. Its run
manifest records all four stage results and the individual Lab findings. The batch
directory contains `report.json`, `interop-lab.junit.xml`, and `interop-lab.sarif`.
Docker images for the pinned native adapters must already be built; the stage does
not pull or replace them during a release check. The pinned commands and fixture
matrix are in [`interop-lab/README.md`](../interop-lab/README.md).

`expectations.yaml` names the exact `baseline/interop.lock` SHA-256 and the allowed
Interop Lab findings. The release gate checks that digest before running and never
rewrites either file. The four allowed findings describe observed behavior in
Interop Lab 0.11.0: bundled JS and libwally reject unresolved SP outputs; rust-psbt-v2
adds an empty output script on roundtrip; libwally drops a zero global
`TX_MODIFIABLE` field. Other parser failures or PSBT field changes fail the stage.

The manifest claims `evidence` only for a full, strict, intent-declaring BIP-375 run
with both independent validators passed and Interop Lab passed on every snapshot.
Missing Docker or another `not run` stage is `not-evidence`, even if signing completed.
Structural and combiner modes keep their weaker scope. A Lab pass with an explicitly
allowed finding records that finding; it does not claim that the affected adapter
roundtrips the PSBT unchanged.

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

Needs `spdk-cli/` built first: `cd spdk-cli && cargo build --release`. The harness runs
`$CARGO_TARGET_DIR/release/spdk-cli` when that is set, else `spdk-cli/target/release/`.
Its `Cargo.toml` depends on spdk's `psbt` crate by git URL pinned to a `rev` (the same commit silent-pay
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

**SeedSigner/embit finalization issue**: its taproot signing path sets both
`PSBT_IN_TAP_KEY_SIG` and `PSBT_IN_FINAL_SCRIPTWITNESS` (key `0x08`) during signing.
BIP-376 assigns the witness construction to the Input Finalizer, which must then
remove `PSBT_IN_TAP_KEY_SIG` and the SP spend fields. BIP-370 requires the finalizer
to retain the five v2 input transaction fields needed for extraction. Coldcard and
Jade return signing fields without prematurely finalizing their inputs.
`spdk-cli` treats an existing final witness as finalized and verifies that witness;
it does not rebuild it from `tap_key_sig`. This still verifies the extracted spend,
but a negative test that changes only `tap_key_sig` does not change the spend being
verified. The upstream embit behavior should be corrected; Jade and Coldcard do not
need to read final witnesses to address it.

## BIP-376 (Silent Payment spend) scenarios

BIP-376 ("Spending Silent Payment outputs with PSBTs") is the spend-side counterpart to
BIP-375: `PSBT_IN_SP_TWEAK` (0x20) and `PSBT_IN_SP_SPEND_BIP32_DERIVATION` (0x1f) let a
signer spend a UTXO that is itself a previously-received Silent Payment, without
per-input BIP-341 taproot derivation. `build_bip375_fixture` (`fixtures.py`) generates
these with a new `sp-spend` input type, and now also accepts `p2wpkh`/`p2tr` (in
addition to `silent-payment`) output types, so a scenario can spend an SP-received UTXO
onward to a plain address or into a new Silent Payment. All scenarios below use
`run-generated` (in-process fixture, no external `--psbt` needed).

Every combination, single Coldcard, two independent Jades, and mixed Coldcard+Jade,
works end to end for the chained SP destination and for single Coldcard, live-verified
via real signature fields (`PSBT_IN_TAP_KEY_SIG`) added by the actual simulator/QEMU
firmware. The two-Jade pair for the plain P2WPKH/P2TR destinations was blocked by a
Jade bug, now fixed; see "Jade never clears inputs/outputs-modifiable for a plain
BIP-376 spend" below.

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
present or who owns them. Verified directly: `bip376-seedsigner-coldcard-sp-spend-to-p2wpkh` signs correctly with
SeedSigner as a genuine co-owner alongside Coldcard, real `PSBT_IN_TAP_KEY_SIG` on every
input. The Jade pairing for a plain destination (`bip376-seedsigner-jade-sp-spend-to-p2wpkh`,
`-to-p2tr`) was blocked by an unrelated Jade bug, now fixed: see "Jade drops a co-owner's
sighash_type on a BIP-376 spend" below. `bip376-seedsigner-jade-sp-spend-to-sp` (same
signers, but the output is a *new* Silent Payment) fails with the identical
`SPValidationError` as before, confirming the boundary is precisely "can SeedSigner
co-own SP *output* construction" (no), not "can SeedSigner co-own BIP-376 *input*
spending" (yes).

```bash
bip375-interop run-generated scenarios/bip376-seedsigner-sp-spend-single.yaml
bip375-interop run-generated scenarios/bip376-seedsigner-jade-sp-spend-to-p2wpkh.yaml
bip375-interop run-generated scenarios/bip376-seedsigner-jade-sp-spend-to-p2tr.yaml
bip375-interop run-generated scenarios/bip376-seedsigner-coldcard-sp-spend-to-p2wpkh.yaml
```

### Jade never clears inputs/outputs-modifiable for a plain BIP-376 spend -- fixed

`bip376-jade-two-way-sp-spend-to-p2tr` and `bip376-jade-two-way-sp-spend-to-p2wpkh`
(two independent Jades, plain P2TR/P2WPKH destination) used to fail verification with
`final PSBT still has inputs-modifiable set`. Since there is no Silent Payment output to
resolve, `scenario_rounds` schedules a single `resolve-sign` round for both signers (see
"Real harness round-scheduling gap found and fixed" above): each Jade contributes its
own input once, and neither ever cleared the PSBT's global inputs/outputs-modifiable
flags (global field `0x06`), confirmed by inspecting every intermediate PSBT in the run:
the byte stayed `0x03` from `00-initial.psbt` through `final.psbt`.

Compared directly against `bip376-coldcard-jade-sp-spend-to-p2tr`, the identical round
shape (per-input, single `resolve-sign` round, plain P2TR destination) with Coldcard as
the first signer instead of Jade: Coldcard clears the flags to `0x00` on its own single
pass, before Jade is even asked to contribute. Jade did correctly clear the flags when
it is the *resolving* signer in the 3-round SP-output dance
(`bip376-jade-two-way-sp-spend-to-sp` passes, and its `02-resolve-sign-jade-b-merged.psbt`
shows the flags cleared there, via `wally_psbt_sp_musig_round1`/`round2` in
`components/libwally-core/upstream/src/silentpayments.c`). So this was specific to the
plain-destination, single-round shape: the plain-spend signing loop in
`main/process/sign_psbt.c` (`sign_psbt()`) never touched `tx_modifiable_flags` at all --
that logic only existed on the SP-MuSig2 resolve path.

BIP-370's Signer role is explicit that this is required: *"a signer must update the
PSBT_GLOBAL_TX_MODIFIABLE field after signing inputs... If the Signer added a signature
that does not use SIGHASH_ANYONECANPAY, the Input Modifiable flag must be set to
False"* (mirrored for Outputs Modifiable/SIGHASH_NONE).

Fixed in Jade (`main/process/sign_psbt.c`, `sign_psbt()`): after the per-input signing
loop, for every input signed this round, inspect the resolved sighash and clear
`WALLY_PSBT_TXMOD_INPUTS` unless every signed input used `SIGHASH_ANYONECANPAY`, and
clear `WALLY_PSBT_TXMOD_OUTPUTS` unless every signed input used `SIGHASH_NONE`. Verified
against a rebuilt QEMU flash image: both scenarios now produce a `final.psbt` with global
field `0x06` at `0x00`.

```bash
bip375-interop run-generated scenarios/bip376-jade-two-way-sp-spend-to-p2tr.yaml
```

### Jade drops a co-owner's sighash_type on a BIP-376 spend -- fixed

`bip376-seedsigner-jade-sp-spend-to-p2tr` and `bip376-seedsigner-jade-sp-spend-to-p2wpkh`
(SeedSigner owns input 0, Jade owns input 1) used to fail the strict additive-merge check
with `contribution omits input 0 sighash_type (03)`: Jade's own `resolve-sign`
contribution, for its own input 1, came back with input 0's `PSBT_IN_SIGHASH_TYPE`
missing entirely, even though Jade does not own that input and the field was present and
unchanged through SeedSigner's own contribution round.

Compared directly against `bip376-seedsigner-coldcard-sp-spend-to-p2wpkh`, the identical
scenario shape with Coldcard substituted for Jade as the second signer: Coldcard
preserves SeedSigner's input 0 `sighash_type` correctly all the way to `final.psbt`. Jade
paired with another Jade also preserves a co-owner's `sighash_type` correctly (no merge
violation in `bip376-jade-two-way-sp-spend-to-p2tr`'s own round, independent of that
scenario's separate inputs-modifiable finding above), so this was not "Jade drops any
field it doesn't own" in general; it reproduced specifically when the co-owned input was
already finalized. A byte-level diff of every intermediate PSBT showed SeedSigner's input
0 arriving with a clean, canonical `PSBT_IN_SIGHASH_TYPE` (key `03`, value `01000000`)
alongside `PSBT_IN_FINAL_SCRIPTWITNESS` -- ie. SeedSigner had already finalized its own
input before handing off to Jade. Root cause was in libwally, not Jade's own PSBT code:
`psbt.c`'s serializer treats a finalized input's sighash/partial-sig/redeem-script/
witness-script fields as stale and skips re-emitting them unless
`WALLY_PSBT_SERIALIZE_FLAG_REDUNDANT` is passed, per BIP-174's "should be cleared"
finalizer guidance -- and Jade's own `serialise_psbt()` (`main/process/sign_psbt.c`)
always passed `flags = 0`. Jade never finalizes SeedSigner's input, so this amounted to
Jade dropping a field it had no business touching, violating BIP-174's Combiner rule
that *"the resulting PSBT must contain all of the key-value pairs from each of the
PSBTs"* combined.

Fixed by passing `WALLY_PSBT_SERIALIZE_FLAG_REDUNDANT` to both the `wally_psbt_get_length`
and `wally_psbt_to_bytes` calls in `serialise_psbt()`. Verified against a rebuilt QEMU
flash image: both scenarios now produce a `final.psbt` with SeedSigner's input 0
`PSBT_IN_SIGHASH_TYPE` intact.

```bash
bip375-interop run-generated scenarios/bip376-seedsigner-jade-sp-spend-to-p2tr.yaml
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
   cd ~/src/silent-pay
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
cd ~/src/silent-pay
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
  a `.jj` directory means jj; a symbolic ref under `gitbutler/` means GitButler;
  otherwise git. Explicit `vcs: gitbutler` forces parent-tip interpretation. A jj
  checkout reports its working-copy commit (`jj log -r @`), not git's HEAD, which is
  the working copy's parent. It is never dirty, because the commit id captures the
  files on disk. Reading it snapshots the working copy. A GitButler checkout derives
  its revision from `git rev-list --parents -n1 HEAD`: one parent id, or a sorted
  comma-separated set of applied-stack tips. Recreate that workspace by merging every
  listed tip; an empty applied stack resolves to the base parent. Dirty status,
  including modified submodules reported by `git status`, still prevents `pin`.
- Known build byproducts are not dirty: libngu's bech32 patch in coldcard and the
  ESP-IDF rewrite of `dependencies.lock.esp32` in jade (`KNOWN_BYPRODUCTS` in
  `checkouts.py`). A moved libngu commit still is. Checkout states list the byproducts
  they excused.
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
the printed summary. The report headline is the label counts. The summary lists each
variance with its status, reason, expectation, and previous run, so the reason is in
the command output.

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
5. Read the variances in the `check` output. Each one includes the status, reason,
   expectation, and previous run. A `REGRESSION` or `UNCLASSIFIED` needs a decision; a
   `FIXED` or `CHANGED` usually means `expectations.yaml` should be updated.

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
