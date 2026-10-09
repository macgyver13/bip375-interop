# PSBT Interop Lab workstream 1

Pinned lab: `psbt-interop-lab@0.11.0`. [workstream1-suite.json](workstream1-suite.json) contains 25 SHA256-committed PSBTv2 parser fixtures: all eight `*.psbt` snapshots from `artifacts/20261006T155813.091645Z-bip375-coldcard-jade-two-way-ca808841/` and the 17 `expected.psbt` values in the current Coldcard firmware `testing/bip375_test_vectors.json` (`sha256:7e83f444756d748db999041c7b23ba4c7cf1e9147f727bd32f259d3d478b2a57`). The plan's old `bip375_coldcard_workflow_vectors.json` path no longer exists in that checkout. [workstream1-results.json](workstream1-results.json) records each fixture's outcome.

Run with Docker context `colima` and Node 24:

```sh
npx --yes psbt-interop-lab@0.11.0 list
npx --yes psbt-interop-lab@0.11.0 run --scenario bip352-multi-output-lifecycle-rust-psbt-v2
npx --yes psbt-interop-lab@0.11.0 matrix --suite-manifest interop-lab/workstream1-suite.json --junit /tmp/bip375-interop-lab.xml --sarif /tmp/bip375-interop-lab.sarif
```

`psbt-lab list` IDs relevant to this workstream:

- BIP-375: `bip375-official-reference-vectors`, `bip375-official-vectors-rust-psbt-v2`, `bip375-sender-workflow-rust-psbt-v2`, `bip375-advanced-sender-workflows-rust-psbt-v2`, `bip375-core-funded-sender-rust-psbt-v2`, `bip375-core-funded-multi-input-rust-psbt-v2`.
- BIP-352: `bip352-sender-receiver-lifecycle-rust-psbt-v2`, `bip352-multi-output-lifecycle-rust-psbt-v2`, `bip352-labels-spdk`, `bip352-multi-receiver-spdk`, `bip352-spdk-wallet-interop`.
- BIP-376: `bip376-spend-workflow-rust-psbt-v2`.

The smoke scenario passed. The matrix ran all 59 bundled scenarios (all passed) plus these 25 custom parser scenarios. Its exit status was 1 because 10 custom assertions expected libwally to accept a PSBT it rejected. The run also reported three existing review findings in bundled scenarios. The complete matrix report is `/private/tmp/artifacts/20261007182928-d8ace91f/report.json`; JUnit and SARIF were written under `/private/tmp/bip375-interop-lab-workstream1.*`.

| Check | Result | Finding |
| --- | --- | --- |
| Lab parser | 25/25 accepted | All fixtures satisfy the lab's base parser. |
| rust-psbt-v2 native parse | 25/25 accepted | No parse divergence. |
| libwally 1.5.4 native parse | 15/25 accepted | It rejects exactly the 10 unresolved PSBTs without `PSBT_OUT_SCRIPT`. Adding the resolved script to an initial snapshot makes libwally accept it. [BIP-375 permits an absent script while `PSBT_OUT_SP_V0_INFO` is present](https://github.com/bitcoin/bips/blob/master/bip-0375.mediawiki). This is a library compatibility limitation, not evidence of invalid fixtures. |
| rust-psbt-v2 direct roundtrip | 15/25 preserve a valid PSBT | On the 10 unresolved PSBTs, it adds an empty `PSBT_OUT_SCRIPT` while the modifiable flags remain set. The lab then rejects the returned PSBT. |
| libwally direct roundtrip | 0/25 preserve a BIP-375-valid PSBT | It rejects the 10 unresolved inputs. For the other 15, it removes zero-valued `PSBT_GLOBAL_TX_MODIFIABLE`; the lab rejects the returned PSBT because that field is required after the silent-payment script is computed. |

The roundtrip checks sent the manifest's PSBTs directly to the two pinned Docker adapter processes using the `psbt-lab.adapter/0.2` `roundtrip` operation, then checked their responses with the lab's own `parsePsbtDocument` and `assertPsbtTransition('roundtrip', ...)`. They did not run as custom-suite cells: schema 0.2 restricts parser fixtures to `mutate` and `compare-parsers`. Version 0.11.0 also forbids `--external-only` or scenario selection with `--suite-manifest`, so the plan's exact matrix command cannot run. `parse-matrix --runtime local` has no rust-psbt-v2 or libwally native bundle here.

The suite deliberately expects all three parsers to accept each fixture, preserving the 10 libwally failures as visible evidence. These documented exceptions satisfy the workstream's triage path; a green matrix still requires fixes in the affected adapters. Do not change those expectations to make the matrix green without resolving or explicitly recording the compatibility exception.
