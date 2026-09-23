# Delivery roadmap

## Milestone 1: reproducible native lanes

- Pin source and submodule revisions and capture tool versions.
- Build each emulator from a disposable source snapshot.
- Run Coldcard, Jade, upstream SeedSigner, and BitSaga native Silent Payment suites.
- Import BIP-375 vectors from `bip375-test-generator` without copying them into tracked
  firmware fixture directories.

## Milestone 2: external fixture adapters

- [x] Generate one-, two-, and three-owner plain BIP-375 PSBTv2 scenarios.
  The first deterministic P2WPKH-only SeedSigner/Jade two-owner scenario's live QEMU run
  was run down: it fails deterministically (see the SeedSigner multi-party finding
  below), not from a QEMU/harness defect, so it is a real, documented blocker rather
  than something to keep retrying. Two real bugs surfaced and were fixed while
  expanding scope:
  - `build_bip375_fixture` (`fixtures.py`) rejected any `contribution_mode` other than
    `per-input`, which silently broke the *already-shipped* one-owner
    `bip375-seedsigner-single` scenario (it uses `global` mode) even though
    `scenario_rounds` has always supported the global/single-owner shape. Fixed by
    dropping the redundant check -- fixture shape does not depend on contribution mode,
    only round scheduling does, and that is already validated in `suites.py`.
  - `build_bip375_fixture` supported P2WPKH inputs only, which blocked evaluating
    taproot inputs at all and made the existing `bip375-three-way.yaml` (which
    declared a P2TR input for `jade-b`) impossible to generate. Added P2TR key-path
    input support (BIP86 test path, `taproot_internal_key` / `taproot_bip32_derivations`
    populated the way `embit`'s own SP signing code expects).
  - One-owner: `bip375-seedsigner-single` (P2WPKH) and the new
    `bip375-seedsigner-single-taproot` (P2TR) both pass end to end, confirming
    SeedSigner's software signer resolves and signs a taproot key-path SP input
    correctly when it is the sole owner.
  - Two-owner, same backend: new `bip375-jade-two-way` (two independent Jade QEMU
    instances) and `bip375-coldcard-two-way` (two segregated Coldcard simulator
    instances, distinguished by PID-keyed socket paths) both pass end to end.
  - Two-owner, pairwise mixed: `bip375-coldcard-jade-two-way` (P2WPKH) continues to
    pass; the new `bip375-coldcard-jade-two-way-taproot` (both inputs P2TR) also passes
    after a real interop fix (below). Any pairing involving `seedsigner` is blocked --
    see the finding below; `bip375-seedsigner-coldcard-two-way` was added and run to
    confirm the identical failure against a second backend, not just Jade.
  - Three-way: `bip375-three-way` (coldcard P2WPKH, jade P2TR, seedsigner P2WPKH) now
    generates and runs; `coldcard-a` and `jade-b` both complete their `contribute`
    round cleanly, and the run fails at `seedsigner-c`'s `resolve-sign` round for the
    same reason as the two-owner case. The scenario previously could not even be
    generated (see the two fixture bugs above: it needs P2TR input support, and its
    original `change` output violated `build_bip375_fixture`'s single-SP-output
    contract, so the output was simplified to one SP payment sized to leave a 1,000 sat
    fee against all three inputs).
  - **Real interop bug found evaluating taproot inputs**: the first
    `bip375-coldcard-jade-two-way-taproot` attempt failed merging with `conflicting
    input 1 type_0x3 (03)` -- `jade-b`'s `resolve-sign` contribution rewrote its own
    taproot input's `PSBT_IN_SIGHASH_TYPE` from `SIGHASH_DEFAULT` (`0x00000000`, what
    the fixture pre-set) to explicit `SIGHASH_ALL` (`0x01000000`). Both values are
    permitted by BIP-341/embit's own `sign_with` check and are functionally equivalent
    for a taproot key-path spend, but the harness's strict-merge policy correctly
    treats any field mutation it did not explicitly allow-list as suspect, per this
    milestone's own "reject transaction-intent mutation" goal. Rather than loosen the
    merge policy, `build_bip375_fixture` now pre-sets `SIGHASH_ALL` for P2TR inputs too
    (matching what P2WPKH inputs already used, and what Jade's firmware itself
    produces), so no signer needs to touch the field at all.
  - **SeedSigner cannot participate in any multi-owner plain BIP-375 scenario**,
    confirmed as the root cause of the original two-way scenario's failure and
    reproduced identically whether SeedSigner is first, last, same-backend-paired
    (moot -- would need a second SeedSigner worker, which hits the same bug), or paired
    with a different backend (Coldcard). `SeedSignerWorker.process`
    (`signer_worker.py`) calls upstream SeedSigner's `embit.silent_payments.psbt.
    SilentPaymentsPSBT.sign_with` unconditionally for every round; `sign_with` always
    calls `derive_sp_outputs`, whose own docstring says it is a "BIP-375 single-signer
    Silent Payment send: this signer controls every eligible input and acts as its own
    output generator." It unconditionally clears any existing
    `PSBT_GLOBAL_SP_ECDH_SHARE`/`PSBT_GLOBAL_SP_DLEQ` contributions and recomputes them
    from only the private keys it derives locally; if any eligible input belongs to a
    different signer it raises `SPValidationError("input(s) ... belong to another
    signer; multi-party Silent Payment sends are not supported.")`. This embit fork
    also has no per-input `PSBT_IN_SP_ECDH_SHARE`/`PSBT_IN_SP_DLEQ` (BIP-375's actual
    multi-party contribution fields) implementation at all -- `psbt.py` only ever
    strips those keys as unknown, with a `# FUTURE` comment. Coldcard and Jade do not
    hit this because their real firmware implements BIP-375's per-input multi-party
    protocol natively; `SeedSignerWorker` is a thin wrapper around software that does
    not. This is a genuine upstream limitation (embit/SeedSigner), not a harness bug --
    consistent with how the BitSaga `_sp_groups` bug in Milestone 3 was handled, it is
    documented here rather than worked around by reimplementing BIP-375's per-input
    ECDH-share/DLEQ scheme inside this harness. `bip375-seedsigner-jade-two-way` and
    the SeedSigner leg of `bip375-three-way` stay blocked until upstream SeedSigner (or
    its embit dependency) adds real multi-party BIP-375 send support.
- [x] Keep each virtual signer in an isolated worker process and strictly merge PSBT maps.
- [x] Permit only BIP-375-authorized output-script resolution and modifiable-flag clearing.
- [x] Run an unresolved PSBT through the actual upstream SeedSigner BIP-375 runtime.
- [x] Overlay external Coldcard fixtures in a disposable checkout copy.
- [x] Add arbitrary-PSBT transports for Coldcard and Jade.
  Jade has a persistent QEMU worker with dynamic host-port allocation. Coldcard has a
  persistent headless segregated simulator worker and a reproducible one-owner external-fixture smoke lane.
- [x] Exercise same-backend, pairwise mixed, and Coldcard/Jade/SeedSigner three-way runs.
  See the scenario-by-scenario results above; every combination not involving
  SeedSigner as a co-owner passes, and the SeedSigner blocker is documented rather
  than silently left unexercised.
- [x] Add BIP-376 (spending Silent Payment outputs with PSBTs) input support to
  generated fixtures, and prove sending a previously-received SP UTXO onward to a
  plain P2WPKH/P2TR address or into a new Silent Payment, on Coldcard and Jade.
  BIP-376 (`PSBT_IN_SP_TWEAK` 0x20, `PSBT_IN_SP_SPEND_BIP32_DERIVATION` 0x1f) is the
  spend-side counterpart to BIP-375's send side: an input whose UTXO is itself a
  Silent Payment, signable from the base spend key plus a per-input tweak instead of
  ordinary BIP-341 taproot derivation. `build_bip375_fixture` (`fixtures.py`) gained a
  new `sp-spend` input type (`spend_key.sp_spend_tweak(tweak)` builds the tweaked
  output key directly -- no `script.p2tr()`, since BIP-352 outputs are not
  additionally BIP-341-tweaked) and now accepts `p2wpkh`/`p2tr` output types
  alongside the existing `silent-payment` one, so a scenario can spend an SP-received
  UTXO onward to a plain address, not only into another Silent Payment.
  New scenarios: `bip376-coldcard-sp-spend-single`,
  `bip376-jade-two-way-sp-spend-to-{p2wpkh,p2tr,sp}`,
  `bip376-coldcard-jade-sp-spend-to-{p2wpkh,p2tr,sp}`,
  `bip376-seedsigner-sp-spend-single`,
  `bip376-seedsigner-jade-sp-spend-to-{p2wpkh,p2tr}`,
  `bip376-seedsigner-coldcard-sp-spend-to-p2wpkh` -- every combination (single
  Coldcard, two independent Jades, mixed Coldcard+Jade, and SeedSigner as a co-owner
  with either device) x every destination type works end to end, confirmed via real
  `PSBT_IN_TAP_KEY_SIG` signatures added by the actual simulator/QEMU firmware, not
  just a non-error exit code.
  - **Real Coldcard/Jade derivation-path requirement found**: BIP-376 spend keys use
    one fixed key per account (`352h/coin_type'/account'/0h/0`), not a key varied per
    UTXO the way a receive path is -- confirmed identical in both Coldcard's
    `validate_silent_payment_inputs` (`shared/silentpayments.py`) and Jade's
    `wallet_is_expected_sp_spend_path` (`wallet.c`). Uniqueness across payments to the
    same spend key comes from each input's own `sp_tweak`, not from deriving a
    different base key per input. The fixture's first attempt varied the path per
    input index and was correctly rejected by real Coldcard firmware
    (`"SP spend path key type must be 0h"`); fixed to use the one fixed path per
    signer that both devices require.
  - **Real harness round-scheduling bug found and fixed, not a device bug**: mixed
    Coldcard+Jade sp-spend scenarios with a plain P2WPKH/P2TR output initially failed
    with Coldcard rejecting `"Transaction looks completely signed already?"` on what
    looked like the first, unsigned round. Root cause was in this harness's
    `suites.py`, not Coldcard: `scenario_rounds()`'s per-input schedule
    (`contribute` -> `resolve-sign` -> `sign`) exists so every owner's ECDH share can
    be collected before a Silent Payment *output* is resolved; a plain-output
    spend-only scenario has no such output to resolve, so both owners were already
    fully signed after only the first two rounds, and the third, redundant round
    handed a signer an already-complete PSBT. Jade silently tolerated being asked to
    re-sign it (a no-op); Coldcard correctly rejected it, which is what actually
    surfaced the bug -- confirmed by reproducing the identical failure with two
    independent Coldcard instances and no Jade involved at all, and by confirming
    both inputs already carried real signatures after round two. Fixed by having
    `scenario_rounds` check whether any scenario output is `type: silent-payment` and
    scheduling a single round for all signers when none is; every existing scenario
    already has such an output, so this only changes behavior for the new
    spend-only case.
  - **SeedSigner's Milestone 2 multi-owner blocker turned out to be narrower than
    documented above: it is specifically about constructing a new Silent Payment
    *output*, not about BIP-376 *spend* inputs.** Upstream SeedSigner's embit
    dependency's `sign_with()` only calls the single-signer-only `derive_sp_outputs()`
    `if self.has_sp_outputs` (i.e. the PSBT has an unresolved SP output); it separately
    and unconditionally calls `_sign_sp_spends()`, which resolves `sp_tweak` inputs
    generically via `resolve_input_privkey`/`match_sp_spend_base`
    (`embit/silent_payments/signing.py`) regardless of how many other inputs are
    present or who owns them. Verified directly:
    `bip376-seedsigner-jade-sp-spend-to-p2wpkh`, `-to-p2tr`, and
    `bip376-seedsigner-coldcard-sp-spend-to-p2wpkh` all produce real
    `PSBT_IN_TAP_KEY_SIG` signatures on every input with SeedSigner as a genuine
    co-owner alongside Jade/Coldcard. `bip376-seedsigner-jade-sp-spend-to-sp` (same
    signers, output changed to a new Silent Payment) fails with the identical
    `SPValidationError("input(s) ... belong to another signer...")` as
    `bip375-seedsigner-jade-two-way`, confirming the boundary precisely: SeedSigner
    can co-own BIP-376 spend-input scenarios, just not ones that also construct a new
    SP output collaboratively.
- Reject conflicts, transaction-intent mutation, premature signatures, invalid proofs,
  and incomplete ECDH coverage.

## Milestone 3: MuSig2 plus Silent Payments

- [x] Add the narrow configurable fixture CLI to Silent Pay; it must emit an unresolved PSBT
  and descriptor without synthesized signer contributions.
  `silent-pay` gained `bip375_interop treasury-wallet` (harness-side: derives a
  `TreasuryWalletConfig` descriptor from the harness's own published test seeds, not an
  externally sourced wallet file) plus `sp-demo`'s `build_round1`/`fund_treasury`/
  `treasury_address`/`broadcast_final`/`verify_onchain` (silent-pay-side: scans/funds a
  real regtest or signet UTXO and builds a real, unresolved round-1 PSBT from it via
  `silent_pay::build_initial_payroll_psbt` -- no synthesized signer contributions). Both
  halves are live-verified against a real signet node: see "Live-verified artifacts" below.
- [x] Define `scenarios/musig2-sp-signet-treasury.yaml`: three `bitsaga-seedsigner`
  signers (seed_ids `test-a`/`test-b`/`test-c`), `suite: musig2-sp`, `network: signet`,
  `suite_config: {key_architecture: aggregate-then-derive, threshold: 3}`.
- [x] Ran `bip375-interop run scenarios/musig2-sp-signet-treasury.yaml --psbt
  ~/work/bip375_artifacts/signet-treasury/initial.psbt`. The PSBT-parsing
  question from the prior pass is resolved and is a real interop win: embit's
  `embit.silent_payments.psbt.SilentPaymentsPSBT.parse` accepts byte-for-byte what
  silent-pay's Rust `psbt`/`psbt-v2` crates encoded (BIP-373 MuSig2 fields, BIP-375 SP
  fields, the `musig2_agg_path` aggregate-derivation entry all round-trip cleanly).
- [x] Add musig2-sp support to Jade's and Coldcard's persistent workers. Previously
  `jade_worker.py`/`coldcard_psbt_worker.py` hard-rejected any suite but `bip375`
  (`musig2_sp=False`), so `bitsaga-seedsigner` was the only backend that could sign
  this suite and `musig2-sp-three-way.yaml` (coldcard-a/jade-b/bitsaga-c) was never
  runnable. Both devices turned out to already support MuSig2-SP through the *same*
  single-shot signing call each worker already used for plain BIP-375
  (`jade.sign_psbt`, Coldcard's upload/`sign_transaction`/keypress/download) -- the
  only missing piece was registering the shared `tr(musig(A,B,C)/<0;1>/*)` descriptor
  once per connection first (`jade.register_descriptor` / Coldcard's
  `miniscript_enroll`), reusing `treasury.py`'s existing `build_treasury_descriptor`
  to build it from the scenario's own signer seed_ids. `WorkerClient.start`/
  `process_psbt` (`worker.py`) now thread that descriptor through like `mnemonic`/
  `network` already were; `cli.py` computes it once per `musig2-sp` run.
  Live-verified end to end on a throwaway regtest node: `treasury-wallet` ->
  `sp-demo`'s regtest-self-mining `build_round1` -> `bip375-interop run
  scenarios/musig2-sp-three-way.yaml --psbt <initial.psbt>` against real Coldcard
  simulator + Jade QEMU instances. Both `01-round1-coldcard-a-merged.psbt` and
  `02-round1-jade-b-merged.psbt` came back correctly contributed and merged --
  the new descriptor-registration code path works against real firmware. (Coldcard's
  own static fixture pair, `testing/data/desc-musig-sp-demo.txt` +
  `musig2-sp-round1-in.psbt`, was considered as a zero-setup shortcut but its "Alice"
  identity is the simulator's own baked-in default seed, confirmed to not match any
  published `test_seeds.py` mnemonic, and Bob/Charlie are BIP-39-passphrase variants
  of that same seed which the harness's worker model doesn't support -- hence building
  a fresh regtest treasury from `test-a`/`test-b`/`test-c` instead.)
- [x] Found and fixed a real bug surfaced by that verification: Jade's round1 call
  succeeded, but round2 always died with a raw `BrokenPipeError` in `worker.py`, not a
  clean `WorkerRequestError` -- meaning the `jade_worker.py` *subprocess itself* was
  exiting after exactly one request, independent of which suite or PSBT it was given.
  Root-caused by instrumenting `serve()`'s stdin loop directly: `_start_qemu()`
  (`jade_worker.py`) spawned QEMU without redirecting its stdin, so QEMU inherited the
  worker's own stdin -- the JSON-lines pipe carrying every subsequent request from
  `WorkerClient`. With `-nographic`, QEMU treats an inherited stdin as its own
  monitor/console input, and contends with the worker's own `for line in input_stream`
  read loop for the same pipe, so the worker sees an early EOF and exits cleanly after
  its first request. Coldcard's simulator subprocess already avoided this
  (`stdin=subprocess.DEVNULL`); Jade's QEMU spawn was missing the same. This is a
  pre-existing harness bug (not something introduced by the musig2-sp work above): any
  earlier scenario that only ever sent Jade a single request per run never exercised it.
  Fixed by adding `stdin=subprocess.DEVNULL` to the QEMU `Popen` call.
- [x] With that fix, ran a new `scenarios/musig2-sp-coldcard-jade-two-way.yaml`
  (2-of-2, no BitSaga) against a fresh regtest treasury built the same way -- **the
  harness's first complete, fully-signed MuSig2-SP round trip**. `run` exited 0; the
  merged `final.psbt` carries both signers' pubnonces (`0x1b`), both partial signatures
  (`0x1c`), and both SP ECDH shares/DLEQ proofs (`0x21`/`0x22`) on the input, plus the
  resolved SP output script -- verified directly via `psbt_maps.semantic_diff` against
  the initial PSBT, not just a non-error exit code.
- [ ] **Next**: the three-way run still fails, but only at the last signer,
  `bitsaga-c` -- the same pre-existing `bitsaga-seedsigner` bug found against the
  signet PSBT, now confirmed to reproduce identically against the regtest one too
  (reproduced directly outside the harness: `Session().advance(psbt, root)` on the
  round1-after-jade-b merged PSBT raises `KeyError: 0` for the `test-c` seed exactly
  as before). This is not new and not related to today's Coldcard/Jade work:
  - `musig2_psbt.py`'s `_sp_groups(psbt)` (around line 402) returns two different shapes:
    the tuple `({}, {})` when no output carries `sp_data`, or a single dict (whatever
    `embit.silent_payments.sp.group_sp_outputs_by_scan_key` returns, keyed by scan-key
    bytes) when at least one does. `sp_scan_keys` (line 413) does
    `list(_sp_groups(psbt)[0])` assuming the first shape; against a real SP output it
    instead indexes the dict with integer key `0`, raising `KeyError: 0`. Any PSBT with
    a real silent-payment output hits this deterministically for every seed.
  - The harness surfaces this today as an opaque `error: 0` (worker.py's `_request_on`
    does `str(error)` on whatever `signer_worker.py`'s generic `except Exception` catches,
    losing the traceback) -- worth improving harness-side error propagation if this
    pattern recurs, but the fix that actually unblocks the round trip belongs in
    `bitsaga-seedsigner`'s `_sp_groups`/`sp_scan_keys`, not here.
  - Once that upstream fix lands, re-run `scenarios/musig2-sp-three-way.yaml` (regtest,
    no external funding needed -- see the regtest recipe below) or the signet scenario;
    `Musig2SpConfig`/`scenario_rounds` already schedule `(round1, all signers), (round2,
    all signers)` with strict-merge between every step, so the orchestration side needs
    no further changes for either backend combination.
  - On success, hand the merged, fully-signed PSBT to silent-pay's existing `finalize`
    bin, then the new `broadcast_final` and `verify_onchain` bins (see Milestone 4) to
    close the loop: broadcast to the same node, confirm, and verify the recipient's
    output exists on-chain.

  **Regtest three-way recipe** (no external funding, unlike the signet flow below):
  1. Start a throwaway regtest `bitcoind` (fresh datadir).
  2. `bip375-interop treasury-wallet test-a test-b test-c --network regtest --out
     wallet.toml`.
  3. A silent-payment address must use regtest's `sprt1` HRP, not signet/testnet's
     `tsp1` -- re-encode an existing `tsp1...` address's scan/spend keys with
     `bech32.bech32_encode(bech32.Encoding.BECH32M, "sprt", [version] + data)`
     (embit's own `encode_silent_payment_address` only knows `sp`/`tsp`).
  4. `cargo run -p sp-demo --bin build_round1 -- --wallet wallet.toml --recipients
     recipients.toml --out-dir <dir> --rpc-url http://127.0.0.1:<port> --rpc-cookie
     <regtest datadir>/regtest/.cookie` -- `build_round1` self-mines and matures the
     treasury deposit automatically on regtest (`fund_regtest_treasury`), no manual
     funding step needed.
  5. `bip375-interop run scenarios/musig2-sp-three-way.yaml --psbt <initial.psbt>`.

  **Live-verified artifacts** (signet, `~/work/bip375_artifacts/signet-treasury/`):
  - `wallet.toml` -- 3-of-3 aggregate-then-derive treasury descriptor derived from
    `test-a`/`test-b`/`test-c` (fingerprints `73c5da0a`/`b8688df1`/`28645006`, path
    `m/48h/1h/0h/3h`). Regenerate via `bip375-interop treasury-wallet test-a test-b
    test-c --network signet --out wallet.toml`.
  - Receive address (chain 0, index 0): `tb1p83upesz8rpnwperlnvs494kf3za07d586a942p87x5298hxdvqeswke5ml`,
    funded on signet by txid `decd5363a371f6480403759bd0ee2efbfc88587475a2b14b11829920f57647f2`
    vout 0 (48,457 sat, confirmed). Regenerate via `sp-demo`'s `treasury_address` bin.
  - `recipients.toml` -- production-schema (label/amount_sat/address only; the demo's
    `demo/recipients.toml` includes `scan_key_hex`, which `silent_pay::recipients`
    rejects via `deny_unknown_fields` -- a real format mismatch between the demo and
    production recipient loaders worth resolving if this becomes a repeatable lane, not
    just worked around per-run).
  - `initial.psbt` / `descriptor.txt` -- built from the UTXO above via `build_round1`
    (1 recipient, 10,000 sat; 37,457 sat change; 1,000 sat fee). Regenerate with:
    `cargo run -p sp-demo --bin build_round1 -- --wallet wallet.toml --recipients
    recipients.toml --out-dir <dir> --rpc-url http://127.0.0.1:38332 --rpc-cookie
    <signet datadir>/signet/.cookie` (use `client_with_timeout`/`SCAN_RPC_TIMEOUT` for
    the scan call -- the default short-timeout RPC client will collide with itself on a
    UTXO set this size; already fixed in `build_round1`, but worth knowing if extending
    it).
- [x] Verify aggregate-then-derive multipath receive/change descriptors on every backend.
  Earlier runs never actually proved this: the change output's script arrived pre-filled
  by silent-pay's own coordinator in every PSBT we'd used, so no signing device was ever
  forced to derive it independently. Tested directly instead -- one 3-of-3
  (`test-a`/`test-b`/`test-c`) aggregate-then-derive treasury, ground truth from
  `sp-demo`'s `treasury_address --chain receive|change --index 0` -- against each
  backend's own address-derivation call, comparing raw witness-program bytes (not
  address strings, which differ cosmetically by network HRP):
  - **Jade**: `jade.register_descriptor(...)` then `jade.get_receive_address(network,
    branch, 0, descriptor_name=...)` for branch 0 and 1 -- byte-for-byte match on both.
  - **Coldcard**: `miniscript_enroll` then `packer.miniscript_address(name, change,
    0)` for `change=False/True` -- byte-for-byte match on both (Coldcard reports the
    address with a `tb1p...` HRP rather than `bcrt1p...` for a regtest wallet; the
    underlying witness program is identical, so this is a cosmetic network-label quirk
    in the simulator, not a derivation bug).
  - **BitSaga**: no address-query API, so exercised its actual `read_aggregates`
    PSBT-parsing function directly (the same code `BitSagaWorker` uses for real
    signing) with a synthetic input scope carrying the right `MUSIG2_PARTICIPANT_PUBKEYS`
    field and `taproot_bip32_derivations` entry for each branch -- also matched exactly.
  - **A real methodology pitfall along the way, worth recording**: silent-pay's own
    `derive_wallet_public_keys_aggregate_then_derive` (`payroll.rs`)
    `participant_pks.sort_by_key(|key| key.serialize())` -- it lexicographically sorts
    participant pubkeys before aggregating, regardless of the order they're written in
    the descriptor string. The first BitSaga attempt fed `read_aggregates` the
    descriptor's *unsorted* order and got a completely different (but internally
    self-consistent) aggregate key -- a false-negative interop failure that was actually
    a test-harness bug, not a BitSaga bug. Jade and Coldcard didn't need this correction
    because their own descriptor parsers already sort internally when evaluating
    `musig(...)`; BitSaga's `key_agg` trusts whatever order the PSBT's
    `MUSIG2_PARTICIPANT_PUBKEYS` field gives it (which is fine for PSBTs silent-pay
    itself builds, since it sorts before writing that field -- but is a sharp edge for
    any future fixture-generation code in this harness that constructs that field
    itself; it must sort too, or a real backend will disagree with a naively-ordered
    fixture).
- [x] **Superseded by real Jade firmware work, then verified end to end.** This item
  was originally scoped as a conformance check only -- confirm each backend explicitly
  reports "unsupported" rather than a real signing test -- because at the time Jade's
  vendored libwally had only the derive-then-aggregate pubkey-aggregation primitive
  (`wally_musig_pubkeys_derive_then_agg`), no PSBT-level signing function for it, and
  nothing in Jade's own firmware exercised the path. That gap has since been closed
  upstream: commit `32a020c3` ("silentpayments: accept derive-then-aggregate MuSig2
  descriptors") teaches Jade's `descriptor.c`/`silentpayments.c` to register
  `tr(musig(A/<0;1>/*,B/<0;1>/*))` (the per-participant-path form) alongside the
  existing aggregate-then-derive shape, backed by a libwally bump that generalizes the
  PSBT-level `wally_psbt_musig2_add_nonce`/`_sign`/`finalize_input` functions to accept
  a keyagg cache from *either* `wally_musig_pubkey_agg` or
  `wally_musig_pubkeys_derive_then_agg` -- no longer aggregate-then-derive-only. Jade's
  own `test_jade.py`/`test_sp_roundtrip.py` gained a `--derive-first` variant that
  exercises this with one real Jade plus a software-simulated cosigner.
  - The compiled firmware (`build/flash_image.bin`, what QEMU actually boots) was stale
    relative to this commit -- rebuilt via `idf.py build` (needs `. <esp-idf
    checkout>/export.sh` sourced first for `idf.py`/`esptool.py`) then
    `main/qemu/make_flash_img.sh <flash_image.bin> <qemu_efuse.bin>` to regenerate the
    actual bootable image. Always check this before trusting a "firmware now supports
    X" claim -- the source and the binary QEMU boots are two different things.
  - Verified with **two independent real Jade QEMU instances** (not the single-Jade
    simulated-cosigner harness above) driven through this repo's own orchestration code
    (`WorkerClient`, `engine.run_rounds`, `psbt_maps` strict merge) -- no changes needed
    to `jade_worker.py` at all, since it already just registers whatever descriptor
    string it's given and calls `jade.sign_psbt`. The only missing piece at the time was
    a derive-then-aggregate descriptor to hand it, since `treasury.py` then built only
    aggregate-then-derive; that first run used a hand-written one
    (`tr(musig([xfp/path]xpubA/<0;1>/*,[xfp/path]xpubB/<0;1>/*))` -- the multipath suffix
    moves onto each participant instead of the aggregate) fed to silent-pay's existing,
    already-implemented `WalletKeyArch::DeriveThenAggregate` path
    (`treasury_address`/`build_round1` needed no changes either -- key_arch is inferred
    entirely from descriptor shape).
  - Full result, closed the same way as the aggregate-then-derive flagship run: round1
    added both signers' pubnonces/ECDH-shares/DLEQ proofs and resolved the SP output
    script; round2 added both partial signatures; `finalize` produced a valid raw
    transaction; `broadcast_final` broadcast and confirmed it on regtest
    (`2540bec67b085b81d65e07ba748e92387292e72910e8dd0976def1d11dada51f`) -- meaning
    Bitcoin's own consensus-level script validation accepted the Schnorr signature
    against the derive-then-aggregate tweaked key, the strongest possible confirmation
    it's cryptographically correct, not just "no merge conflicts"; `verify_onchain`
    independently re-scanned and confirmed the recipient's 10,000 sat output.
  - BitSagaWorker still explicitly rejects this key architecture
    (`"BitSaga MuSig2-SP supports aggregate-then-derive only"`) -- unaffected by any of
    this, since it's a separate backend with its own code path.
  - **Follow-up, now done**: `treasury.py` builds both shapes
    (`build_treasury_descriptor(..., key_architecture=...)`, exposed as
    `treasury-wallet --key-architecture`), and `cli.py` reads the scenario's declared
    `key_architecture` off the parsed suite config instead of always assuming
    aggregate-then-derive. `scenarios/musig2-sp-jade-derive-first-two-way.yaml` is
    therefore runnable through plain `bip375-interop run`, with no hand-written
    descriptor and no bespoke driver script: re-verified end to end that way
    (`artifacts/20260916T141309Z-musig2-sp-jade-derive-first-two-way/`, broadcast
    `ad8a806cc690ce1bd593b2a0d42a5927aabc7dee2f30f3bfd763432a5627ad2c`, recipient output
    re-detected on-chain).
  - **Gap check against silent-pay** (the other implementation of this same descriptor
    format): generated both shapes with `treasury-wallet` and resolved each through
    silent-pay's `treasury_address`. All four receive/change addresses matched the
    device-verified values exactly, and `build_round1` echoed our generated descriptor
    back unchanged -- which is a free fixed-point check, since silent-pay's
    `normalized()` *regenerates* the descriptor from its own parsed signers via
    `descriptor_from_signers`. So the Python and Rust builders agree byte for byte, not
    merely parse-compatibly. The check is written up in `docs/runbook.md` with its
    expected values so it can be re-run after touching either side.
  - That check also **corrected a wrong claim in the runbook**: it had said signer order
    in a scenario determines the aggregate key. It does not -- silent-pay sorts
    participant pubkeys before aggregating, and Jade/Coldcard sort inside their
    descriptor parsers, so reordering signers yields a byte-identical treasury (verified
    by building reversed-order descriptors for both architectures and getting identical
    addresses). Reassuring for `--signer-order`/`--shuffle-signers`, which can therefore
    reorder signing freely without changing the wallet being signed for.
- [x] Determine whether upstream SeedSigner has any MuSig2-SP capability at all, since
  `SeedSignerAdapter.capabilities = AdapterCapabilities(plain_bip375=True,
  musig2_sp=False)` (`src/bip375_interop/adapters/seedsigner.py:26`) and
  `SeedSignerWorker.process()`'s hardcoded `"upstream SeedSigner does not support
  MuSig2-SP"` (`src/bip375_interop/signer_worker.py:84-87`) were previously just
  harness-side assumptions, never checked against upstream source. Checked the
  `seedsigner` checkout directly (`~/src/seedsigner`, remotes
  `upstream` = `SeedSigner/seedsigner`, `origin` = `notTanveer/seedsigner`): no file
  anywhere in its source tree mentions `musig`, `musig2`, or `frost` in any form.
  Compare with `~/src/bitsaga-seedsigner` (remotes `upstream` =
  `3rdIteration/seedsigner`, `origin` = `bitsagarob/seedsigner`): dedicated modules
  (`helpers/musig2.py`, `musig2_psbt.py`, `musig2_card.py`), five dedicated test
  files, `docs/musig2.md`, dozens of feature commits, and its own forked `embit` pin
  (commit `2ea1e49a`, "Pin embit at our fork, which adds BIP-390 musig() descriptors")
  adding BIP-390 `musig()` descriptor parsing that upstream embit/SeedSigner lack.
  - **Conclusion: genuine upstream capability gap, not a missing harness adapter.**
    Plain upstream SeedSigner firmware has no MuSig2 or MuSig2-SP signing path to
    adapt to; `bitsaga-seedsigner` exists specifically to add it, reaching down into
    its own `embit` fork to do so. No SeedSigner-family MuSig2-SP work should be
    scoped against the `seedsigner` checkout -- `bitsaga-seedsigner` remains the only
    MuSig2-SP-capable member of the SeedSigner family here, and it is already blocked
    by the `KeyError: 0` bug tracked in the **Next** item above.
  - Practical consequence, combined with Milestone 2's finding that the plain
    `seedsigner` checkout's embit dependency has no per-input BIP-375 multi-party send
    support either: upstream SeedSigner (as distinct from `bitsaga-seedsigner`) cannot
    currently participate as a co-signer in *any* multi-party send-to-silent-payment
    scenario -- plain BIP-375 or MuSig2-SP -- pending upstream fixes tracked in
    Milestone 2 and Milestone 3 respectively. A full Coldcard+Jade+SeedSigner
    three-way "send to SP" proof is therefore blocked end-to-end on upstream work in
    both suites, not a harness gap.

## Milestone 4: optional regtest and physical devices

- [x] Fund and broadcast completed fixtures on an ephemeral Bitcoin Core regtest node.
  The previously-missing middle step -- broadcasting an actual *signed* transaction --
  is now live-verified end to end, closing this loop for the Coldcard+Jade 2-of-2
  MuSig2-SP round trip from Milestone 3: `finalize` on the fully-signed
  `musig2-sp-coldcard-jade-two-way` PSBT (`cargo run -p sp-demo --bin finalize --
  <final.psbt>`) produced a valid raw transaction and confirmed the SP output;
  `broadcast_final` (`--wallet wallet.toml --tx-hex-file <hex> --rpc-url ... --rpc-cookie
  ...`) broadcast and confirmed it on the same regtest node
  (`0dc19e6065f7a80beeb0eca55dfca10789555dcdc3dd1f6b01d0a31b66259a7d`, 1 conf); and
  `verify_onchain` (`<final.psbt> --recipients recipients-scan.toml --txid ...`) ran the
  real BIP-352 recipient scan against live chain state and confirmed the 10,000 sat
  output. Note `verify_onchain`/`scan_recipients` need the *demo* recipient schema
  (`scan_key_hex` present, since scanning needs the recipient's own private scan key) --
  a separate file from the *production* schema `build_round1` requires (which rejects
  `scan_key_hex` via `deny_unknown_fields`), so keep both when re-running this recipe.
- Export resumable raw/base64/QR artifact bundles for guided hardware handoff.

**Harness cleanup gap -- fixed.** Both `ColdcardPsbtWorker`'s `simulator.py --segregate`
and `JadeWorker`'s `qemu-system-xtensa` were being left as orphans (reparented to pid 1)
after `worker.stop()`, repeatedly, across an entire session's worth of runs. Root cause
was in `WorkerClient` (`worker.py`), not either device worker: `stop()` called
`process.terminate()`, sending SIGTERM only to the worker's own process; the OS's default
SIGTERM handling kills a process immediately and never runs Python's `finally` blocks, so
the worker's own `close()` (which stops its simulator/QEMU child) never executed, leaving
that child parentless. Fixed by having `start()` put the worker in its own session
(`start_new_session=True`, so its child inherits the same process group without also
capturing the harness's own process) and having `stop()` signal the whole group
(`os.killpg(os.getpgid(pid), sig)`, SIGTERM then SIGKILL) instead of just the one process.
Verified with real Coldcard+Jade runs on both the success path
(`musig2-sp-coldcard-jade-two-way.yaml`) and the failure path (`musig2-sp-three-way.yaml`,
which still hits the BitSaga bug above) -- zero orphaned `simulator.py`/`qemu-system-xtensa`
processes after either.

## Future suite: FROST plus Silent Payments

`frost-sp` already has a stable suite identifier and an isolated configuration namespace.
Implementation waits for concrete descriptor, PSBT field, threshold, round, nonce, and
verification requirements. It must add its own suite handler and capabilities without
changing existing scenario or adapter contracts.
