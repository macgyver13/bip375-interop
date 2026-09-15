# bip375-interop

Reproducible interoperability harness for collaborative Silent Payment signing.
It deliberately separates three protocol suites:

- `bip375`: ordinary eligible inputs signed by Coldcard, Jade, or upstream SeedSigner.
- `musig2-sp`: MuSig2 inputs signed by Coldcard, Jade, or BitSaga SeedSigner.
- `frost-sp`: reserved extension point. It is recognized but rejected until its protocol
  fields, rounds, and verification rules are implemented.

The harness coordinates existing device-native tests; it does not implement signing or
silently substitute a software cosigner. Every configured participant is an independent
emulator process or a guided physical-device handoff.

## Quick start

```sh
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp config/interop.example.yaml interop.yaml
bip375-interop doctor --allow-dirty
bip375-interop validate scenarios/bip375-three-way.yaml
pytest
```

Pin every checkout `revision` before treating a run as reproducible. Dirty sources are
rejected by default. `--allow-dirty` is intended for development and records the working
tree state as non-reproducible.

## Design boundaries

Scenario YAML contains common signer/input/output intent and a suite-specific block.
Adapters own device lifecycle and transport. Suites own round sequencing and protocol
verification. MuSig2 nonce handling and descriptor rules therefore do not leak into the
plain BIP-375 or future FROST suites.

Each run writes an isolated artifact directory containing the input and output from every
signer, semantic checks, logs, and a hash manifest. Native checkout paths are inputs, not
scratch space. Builds and fixture overlays belong in harness-managed temporary/cache
directories.

Phase 2 adds a persistent external worker protocol, strict PSBTv2 map merging, and a
working upstream SeedSigner BIP-375 path:

```sh
bip375-interop plan scenarios/bip375-seedsigner-single.yaml
bip375-interop run scenarios/bip375-seedsigner-single.yaml --psbt unresolved.psbt
```

The worker accepts only published test-seed identifiers, stays alive for every required
round, and writes each returned and merged PSBT to an isolated run directory. Coldcard
can consume external MuSig2 fixture bundles through a disposable checkout overlay.
Coldcard still needs its per-PSBT transport before it can join an arbitrary mixed
run. Jade's persistent QEMU transport is available for the single-device lane;
the next fixture-generator milestone makes it usable in a mixed run.

Jade now also has a single-command QEMU smoke lane. It builds the documented
``--dev --ci --psram`` image when needed, starts an isolated container on an
ephemeral port, runs Jade's own BIP-375 resolve-and-sign fixture through the
JSON-lines worker, strictly merges the returned PSBT, and tears the container
down:

```sh
bip375-interop --allow-dirty smoke jade
```

The command reports `mixed_device_psbt_interoperability: "not exercised"`.
It is evidence that the Jade emulator transport works; it is not yet evidence
that Jade can process another backend's modified PSBT. The same persistent
worker is the transport used by future mixed BIP-375 scenarios.

The first generated mixed lane is SeedSigner plus Jade. Install both upstream
projects' pinned Python requirements into the harness environment, then run:

```sh
python -m pip install -r /Users/macgyver/src/seedsigner/requirements.txt
python -m pip install -r /Users/macgyver/src/Jade/requirements.txt
bip375-interop --allow-dirty run-generated \
  scenarios/bip375-seedsigner-jade-two-way.yaml
```

`run-generated` derives each P2WPKH test input from its declared published
test seed, builds an unresolved PSBTv2 with SIGHASH_ALL, starts Jade QEMU on
an ephemeral host port, and retains the same Jade connection across the three
BIP-375 phases. The run is not considered proven until it completes against a
live Docker daemon.

Coldcard's native adapter runs its upstream simulator suites in a disposable
checkout copy. The BIP-375 lane covers `test_bip375_vectors.py`,
`test_silentpayments.py`, and `test_bip352_vectors.py`; the MuSig2-SP lane
covers `test_musig2_silentpayments.py` and `test_musig2_sp_signers.py`.
On Apple Silicon, the adapter discovers Homebrew's
`/opt/homebrew/lib/libsecp256k1.dylib` automatically; set `PYSECP_SO` to
override the host library path.
