# PSBT validation remedy plan

Source of truth for closing the false-pass holes found in the PSBT review. This is a
workstream, not a status log. `docs/roadmap.md` records what already shipped. Update
this file when a task lands or a decision changes.

The harness stays a harness. It does not become a general wallet validator. A green
run may claim only the checks that actually ran.

## Claim boundary

Trusted creator PSBT, untrusted signer bytes. Strict merge stops a signer from
changing a field that was already present. It does not, by itself, prove the PSBT
matches scenario intent, that every signature verifies, or that the result is
BIP-375-compliant or broadcastable.

Three result labels, written into the run manifest:

| Label | Meaning |
|---|---|
| `evidence` | Full bip375 crypto checks ran, intent was bound, and the release-gate validators ran. |
| `interop-only` | Round completed, but this process did not check intent or did not check MuSig2 aggregation. `reason` says which. |
| `not-evidence` | `verification: structural`, `merge_policy: combiner`, or a skipped validator. Must not be reported as a pass. |

`reason` is a short string (`structural-musig2`, `consistency-only`, `validator-skipped`, `combiner-repairs`, `structural`). It is not a fourth scope. Do not widen `passed` in `check` until Workstream A and the release gate in Workstream D are in.

## Non-goals

- Reimplementing BIP-375, MuSig2, or a software cosigner.
- Banning unknown or proprietary PSBT fields. Additive maps are intentional. The hole is a signer adding a *security-relevant* field the completion check then ignores.
- Unilateral renumbering of silent-pay's `0x21` / `0x22` records. That is an upstream wire-format change, not a harness fix.
- Treating Bitcoin Core, BIP-174 key order, or a BIP-375 sighash rule as already proven by this repo. Confirm the spec text, then implement. See Workstream E.

## Order

A blocks publication of any "verified" claim. B and C block calling a bip375 run intent-checked. D is the release gate. E is conformance, after the false passes are gone. F is packaging and usability; it does not fix a false pass.

---

## A. Stop false passes

Integrity. No scenario YAML change required for the first task.

### A1. Verify P2WPKH signatures

`verification.py:_verify_input_signature` returns on `script_type == "p2wpkh"` after comparing `entry.key_data` to the derived pubkey. The signature value is never checked. Taproot in the same function calls `schnorr_verify`. The docstring on `verify_bip375_completion` says every signature is verified.

- Verify the ECDSA signature with the sighash byte BIP-174 appends to a partial signature, against the message hash for that sighash, under the derived pubkey.
- Reject a missing sighash byte, a bad length, and a signature that does not verify.
- Keep the existing pubkey match. A valid signature under the wrong key is still a failure.

Acceptance: a new test in `tests/test_verification.py` takes a fully signed single-owner PSBT, changes only the P2WPKH signature value, and `verify_bip375_completion` raises `VerificationError`. The current taproot wrong-key test stays. Do not route this case through `verification: structural`.

### A2. Verify global Silent Payment share and DLEQ

`_verify_sp_input_evidence` returns immediately when `contribution_mode == "global"` or the scenario has one owner. Output-script recomputation does not check those records. Multi-owner per-input already requires input `0x1d` / `0x1e` and calls `verify_dleq_proof`. The same function rejects a global share on a multi-owner run, so the global fields are known (`0x07` / `0x08`, keyed by scan key).

- For a global or single-owner run with a Silent Payment output, require the global ECDH share and DLEQ for every distinct scan key, and verify the proof against the owner's key. Reuse `verify_dleq_proof`. Do not treat a matching output script as a substitute.
- If a mode legitimately has no share record, name that mode in the docstring and test it. Do not leave the early `return` as the default full path.
- Fix the `verify_bip375_completion` docstring so it lists what is checked.

Acceptance: a single-owner fixture with a wrong or missing global DLEQ fails full verification. A multi-owner per-input run still fails if a global share is present.

### A3. Say what MuSig2 completion does not prove

`verify_musig2_sp_completion` counts `0x1b` / `0x1c` records and, after round 2, checks that a Silent Payment output script key exists and that `tx_modifiable` is clear. It does not verify nonces or partial signatures. `check` already records those runs as `completed`, with the reason "finalize and verify on-chain".

- Keep the count check. Do not add a fake crypto check.
- Manifest field `verification_scope: interop-only` and `reason: structural-musig2` on every musig2-sp run.
- `check` must not count these as `passed`. That is already true; add a test so it stays true.
- Real aggregation stays in D3, via `scripts/musig2-regtest.sh`, not inside `verify_musig2_sp_completion`.

Acceptance: a musig2 run whose record counts match still cannot produce `status: passed` or `verification_scope: evidence`. `reason` is `structural-musig2`.

---

## B. Bind intent, and do not trust signer-supplied metadata

Landed on `psbt-bind-intent`. A run refuses to start unless every input already
carries a UTXO, and records `utxo_source` (`declared` or `non_witness_utxo`).
Completion binds counts, script types, amounts, `recipient_id`, and plain
scripts. A signer who adds a security-relevant field outside the phase's
allow list fails by name, including during `resolve-sign` and `sign`.
Proprietary fields still merge.

Consistency with fields already inside the PSBT is not scenario intent. `expected_sp_output_script` still recomputes from the PSBT's own recipient keys; completion now also requires those keys, counts, types, and amounts to match the scenario. `fixtures.py` resolves `recipient_id` through the same table.

### B1. Refuse a signer-added witness UTXO

Merge forbids changing an existing record. It allows adding key type `0x01` when the base omitted it. Completion then verifies a taproot signature against that UTXO. Generated fixtures include `witness_utxo`, so this is not how current scenarios fail. It is how an external or stripped PSBT becomes signer-controlled.

- Before `run_rounds`, require every input to already carry `witness_utxo` (`0x01`) or `non_witness_utxo` (`0x00`). Reject otherwise. Do not let the first signer supply it.
- Where `non_witness_utxo` is present, check the output at `previous_txid` / `output_index` matches `witness_utxo` when both exist. Where only `witness_utxo` exists, record `utxo_source: declared` in the manifest. Do not claim it was fetched from a node.
- A later task may fetch the prevout. That is not required to close the signer-addition hole.

Acceptance: merge or the pre-round check rejects a contribution that adds `0x01` to an input that lacked it. A generated fixture, which already has it, still runs.

### B2. Check the final PSBT against scenario intent

For `run-generated`, the scenario is the intent. Check all of:

- input count and output count
- each input's script type against `scenario.inputs[].type`
- each output's `amount_sat` against `scenario.outputs`
- Silent Payment outputs: `PSBT_OUT_SP_V0_INFO` equals the scan/spend keys the scenario's `recipient_id` resolves to, not merely the keys the PSBT already carries
- plain outputs: script equals the script the fixture was supposed to create
- input amounts against the UTXO value already on the input

`fixtures.py` must stop ignoring `recipient_id`. Resolve it through the same table the completion check uses. A scenario that names `recipient-a` and a PSBT that pays a different scan key fails.

For `run --psbt`, do not invent intent. If the scenario declares inputs and outputs, apply the same checks. If it does not, set `verification_scope: interop-only` and `reason: consistency-only`. Never `evidence`.

Acceptance: tests for output-count mismatch, amount mismatch, and a recipient scan key that does not match `recipient_id`. Each fails `verify_bip375_completion`. A self-consistent PSBT whose recipient differs from the scenario does not pass.

### B3. Ownership of security-relevant additions

Unknown and proprietary fields may still be appended. That is normal PSBT extensibility, not a bug.

Security-relevant types, checked no matter who added them:

- input: `partial_signature` (`0x02`), `sighash_type` (`0x03`), `witness_utxo` (`0x01`), `non_witness_utxo` (`0x00`), `tap_key_signature` (`0x13`), `final_scriptsig` (`0x07`), `final_scriptwitness` (`0x08`), `sp_ecdh_share` (`0x1d`), `sp_dleq` (`0x1e`)
- output: `script` (`0x04`), `sp_v0_info` (`0x09`)
- global: `sp_ecdh_share` (`0x07`), `sp_dleq` (`0x08`), `tx_modifiable` (`0x06`)

`_assert_phase_contract` today only looks for one added field on inputs the signer owns. Extend it:

- `contribute`: owned inputs gain an ECDH share and a DLEQ; no signature field is added on any input; no output script is added; `witness_utxo` is not added.
- `resolve-sign` / `sign`: the signer adds a signature only on an owned input. A signature appearing on an unowned input fails, even if the bytes would verify later.
- Completion still verifies every security-relevant field. The phase check is so a poisoned slot fails at the signer who wrote it, with that signer's name in the error.

Do not reject proprietary `0xfc` here.

Acceptance: an engine test where signer B, during `contribute`, adds a `partial_signature` on signer A's input fails naming signer B. A proprietary field added beside a valid share still merges.

---

## C. Make weak modes obvious

Landed on `psbt-weak-modes`. A run with `verification: structural` writes
`verification_scope: not-evidence` and `reason: structural`, including when
the policy is also combiner. `merge_policy: combiner`, or any recorded
repair on a full run, writes the same scope with `reason: combiner-repairs`.
`check` records those runs as `completed`, not `passed`. The run JSON prints
the scope beside `final_psbt`. Merge failures keep the field text and prefix
step, phase, and signer.

### C1. `not-evidence` for structural verification and combiner repairs

`verification: structural` skips crypto and is documented. `merge_policy: combiner` restores omitted fields and the run still succeeds. No scenario sets `combiner`. The knob is still public, and the manifest records `repairs` without changing status.

- A run that used `structural`, or that recorded any repair, writes `verification_scope: not-evidence` and must not be `passed`.
- CLI summary prints that label next to the artifact path.
- Keep both knobs. They are how the sighash-default and omission findings were isolated. They are not a release result.

Acceptance: `tests/test_verification.py::test_structural_verification_skips_cryptographic_checks` still shows the opt-out works, and a batch/manifest test shows that run is not `passed`.

### C2. Name the signer on merge failures

`PsbtMergeError` messages such as `contribution omits input 0 sighash_type (03)` do not include the phase or signer. `engine.run_rounds` lets the exception propagate from `merge_psbts`.

- Wrap merge failures with step, phase, and signer. Do not change the field text; the runbook matches on it.

Acceptance: a forced omission names the signer in the exception string.

---

## D. Release gate

Independent review is not the default path. `validators:` is per scenario. Two scenarios opt in, each to one validator. `check --exhaustive` attaches both for `bip375` only, and only when that flag is passed. `models.py` rejects validators on `musig2-sp`. SPDK's `snapshot_glob` is `final.psbt`. Caravan's is `*.psbt`.

### D1. `check --release`

One profile, no per-scenario editing:

- bip375: `verification: full`, `merge_policy: strict`, Caravan on every `*.psbt` snapshot, SPDK on `final.psbt`
- musig2-sp: not marked `passed` by this command; D3 covers it
- a scenario that is `structural` or `combiner` is `not-evidence`, not a release pass
- manifest lists the validators that ran, the snapshot count, and `verification_scope`

`run` / `run-generated` stay able to skip validators for a tight device loop. Their manifest must then say the independent check did not run, so the scope cannot be `evidence`.

Acceptance: a bip375 scenario with no `validators:` key still runs Caravan and SPDK under `--release`. A PSBT Caravan rejects fails the case. Missing validator build (`caravan` dist, `spdk-cli` binary) is a preflight error, not a skip.

### D2. Do not call the default checker an independent oracle

`verify_bip375_completion` uses embit (`derive_sp_outputs`, `schnorr_verify`, `verify_dleq_proof`). That is a second implementation relative to Coldcard and Jade firmware. It is not Caravan, and it is not rust-psbt. Do not write "independent" in the README or in that docstring unless the release gate's validators ran.

Acceptance: README design-boundary section states the three scopes and which code implements each. The stale sentences that say Coldcard has no per-PSBT transport, and that mixed Jade runs are future work, are removed in the same edit. They contradict `docs/roadmap.md`.

### D3. MuSig2 release leg is the regtest script

`scripts/musig2-regtest.sh` already finalizes, broadcasts, and scans. `check` does not call it.

- `check --release` invokes both architectures, or refuses to emit a release summary if they were not run.
- A leg counts only when the script prints `PASS`.
- Leave cryptographic aggregation in silent-pay. Do not reimplement it in `verification.py`.

Acceptance: a release summary with no `PASS` line for either architecture is a failure, not an empty success.

---

## E. Conformance, after the false passes

Each item starts by quoting the requirement. If the text does not say what we thought, drop the task. Do not ship a behavior change on an unverified reading.

### E1. Map key order

`PsbtMap.serialize` preserves entry order and appends new keys. A partial signature is type `0x02`, appended after `previous_txid` (`0x0e`), so the merged map is not lexicographic.

- Confirm BIP-174's key-order rule from the BIP text.
- If it requires sorted keys: sort on serialize, reject unsorted input in `parse_psbt`, and add a round-trip test that a merged partial signature still parses.
- If a device under test depends on preserved order, sort only the bytes written as `final.psbt` and the bytes sent to the next signer, and record that. Do not leave unsorted artifacts unlabeled.

### E2. PSBTv2 unsigned transaction

`parse_psbt` requires version 2 and does not reject global `unsigned_tx` (`0x00`).

- Confirm BIP-370 forbids that field in v2.
- If it does, `parse_psbt` raises `PsbtFormatError`. Test with a v2 map that carries `0x00`.

### E3. Sighash rule as a completion check

The sighash-default scenario is a device finding: neither firmware rejects a non-ALL sighash while a Silent Payment output is present, and the run then dies because Jade omits the field. `verify_bip375_completion` does not require `SIGHASH_ALL`, and it ignores the `SIGHASH_SINGLE` bit in `tx_modifiable`.

- Quote the BIP-375 rule.
- If the BIP requires `SIGHASH_ALL` on every input when an SP output is present, fail completion when the input sighash is anything else, including a taproot signature whose trailing byte is not ALL. Keep the device-finding scenario; it is allowed to fail. It must not be able to pass full verification by preserving `SIGHASH_DEFAULT`.
- Do not equate "DEFAULT and ALL hash the same on taproot" with "the PSBT complies." Note the consensus equivalence in the error text if useful. It is not an excuse to pass.

### E4. Private MuSig2-SP key types

`_FIELD_NAMES` labels input `0x21` and `0x22` as `musig2_sp_ecdh_share` and `musig2_sp_dleq`, and the comment says they are not BIP-375/376 fields. They are silent-pay's encoding.

- In the field table and README, mark them private to that producer. Do not list them as standard types.
- Do not renumber them in this harness. File the wire-format question upstream if a published BIP assigns those types.

---

## F. Usable by someone who is not on this machine

Separate from integrity. A false pass is fixed in A–D even if F slips.

### F1. One entry point

`just validate` runs scenario schema check and prints `valid:`. There is no `just check`. The full sequence lives in `.claude/skills/interop-regression/SKILL.md`.

- Add `just check` for the ordinary batch and `just release` for D1 plus D3.
- Rename or relabel the schema command so it cannot be read as PSBT validation. `bip375-interop validate` may remain as the schema check if the help text says "scenario schema, not a PSBT."

### F2. `validate-psbt`

A file in, a verdict out. No QEMU, no scenario.

- Parser, then Caravan on that file, then SPDK if the file is fully signed.
- Print which stage ran. An unresolved PSBT that fails SPDK finalize is `structural-only`, not a failure of SPDK, when the caller did not ask for signature verification.

### F3. Build `spdk-cli` from `interop.yaml`

`spdk-cli/Cargo.toml` hardcodes `/Users/macgyver/src/spdk/psbt` and `/Users/macgyver/src/rust-psbt`. `config/interop.example.yaml` hardcodes the same home directory. `rust-psbt` is not in `interop.lock`.

- Resolve those two paths from checkout config at build time, or document them as required `[patch]` overrides generated by `doctor`.
- A missing path fails `doctor` with the checkout name, not a cargo error from a foreign absolute path.
- Record the `rust-psbt` commit in the lock, or in a named sidecar the release summary prints. Do not leave it only in `docs/regression-plan.md`.

### F4. Tie expectations to the lock

`expectations.yaml` says it is not yet tied to pinned revisions. A green `check` against a floating checkout is not a result.

- `check --release` fails if `expectations.yaml` has no `lock` digest, or the digest does not match `interop.lock`.
- Updating expectations is a separate, reviewed edit. Do not auto-rewrite them to make a run pass.

### F5. Coldcard network is not the requested network

`coldcard_psbt_worker.py` accepts any network and the simulator stays on testnet (`chain=XTN`). A signet scenario must not be `evidence`.

- Manifest field `network_exercised: testnet` when the backend cannot honor the scenario network.
- Release gate skips or fails signet scenarios on that backend instead of reporting them as the scenario's network.

---

## Suggested execution

One PR per workstream, in order. A1 is the first commit: it is a localized fix with a failing test. Do not start E or F while A1 is open.

| PR | Tasks | Done when |
|---|---|---|
| 1 | A1, A2, A3 | P2WPKH tamper fails; global DLEQ tamper fails; musig2 cannot be `passed` |
| 2 | B1, B2, B3 | Signer cannot introduce `witness_utxo`; recipient and amounts are checked; unowned signature fails at that signer |
| 3 | C1, C2, D1, D2 | `--release` runs both validators; weak modes are `not-evidence`; README matches the code |
| 4 | D3, E1–E4 | Regtest `PASS` is part of release; conformance items either land with a BIP citation or are dropped |
| 5 | F1–F5 | A clean machine fails `doctor` on missing paths instead of cargo, and `just release` is the only command a reviewer must run |

## Verification for this plan

Each task names its test. No task is done because a docstring changed. After PR 3, one bip375 scenario with no `validators:` key, run under `check --release`, must show Caravan and SPDK in the manifest and `verification_scope: evidence` only if A and B also ran. A P2WPKH signature-value tamper must still fail that run.
