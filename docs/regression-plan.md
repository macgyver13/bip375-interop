# Regression workflow plan

Source of truth for making a BIP-375 interop regression run repeatable, comparable to
the previous run, and clear about which scenarios are supported for which versions.
Update this file when a decision changes; remove items that are corrected or done.

## Why

The first full run on 2026-09-18 exposed these gaps:

- A dirty checkout made every scenario fail in preflight. The score read 0 of 25, and
  the failure was reported per scenario instead of once.
- embit's API drifted (`all_outpoints`, `compute_ecdh_share` removed, `derive_sp_outputs`
  and `group_sp_outputs_by_scan_key` return shapes changed) and the harness crashed
  mid-run with a traceback and no batch report.
- Ten failures were reported as a flat list. Telling documented device findings from
  possible regressions meant reading `docs/runbook.md` by hand.
- The tip under test was read from git, but silent-pay is managed with jj, so the
  reported revision was the parent, not the working copy.
- The MuSig2-SP legs need a manual regtest recipe (node, wallet, `build_round1`, run,
  finalize, broadcast, verify).

## Goals

1. Every scenario has an explicit status for a pinned set of checkout versions.
2. Each run is compared against the previous run and the expectations, and reports only
   the variances.
3. Exit codes mean something: today `check` exits nonzero on any failed scenario, expected
   findings included. It should exit zero when every variance is expected, and nonzero on a
   regression or a preflight failure.
4. The full procedure, including the MuSig2-SP legs, is one documented command sequence
   that a project skill can drive.

## Version control conventions

Each repo has one preferred VCS. Record it per checkout in `interop.yaml` (`vcs:` field)
so tools and agents stop guessing. Detected today:

| Checkout | Preferred VCS | Notes |
|---|---|---|
| bip375-interop | GitButler (`but`) | git branch `gitbutler/workspace` |
| silent-pay | jj | git HEAD is stale under jj, read the tip with `jj log -r @` |
| bip375-test-generator | jj | colocated git |
| coldcard-firmware | jj | colocated git |
| spdk | jj | colocated git |
| Jade | GitButler | |
| bitsaga-seedsigner | GitButler | |
| seedsigner | git | branch `sp-send-support` |
| caravan | git | branch `feat/sp-sending` |
| embit (`/Users/ron/src/embit`) | git | editable install, not yet in `interop.yaml` |

Confirm this table before it lands in `interop.yaml`; the detection was by directory
markers only.

### GitButler rules for changes to this repo

- One dedicated GitButler branch per work group below. Commit only this session's
  changes; other agents' work in the workspace is not touched.
- Use `but diff`, then `but commit -b <branch> -m "<message>" <id> <id>`. Split unrelated
  hunks by hunk, not by file. Keep tests in the same commit as the behavior they verify.
- Fixes to an unpublished commit are amended into that commit, not added as fixup
  commits. Ask before rewriting pushed or shared history.
- Commit messages are short: what changed, why, any decision. No co-author trailers.
- No push and no pull request unless asked. A requested PR is opened as a draft.
- Before starting a group, `but status` to confirm the workspace only holds this
  session's branches.

## Phase 1: harness changes

Each group is one coherent, tested commit (or a small stack) on its own branch.

| # | Branch | Change | Tests |
|---|---|---|---|
| 0 | `embit-api-sync` | Adapt `verification.py` and `tests/test_verification.py` to embit `f18d23b`. Committed (`qkm`), stacked above `scenario-groups` because it edits lines from that branch's verification tests commit. | existing suite |
| 1 | `expectations` | `expectations.yaml` schema and loader. One entry per scenario: `status` (`supported`, `finding`, `unsupported`, `needs-external-psbt`, `unclassified`), reason, reference. Seeded from the 2026-09-18 results and `docs/runbook.md`; `unclassified` holds the four untriaged failures until the stable-pin run. | loader validation, unknown scenario, missing scenario |
| 2 | `version-pins` | `vcs:` per checkout, auto-detected from a `.jj` directory when unset (values `git`, `jj`, `gitbutler`). jj checkouts report the working-copy commit id, not git HEAD, and are never dirty. Committed `interop.lock` maps checkout name to commit id (`interop.yaml` is gitignored, it holds local paths); `pin` writes it, and a lock entry fills an unset `revision` and conflicts with a different one. Run manifests and `doctor` record `vcs` and the tip. embit is added to the local `interop.yaml`. `pin` refuses dirty git checkouts. `gitbutler` is inspected as plain git and carries a FIXME. | jj tip, jj revision tracks edits, git vcs, lock round trip, lock conflict, pin, pin refuses dirty |
| 3 | `report-diff` | `check` labels every case against `expectations.yaml` and the previous finalized batch of the same project: REGRESSION (expected supported, failed), FIXED (expected not supported, passed), CHANGED (status or reason differs from the previous run), STEADY, plus NOT-RUN (expected supported but blocked), NEW (no expectation) and UNCLASSIFIED. Labels appear in `report.json`, `report.html` and the `check` summary, which lists only the variances. Skipped when `expectations.yaml` is absent. | classification table, missing previous run, unfinalized batches ignored, labels in report and summary |
| 4 | `exit-codes` | Preflight failures (dirty checkout, import error) abort the batch with one clear error instead of per-scenario failures. Preflight inspects every checkout once and the batch report records those states. Exit nonzero only on REGRESSION, UNCLASSIFIED, NEW or a preflight failure, and zero when every variance is an expected finding. | preflight abort, exit code per label |
| 5 | `docs-regression` | Update `docs/runbook.md` to describe expectations, the diff report and the exit codes. | none |

Order matters: 0 first (unblocks the run), then 1 to 4 in order, 5 last.

### Baseline

First pin, written 2026-09-21, in `baseline/interop.lock` (a separate config and lock
from the live, gitignored `interop.yaml`, so day-to-day dev checks against moving
checkouts are unaffected):

| Checkout | Commit | How it was chosen |
|---|---|---|
| silent-pay | `7761df1edfbb2b8237608208e625dd7bd7fb9a62` | `musig2-jade-cc-interop` resolved to `43dc64e923b3`, which predates `build_round1` (added by the very next commit, `bbe99ee93e1c`) and could not run the MuSig2-SP legs at all; moved to `uln`/`7761df1edfbb`, a direct descendant of `bbe99ee93e1c` that also touches `build_round1.rs` for derive-then-aggregate |
| coldcard-firmware | `80a27ae5326fc703de4c9372285dadef9152047b` | `musig_silentpayments` resolved to `89d5a4ed428c`, one commit behind a fix (`WIP: fix (SP): retain explicit sighash when PSBT has SP outputs`) already on the live tip; that gap reintroduced `contribution omits input 0 sighash_type (03)` across nearly every coldcard-backed BIP-375 scenario in a check run at the bookmark commit, so moved to the live tip instead |
| spdk | `6e26856c58981efa956cb85773c56c80d91bb81b` | `musig2-working` bookmark |
| jade | `521cdcaddb5e014892e13c8576b7f8b7ec7deb92` | current cleaned applied-stack tip (group 8 parent-tip pin), not historically reconstructed |
| bitsaga-seedsigner | `a41ff4467a3943ff2f319c4072e95d104922e702` | current cleaned applied-stack tip; no working end-to-end scenario ever anchored a baseline for this checkout |
| seedsigner | `4f8442f4012275891f1b4557399e8f62a0226abc` | current tip, unchanged since before any runbook run |
| caravan | `b3b39754619a9cc06d9c92801927e5639bbd60e7` | current tip, unchanged since before any runbook run |
| bip375-test-generator | `29b6d74b46913210545cb0e8931e5b8fcd5e57ce` | current tip; not referenced by any scenario result, so unverified |
| embit | `f18d23bd5e089693198dcaaa15429040aad6e600` | current tip; the revision `verification.py` was adapted to this session |

Why not the runbook's own "last confirmed passing" dates: checkout-revision recording in
manifests only landed 2026-09-16 18:32 (`e103841`), after every artifact the runbook
cites, so those manifests all have `"checkouts": []` and record nothing to read back.
jj commit timestamps in this history are also unreliable for date-based reconstruction:
many `coldcard-firmware` ancestors share one identical committer timestamp, and one
`spdk` ancestor is timestamped later than its own descendant, both signs of a rebase or
import that reset stamps. silent-pay, coldcard-firmware and spdk had also drifted well
past their last interop-relevant work (unrelated FROST and refactor commits), so a
"last good" search by date could not be trusted. Bookmarks named for the musig2/SP work
were used instead, each checked for being a real, non-hidden commit before pinning.

silent-pay, coldcard-firmware and spdk are pinned through a **frozen `git worktree`**
at `<repo>-baseline` next to each live checkout (`/Users/macgyver/src/silent-pay-baseline`
etc.), not the live path, because those checkouts are jj working copies under active
development that must not be disturbed to take this pin. `jj`'s own tip inspection only
ever reads `@`, so a bookmark's commit can only be pinned by checking it out somewhere
else. The worktree is a plain detached-HEAD git checkout; the harness reads it as `vcs:
git`, which needs no override since it has no `.jj` directory. jade and bitsaga-seedsigner
use their live path, since their group-8 pin already reads a real branch tip rather than
the moving GitButler workspace commit; a worktree is only needed where the vcs itself
(`jj`) resists pinning that live path.

Ran `check --project harness --exhaustive` against this pin to get the actual baseline report and classify the four
`unclassified` scenarios. `bip375-test-generator` and `bitsaga-seedsigner` are pinned
with no verified-good anchor behind them; treat their entries as "current, unverified,"
not as evidence of correctness.

### Baseline results (2026-09-22)

`check --project harness --exhaustive` against the corrected pin:
[artifacts/batches/20260922T124419.356352Z-harness-144b285c/](../artifacts/batches/20260922T124419.356352Z-harness-144b285c/report.html)

| Label | Count | Notes |
|---|---|---|
| STEADY | 19 | matches its expectation, unchanged |
| CHANGED | 5 | the coldcard scenarios that failed at the wrong (`musig_silentpayments`) pin now pass, recovering after the pin correction above; not a regression |
| UNCLASSIFIED | 4 | `bip376-jade-two-way-sp-spend-to-p2tr`, `bip376-jade-two-way-sp-spend-to-p2wpkh`, `bip376-seedsigner-jade-sp-spend-to-p2tr`, `bip376-seedsigner-jade-sp-spend-to-p2wpkh`, the same four flagged since 2026-09-18; triaged below, now `finding` |
| NOT-RUN | 2 | the two MuSig2-SP scenarios (no `--psbt` bound in this run; verified separately below) |

No REGRESSION, no NEW. `check` exits 1 solely because of the four UNCLASSIFIED entries,
per the `FAILING_LABELS` decision above.

MuSig2-SP legs, run separately against `baseline/interop.yaml` with a round-1 PSBT from
`silent-pay-baseline`:

| Scenario | Architecture | Txid | Result |
|---|---|---|---|
| `musig2-sp-coldcard-jade-two-way` | aggregate-then-derive | `d0d17994b34d9e046ebbc6aa0e8349385c33ee10a9a931da6c725c3b84732fbb` | finalized, broadcast, confirmed, recipient output found on-chain |
| `musig2-sp-jade-derive-first-two-way` | derive-then-aggregate | `b08fb44886d477d34d55d44addfbfdf6b09c7b880f42359adcc66052333a08b1` | finalized, broadcast, confirmed, recipient output found on-chain |

The first leg's txid is identical to an earlier, unpinned manual run in this same
session, which is a good sign of determinism given the same wallet seeds and treasury.

### Triage: the four UNCLASSIFIED scenarios (2026-09-22)

Both pairs are genuine Jade firmware findings, isolated by direct comparison against
Coldcard in the byte-for-byte identical scenario shape, not harness bugs and not
regressions. Both are now `finding` in `expectations.yaml`. Full writeups in
`docs/runbook.md` under "Jade never clears inputs/outputs-modifiable for a plain
BIP-376 spend" and "Jade drops a co-owner's sighash_type on a BIP-376 spend".

- `bip376-jade-two-way-sp-spend-to-p2tr` / `-p2wpkh`: Jade never clears the global
  inputs/outputs-modifiable flags in a jade+jade single-`resolve-sign`-round, non-SP-output
  spend. Coldcard clears them on its first pass in the identical round shape
  (`bip376-coldcard-jade-sp-spend-to-p2tr`). Jade does clear them correctly when it is
  the resolving signer in the 3-round SP-output dance, so this is specific to the
  plain-destination, single-round shape, where no device is ever the one expected to
  clear them.
- `bip376-seedsigner-jade-sp-spend-to-p2tr` / `-p2wpkh`: Jade's own contribution drops
  `sighash_type` on SeedSigner's (not its own) input. Coldcard preserves that same field
  correctly in the identical scenario shape
  (`bip376-seedsigner-coldcard-sp-spend-to-p2wpkh`), and Jade preserves a Jade co-owner's
  `sighash_type` correctly, so this reproduces specifically when the co-owned input was
  produced by SeedSigner. The runbook's earlier claim that all three SeedSigner+Jade
  BIP-376 spend scenarios sign correctly with a real signature on every input was
  corrected; that only held for SeedSigner+Coldcard.

### Known flake: an occasional Coldcard simulator startup timeout

One exhaustive baseline run flagged `bip375-caravan-coldcard-jade-two-way` as a
REGRESSION with `Coldcard simulator did not become ready`. It passed cleanly on retry in
isolation, with no leftover simulator process and nothing in this session touching
Coldcard's startup path; a second full exhaustive run came back clean. Likely transient
resource contention when many Coldcard simulator instances start in quick succession
during a long batch, not a code regression. Worth a retry before trusting a lone
REGRESSION naming this failure reason.

### rust-psbt: an undeclared fourth pin

`silent-pay` and `spdk`'s `psbt` crate both depend on the same private `rust-psbt` fork.
Building `silent-pay-baseline` against `spdk-baseline` failed with two different
resolved copies of `psbt_v2::Output` in the same dependency graph: `silent-pay` fetches
a floating git branch (`musig2-working-rebase-sosthene`) directly, while `spdk`
overrides its own equivalent dependency to a local path
(`[patch]` to `/Users/macgyver/src/rust-psbt`). At the time this baseline was built, the
live `silent-pay` and `spdk` tips did not compile against each other at all (`spdk` had
renamed `SilentPaymentAddress` to `SilentPaymentCode`); this is an active, in-progress
break between those two repos, unrelated to which commit is pinned.

Pinned: `rust-psbt` at `8af0fc273aec642dfc0165779a2b86a30ccfcdf1` (jj change `xmy`), in a
frozen worktree at `/Users/macgyver/src/rust-psbt-baseline`, same reasoning as the other
three (the live checkout was dirty with unrelated work at the time). This pin lives
outside `interop.lock`'s schema, which only covers `interop.yaml`'s nine named
checkouts and has no concept of a checkout's own transitive dependencies; recorded here
instead. A future improvement could extend the lock schema to cover named transitive
pins like this one, if MuSig2-SP legs are run against the baseline routinely enough to
need it versioned rather than written down once.

To make the build actually resolve to the pinned worktrees instead of the live,
moving checkouts, `spdk-baseline/Cargo.toml`'s `[patch]` path was repointed at
`rust-psbt-baseline`, and `silent-pay-baseline/Cargo.toml` gained a matching `[patch]`
section, since both `silent-pay` and `silent-pay-baseline` hardcode absolute paths to
the *live* `spdk` checkout for the `psbt` and `silentpayments` crates
(`psbt = { path = "/Users/macgyver/src/spdk/psbt" }`, both in the root and in
`demo/Cargo.toml`); those were also repointed at `spdk-baseline`, and `Cargo.lock` was
regenerated. These are real, necessary content changes to the pinned worktrees, not
build byproducts like coldcard's bech32 patch, so `silent-pay-baseline` and
`spdk-baseline` now report dirty for a different, equally load-bearing reason:
reproducing the MuSig2-SP legs from this pin requires reapplying the same three patches
(`silent-pay-baseline`'s two path overrides plus its new `[patch]` section,
`spdk-baseline`'s repointed `[patch]` path) and regenerating `Cargo.lock`, in addition to
`--allow-dirty` for coldcard's bech32 exception. `pin` itself needs the same dirty
checkouts temporarily reverted first, since it refuses dirty checkouts regardless of
`--allow-dirty` (as does coldcard's bech32 patch, for the same reason).

A real fix belongs upstream: `silent-pay` and `spdk` hardcoding each other's and
`rust-psbt`'s *live* absolute paths means no worktree- or commit-based pin of either
can ever be reproduced without editing that path, and a floating git branch dependency
(no pinned `rev`) means two crates that both depend on it can silently diverge, as they
did here. Worth raising with the user rather than fixing unilaterally, since it touches
their build configuration across two repos.

### coldcard-firmware-baseline build

The coldcard worktree needed its `ENV` and simulator built from scratch (a fresh
`git worktree` has no build output, and `adapters/coldcard.py`'s own `doctor()` check for
this is never called by `check`'s preflight (filed as a gap below). Built following the
macOS section of coldcard-firmware's README:

```bash
git submodule update --init external/ckcc-protocol external/libngu external/micropython external/mpy-qr
# each of the above may itself need a nested submodule; git clone the LIVE checkout's
# already-fetched copy locally if the exact pinned commit isn't reachable from the
# submodule's own remote (this happened for ckcc-protocol at this pin)
python3 -m venv ENV && source ENV/bin/activate && pip install -U pip setuptools
pip install -r requirements.txt
export MPY_CFLAGS='-Wno-unused-but-set-variable -Wno-array-bounds -Wno-error=unknown-warning-option -Wno-error=deprecated-non-prototype -Wno-error=bitwise-instead-of-logical -Wno-unterminated-string-initialization -Wno-gnu-folding-constant'
make -C external/micropython/mpy-cross CFLAGS_EXTRA="$MPY_CFLAGS"
cd unix && make setup CFLAGS_EXTRA="$MPY_CFLAGS" && make ngu-setup && make CFLAGS_EXTRA="$MPY_CFLAGS"
```

`libngu`'s `Makefile` applies `bech32.patch` to the vendored `bech32` submodule during
`ngu-setup` and never commits it (`BECH32_PATCH ?= cd libs/bech32; git apply
../../bech32.patch || true`). Confirmed the live checkout carries the identical
uncommitted diff. This makes any correctly-built coldcard-firmware checkout report
dirty via plain `git status`, harmlessly and reproducibly, so the baseline check for
`--project harness` needs `--allow-dirty` specifically for this. Not a real
reproducibility gap; a documented codebase quirk, closer to the runbook's existing
"Known gotchas" than to an unpinned checkout.

### Gap found: preflight does not call an adapter's own `doctor()`

`adapters/coldcard.py.doctor()` already detects a missing `ENV`, but nothing in
`preflight.py` calls it before a worker starts, so a missing backend build crashes the
whole batch with a raw `FileNotFoundError` (per the "hard crash preferred" decision,
uncaught) instead of a clean preflight message naming the missing build. Worth fixing:
`run_preflight` should call `.doctor()` on each backend an actually-runnable scenario
needs, alongside the checkout and embit checks it already does.

### Phase 1 progress

Groups 0 to 5 are committed, one branch each, in one linear stack: `embit-api-sync`,
`expectations`, `version-pins`, `report-diff`, `exit-codes`, `docs-regression`. Not yet
done: the stable-pin baseline run, which classifies the four `unclassified` scenarios and
writes the first `interop.lock`.

## Phase 2: project skill and recipes

| # | Branch | Change |
|---|---|---|
| 6 | `regtest-recipe` | `scripts/musig2-regtest.sh` and `just musig2-regtest <architecture>`: start a throwaway regtest node, `treasury-wallet`, recipients file, `build_round1`, `run --psbt`, finalize, broadcast, `verify_onchain`, stop the node. Exits nonzero unless the on-chain scan finds the recipient. Verified for both key architectures on 2026-09-18. |
| 7 | `interop-skill` | Project skill `interop-regression` in `.claude/skills/`. Ordered checklist: read each checkout tip with its own VCS, unit tests, `check --exhaustive`, MuSig2 legs via the recipe, read the diff labels and summarize variances. It holds the questions (which versions, is dirty acceptable, which baseline), the reading rules for labels and exit codes, the per-repo VCS table and the GitButler commit rules. The harness holds the deterministic logic. |

The skill calls harness commands and does not reimplement them.

### Phase 2 progress

Groups 6 and 7 are committed on `regtest-recipe` and `interop-skill`, stacked above
`docs-regression`. Groups 8 and 9 are committed on `gitbutler-tips`, on top of the stack,
and the `gitbutler` FIXME in `checkouts.py` is closed. A `jj-stderr-fix` branch on top
of that closes a real bug found while pinning: `_jj_revision` merged jj's stderr into
its stdout capture, so an informational notice jj writes on success (for example after
something touches the colocated git refs directly) corrupted the reported commit id.
See "Baseline" below for the first pin.

## Workstream: GitButler checkouts

Closes the `gitbutler` FIXME in `checkouts.py`. Jade and bitsaga-seedsigner are managed with
GitButler, and today they are read as plain git, which returns the workspace commit.

### Problem

The workspace commit (`GitButler Workspace Commit` on `gitbutler/workspace`) is synthetic. It
is rewritten whenever any applied branch changes and exists only in that clone, so a pin of
it cannot be recreated elsewhere and changes for reasons unrelated to the code under test.

### Findings (2026-09-21)

- Jade: the workspace commit has one parent, `d6b21857`, the tip of stack `sp-musig`
  (`sp-core` sits below it). The workspace tree equals that parent's tree, so the parent
  is a real commit that fully identifies the code, apart from uncommitted changes.
- bitsaga-seedsigner: same shape (one parent, equal tree), but `but status` reports no
  applied stacks and five files are modified and uncommitted. The parent is only the
  base, so the working tree is not what any commit says. The dirty guard must catch this.
- Jade is also dirty: a modified submodule (`components/libwally-core/upstream`) and
  three untracked files.
- With several applied stacks the workspace commit is a merge of their tips, so the
  parents are the tips.

### Design

- Detect GitButler when `HEAD` is a symbolic ref under `gitbutler/` (currently
  `gitbutler/workspace`). An explicit `vcs: gitbutler` still overrides.
- The revision is the parent commit id(s) of the workspace commit, read with plain git
  (`git rev-list --parents -n1 HEAD`). One parent gives that id. Several give the sorted
  ids joined with `,`. This does not depend on the `but` binary, its version or its JSON.
- Dirty detection is unchanged: `git status` and `git diff HEAD` against the workspace
  commit already report uncommitted changes, and `pin` already refuses a dirty tree.
- Considered and rejected: pinning the workspace commit (unrecreatable); parsing
  `but status --json` (works, but ties the harness to `but` output, and is empty for
  bitsaga-seedsigner).

### Work groups

Each is a branch stacked on top of the current stack, per the GitButler rules above.

| # | Branch | Change | Tests |
|---|---|---|---|
| 8 | `gitbutler-tips` | Done. Detect GitButler, return parent tip(s) as the revision, remove the FIXME. Build the fixtures with plain git: a repo whose `gitbutler/workspace` HEAD is a merge of one or more branch tips. | one parent, several parents sorted, non-GitButler repo unchanged, dirty still detected, lock round trip through `pin`, pinned mismatch fails |
| 9 | `gitbutler-docs` | Done, on the same branch as group 8. Runbook: how the revision is derived, that a value with `,` is a set of tips, how to recreate one, and that uncommitted changes make it dirty. Update the skill's known gaps and the VCS table note. | none |

### Acceptance

- `doctor` shows Jade at `d6b21857` and bitsaga-seedsigner at its base parent, both marked
  dirty until the uncommitted changes are committed to a branch or discarded.
- After a commit to an applied branch, the reported revision changes to that commit. An
  unrelated workspace rewrite (for example reordering an unrelated stack) does not change
  it unless a tip moves.
- `pin` writes a value that `inspect_checkout` accepts on the next run.

### Risks and open decisions

- A value with `,` for several stacks is recreated by merging those tips. Recreation stays
  manual for now; an automated checkout helper is out of scope.
- Empty applied branches point at the base, so they pin the base. That is correct but can
  surprise.
- Whether a modified submodule should count as dirty. It does today, because `git status`
  reports it. Keep that unless it blocks real runs.
- GitButler could rename `gitbutler/workspace`; matching the `gitbutler/` prefix covers a
  rename within it, not a move out of it.

## Decisions

- VCS table above is confirmed.
- Baseline: pin the checkouts to a more stable set of revisions, run the full regression
  there, and treat that as the line in the sand. Only then classify the four
  undocumented failures from 2026-09-18 (`bip376-jade-two-way-sp-spend-to-p2tr`,
  `bip376-jade-two-way-sp-spend-to-p2wpkh` with `inputs-modifiable` still set, and
  `bip376-seedsigner-jade-sp-spend-to-p2tr`, `bip376-seedsigner-jade-sp-spend-to-p2wpkh`
  with `sighash_type (03)`). Nothing about upstream is guaranteed stable, so the pin is
  what makes a run reproducible.
- `artifacts/` stays out of version control. What is committed is the data needed to
  recreate a run, like `Cargo.lock`: the commit id of every checkout. Each run manifest
  records those ids so an artifact set can be regenerated.

- Unexpected exceptions (anything that is not an `InteropError`) crash the run with a
  traceback. There is no broad catch that skips to the next scenario, so an unknown failure
  cannot be mistaken for an ordinary scenario failure.

- GitButler checkouts stay supported through parent-tip pins (group 8). Pinning a separate
  plain clone is still allowed where an isolated build is wanted, but is not required. A
  modified or unpublished submodule keeps a checkout dirty, and so unpinnable, either way.

## Open decisions

- Exit codes: REGRESSION, UNCLASSIFIED and NEW fail a run; CHANGED, FIXED and NOT-RUN are
  reported but do not. Set by `FAILING_LABELS` in `regression.py`. Revisit if UNCLASSIFIED
  should only warn.
- How `expectations.yaml` relates to versions. Recommended: a single pin set in a
  generated `interop.lock` (one commit id per checkout, written by a `pin` command),
  with `expectations.yaml` valid for exactly that lock. Changing a pin and its
  expectations happens in the same commit, so git history is the record of what was
  supported when. The alternative is named profiles (for example `stable` and `tip`),
  each with its own lock and expectations. That supports comparing the stable line with
  upstream movement but doubles the maintenance. Start with one lock and add profiles
  only if a second line is actually needed.
- `musig2-sp-three-way` and `musig2-sp-signet-treasury` need a third signer and, for
  signet, real funding. They stay `needs-external-psbt` until that is scheduled.
