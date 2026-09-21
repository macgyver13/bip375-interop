---
name: interop-regression
description: Run and interpret a BIP-375 interop regression in this repo. Pins or checks checkout versions, runs preflight and `check`, drives the MuSig2-SP regtest legs, and reports only the variances against expectations.yaml and the previous run. Use when asked to run, compare or triage an interop regression, to establish a baseline, or to update expectations.yaml or interop.lock.
---

# Interop regression

The harness owns the logic (expectations, labels, preflight, exit codes). This skill is the
order of operations and the judgment calls. Do not reimplement harness behavior here.

Source of truth: `docs/regression-plan.md` (why and status), `docs/runbook.md` under
"Regression groups" (commands, labels, exit codes).

## Decide first

Ask only for what the request does not already say:

1. **Which versions?** Test what the lock pins (`interop.lock`), or establish a new
   baseline (`pin`). A new baseline needs clean checkouts and an explicit choice of
   revision per checkout from the user.
2. **Is a development run acceptable?** `--allow-dirty` makes the result non-reproducible.
   Say so in the report.
3. **Compared to what?** The default is the previous finalized batch of the same project.

## Steps

Run from the repo root with the venv active (`source .venv/bin/activate`); without it, use
`PYTHONPATH=src python3 -m bip375_interop.cli` in place of `bip375-interop`. Long runs
(`check --exhaustive` is several minutes) go in the background.

1. `bip375-interop doctor` (add `--allow-dirty` only for a development run). Record each
   checkout's VCS and tip. Note any that are dirty or differ from `interop.lock`.
2. Baseline only: after the user picks revisions and the checkouts are clean,
   `bip375-interop pin`, then show the lock for review. Never pin a dirty tree.
3. `python -m pytest -q tests`. Stop and report if it fails.
4. `bip375-interop check --project harness --exhaustive`. Add
   `--psbt SCENARIO=PATH` for any MuSig2-SP scenario that already has an initial PSBT.
5. MuSig2-SP legs that have no PSBT: `just musig2-regtest aggregate-then-derive` and
   `just musig2-regtest derive-then-aggregate`. They need `bitcoind` and `bitcoin-cli`
   (`BITCOIND` / `BITCOIN_CLI`); `ALLOW_DIRTY=1` for a development run. A leg passes only
   when it prints `PASS`.
6. Read `report.json` in the new `artifacts/batches/` directory, not just the summary.

## Reading the result

- Exit 2 is a preflight or configuration failure: nothing ran. Fix the named problems.
- Labels: `REGRESSION`, `UNCLASSIFIED` and `NEW` fail the run. `CHANGED`, `FIXED` and
  `NOT-RUN` are reported but pass. A MuSig2 scenario without a PSBT is `NOT-RUN`, which
  is why step 5 exists.
- `FIXED` or `CHANGED` usually means `expectations.yaml` needs an update. Propose it,
  with the evidence, and let the user decide. Never change an expectation to make a run
  green, and never classify an `unclassified` scenario without evidence from a run.
- A failure mentioning `embit`, an `ImportError` or an unpack `ValueError` is usually an
  API drift, not a device regression. Check which embit the interpreter imports (the
  `.venv` must use the embit checkout editable, not a copy in site-packages) and its tip
  against the pin first. Preflight catches a wrong location and the known shape change.
- Do not pipe `check` through `tail` or similar to shorten output: the pipeline's exit
  status becomes the last command's, and a failing run looks like success. Redirect to a
  file instead.

## Report

Lead with the outcome, then only what changed:

- Versions tested: each checkout's tip, and whether the run was pinned or a development run.
- Counts by label, then a table of the variances (scenario, label, reason).
- MuSig2-SP legs: scenario, architecture, txid, `PASS` or the failing step.
- Anything you could not check, and what you assumed.

## Version control

Use each repo's own tool:

| Repo | Tool |
|---|---|
| bip375-interop, Jade, bitsaga-seedsigner | GitButler (`but`); reported revision is workspace-parent tips, comma-separated when several stacks are applied, never the synthetic workspace commit |
| silent-pay, bip375-test-generator, coldcard-firmware, spdk | jj (read the tip with `jj log -r @`, not git HEAD) |
| seedsigner, caravan, embit | git |

Changes to this repo go through GitButler: one branch per coherent change group, stacked on
top of the existing stack (`but branch list` shows the real stacking; `but status` draws
the branches as siblings). Commit with `but diff` then `but commit -b <branch> -m "..."
<id> ...`. Amend fixes into the unpublished commit they belong to. Keep messages short,
with no co-author trailers and no em or en dashes. Do not push or open a PR unless asked.
If a commit is refused for depending on another branch, stack the new branch above the top
of the stack.

## Known gaps

- `expectations.yaml` is not yet tied to a specific lock; it applies to the lock in the
  same commit.
