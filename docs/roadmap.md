# Delivery roadmap

## Milestone 1: reproducible native lanes

- Pin source and submodule revisions and capture tool versions.
- Build each emulator from a disposable source snapshot.
- Run Coldcard, Jade, upstream SeedSigner, and BitSaga native Silent Payment suites.
- Import BIP-375 vectors from `bip375-test-generator` without copying them into tracked
  firmware fixture directories.

## Milestone 2: external fixture adapters

- [ ] Generate one-, two-, and three-owner plain BIP-375 PSBTv2 scenarios.
  The first deterministic P2WPKH-only SeedSigner/Jade two-owner scenario is available;
  expand it only after its live QEMU run passes.
- [x] Keep each virtual signer in an isolated worker process and strictly merge PSBT maps.
- [x] Permit only BIP-375-authorized output-script resolution and modifiable-flag clearing.
- [x] Run an unresolved PSBT through the actual upstream SeedSigner BIP-375 runtime.
- [x] Overlay external Coldcard fixtures in a disposable checkout copy.
- [ ] Add arbitrary-PSBT transports for Coldcard and Jade.
  Jade has a persistent QEMU worker with dynamic host-port allocation; Coldcard remains pending.
- Exercise same-backend, pairwise mixed, and Coldcard/Jade/SeedSigner three-way runs.
- Reject conflicts, transaction-intent mutation, premature signatures, invalid proofs,
  and incomplete ECDH coverage.

## Milestone 3: MuSig2 plus Silent Payments

- Add the narrow configurable fixture CLI to Silent Pay; it must emit an unresolved PSBT
  and descriptor without synthesized signer contributions.
- Drive two-round Coldcard, Jade, and BitSaga instances while preserving each nonce session.
- Verify aggregate-then-derive multipath receive/change descriptors on every backend.
- Treat derive-then-aggregate as host conformance and an explicit unsupported firmware
  result until a backend advertises it.

## Milestone 4: optional regtest and physical devices

- Fund and broadcast completed fixtures on an ephemeral Bitcoin Core regtest node.
- Export resumable raw/base64/QR artifact bundles for guided hardware handoff.

## Future suite: FROST plus Silent Payments

`frost-sp` already has a stable suite identifier and an isolated configuration namespace.
Implementation waits for concrete descriptor, PSBT field, threshold, round, nonce, and
verification requirements. It must add its own suite handler and capabilities without
changing existing scenario or adapter contracts.
