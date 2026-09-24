# Portability plan: running the harness without local checkouts

Goal: someone who clones only this repo can run both baseline profiles
(`check --project harness --exhaustive`) and both MuSig2-SP regtest legs, and get the
same labels as `docs/regression-plan.md` "Baseline results (2026-09-23)" (no regressions
in either profile) and the same regtest fingerprint for both MuSig2-SP legs.

`musig2-regtest.sh` prints a `FINGERPRINT` line (also `fingerprint.json` in the run's
artifacts): the spent outpoints, the outputs as a sorted set of `(scriptPubKey, sats)`,
the SP output the on-chain scan found, the txid and the wtxid. Output order is random, so
each leg has two txids; the wtxid changes every run, as the MuSig2 signatures differ
(what fresh nonces should produce). Neither is compared. The fields that must repeat, identical across two
macOS and two arm64 Linux runs on 2026-09-24:

- aggregate-then-derive: input `812dd031...:0`, SP output `5120430a5f56398f759fc84bf14d54fdf136f4087ec71f57626ddf94b2b54bde8f64`
- derive-then-aggregate: input `c2d757e7...:0`, SP output `5120f25c02aa6a137215d35b0a06ed85419cdd84ce5760205d6777e396287024df1f`

## Where things stand (2026-09-23)

| Area | State |
|---|---|
| Profiles | `baseline/` (BIP-375 line, `suites: [bip375]`) and `baseline-musig2/` (MuSig2 line), one shared `expectations.yaml` |
| Profile configs | committed, `~/src/...` paths, no hardcoded home directory |
| Pins | every pin in both locks is on a public remote; the 2026-09-23 pins are tagged (below) |
| Rust deps | silent-pay, spdk and `spdk-cli` depend on each other and on rust-psbt by git `rev`; `spdk-cli/Cargo.lock` is committed |
| Frozen worktrees | jade (x2), coldcard (x2), silent-pay, spdk and caravan run from worktrees at their pins, each with its own build |
| Toolchains | still whatever the host has: ESP-IDF, qemu-xtensa, Coldcard's `ENV`, Rust, Node, bitcoind |
| Clone step | none; checkouts are hand-made |

Tags:

| Tag | Commits |
|---|---|
| `bip375-interop/baseline-2026-09-24` | silent-pay `765af1c3` (the GUI behind an optional `gui` feature; the pin in both locks) |
| `bip375-interop/baseline-2026-09-23` | jade `3cf19c81`, coldcard `d210635d`, silent-pay `bed75f5d` (superseded), spdk `5c8a3823` |
| `bip375-interop/baseline-musig2-2026-09-23` | jade `a724e376` |
| `bip375-interop/baseline-2026-09-22` | coldcard `80a27ae5` (MuSig2 profile pin) and its `ckcc-protocol` / `libngu` submodule commits; jade `521cdcad` (retired) |

## Recommendation

Ship the harness as published container images: someone who wants to reproduce the
baseline pulls and runs them and never builds a toolchain. Images do not fix
unpublished commits or host paths, so the order is: publish the sources, make the
harness able to fetch and verify them without a developer's machine, then build and
publish images from CI.

Keep the local workflow as the primary dev loop. The images are for reproducing the
baseline, for CI, and for testing a local change without installing its toolchain.

A Nix flake would pin toolchains more tightly, but it asks a lot of contributors and
ESP-IDF under Nix is its own project. A `.devcontainer/devcontainer.json` that points
at the runtime image gives VS Code and Codespaces users an environment for free.

## Phases

### Phase 0: publish the pins (remaining)

| Item | Action |
|---|---|
| caravan `b3b39754` | public only on `jvgelder/caravan`; mirror to your fork and tag |
| embit `f18d23bd` | pushed to your fork's `feat/silent-payments-V2` (2026-09-24); tag |

```yaml
jade: https://github.com/macgyver13/Jade.git
coldcard:
  url: https://github.com/macgyver13/coldcard-firmware.git
  submodules:
    external/ckcc-protocol: https://github.com/macgyver13/ckcc-protocol.git
    external/libngu: https://github.com/macgyver13/libngu.git
```

The overrides matter for coldcard `80a27ae5`, whose `.gitmodules` names upstream repos
that do not have its submodule commits (GitHub serves them through the fork network,
but that should not be relied on).

Why a separate file instead of `url` in `interop.lock`: the lock stays machine-written
by `pin`, the URLs are shared by both profiles while each profile has its own lock, and
it mirrors `Cargo.toml` (where) versus `Cargo.lock` (which commit).

Optional: move the MuSig2 profile's coldcard to the `musig_silentpayments` tip, which now
sits on the BIP-375 line (`kwwykxzr`), so both coldcard pins share history.

Done when a script clones every `sources.yaml` URL and finds every lock commit, for both
profiles, on a machine with no local clones.

### Phase 1: harness prerequisites and `fetch`

These help local runs too, so they come before any image.

1. **Versions without `.git`.** Dropped. Images keep the shallow `.git` that `fetch`
   clones, so git still reports each tree's commit and the dirty check still works. A
   recorded commit file could not detect later edits, and a shallow `.git` costs little.
   A checkout path with no `.git` or `.jj` is now an error instead of resolving to a
   repository above it.
2. **Known build byproducts.** Done. A built coldcard carries libngu's `bech32.patch`,
   and a built jade rewrites `dependencies.lock.esp32` to the local ESP-IDF version.
   `KNOWN_BYPRODUCTS` in `checkouts.py` leaves both out of the dirty check (a moved
   libngu commit still counts as dirty), and each run's manifest lists the byproducts
   it excused. Both profiles now pass `doctor` without `--allow-dirty`.
3. **`bip375-interop fetch --config <profile>`.** Done. Clones each checkout at the
   profile's lock commit from `sources.yaml` into `.checkouts/<name>@<rev12>` (gitignored),
   with its submodule URL overrides and only the submodules it lists, and writes
   `<profile>/interop.fetched.yaml` pointing at them. Both profiles fetch from GitHub and
   pass `doctor` clean (7 checkouts, 647 MB; the second profile reuses five).
   `bip375-interop build [names] [--dry-run]` then runs each checkout's `plan_build`
   (adapters, plus `sp-demo` and an editable embit). On macOS the fetched baseline built
   with `IDF_PATH=~/src/esp-idf`, `CARGO_TARGET_DIR`, `CFLAGS_EXTRA=-Wno-error` (current
   clang rejects micropython's `mpy-cross`) and GNU sed first on `PATH` (Jade's
   `switch_to.sh` uses `sed -i` without a suffix), leaving only the known byproducts. The
   `seedsigner` and `embit` steps install into the running Python, so run them in a venv.
   `smoke coldcard` and `smoke jade` on the fetched, macOS-built trees pass (577 and 772
   bytes, as on Linux). Phase 1 counts macOS as covered by that plus the macOS baseline at
   the same pins; a full fetched `check` on macOS waits for CI.
4. **Regtest legs without `cargo`.** Done. `SP_DEMO_BIN=<dir>` makes `musig2-regtest.sh`
   run prebuilt `sp-demo` binaries from that directory, with no cargo or silent-pay
   checkout. The aggregate-then-derive leg passed this way with cargo off `PATH`.
5. **Linux paths.** Done, and verified on arm64 Linux (`images/linux/`). qemu-xtensa
   discovery now honors `IDF_TOOLS_PATH` (default `~/.espressif`). `PYSECP_SO` needed
   nothing: unset, coldcard's `pysecp256k1` falls back to `find_library`, which finds the
   system library on Linux.

Done when a clean macOS or Linux host with the toolchains installed runs `fetch`, builds
the components, and reproduces both profiles' labels without `--allow-dirty`.

### Phase 2: images

Status (2026-09-24): `images/Dockerfile` builds one runtime image per baseline
(`bip375-interop:baseline`, `bip375-interop:baseline-musig2`) on arm64. It clones the
harness from GitHub at `HARNESS_REF`, and the harness itself fetches and builds every
checkout (`fetch`, `build`). Stages before `runtime-base` do not depend on the baseline,
so both images share those layers and each adds its own coldcard and jade. Both images'
`check --exhaustive` match macOS (score 76, 19 of 25, all STEADY), and both MuSig2 legs
pass from the MuSig2 image. Each image is 4.4 GB; building one baseline needs about
20 GB of Docker disk, so CI (Phase 3) is the better place to build them. Not done yet:
the `-dev` variant, amd64, and publishing. `images/linux/` holds the earlier per-tool
prototypes this replaces.

The original design follows.

Component images, each built from its pin and tagged with it, rebuilt only when that
pin moves:

| Component | Built from | Produces |
|---|---|---|
| jade, once per line (`sp-core`, `sp-musig`) | Jade's `blockstream/jade_builder@sha256:...`, `switch_to.sh qemu --dev --ci` | `flash_image.bin`, `qemu_efuse.bin` |
| coldcard, once per line | Debian with coldcard's Linux build deps and `libsdl2` | unix simulator and its `ENV` |
| rust | `rust:<pinned>` | silent-pay `sp-demo` binaries, `spdk-cli` |
| caravan | `node:<pinned>` | built `@caravan/psbt` (`npm install`: the pinned lock file is out of sync, so `npm ci` fails) |

The runtime image, `bip375-interop:<hash of both locks>`, is Python plus bitcoind from a
release tarball checked by sha256, Espressif's qemu-xtensa, the pure-Python checkouts
(seedsigner, embit), the harness, and every component's output. It is
the only image a user pulls:

```sh
docker run --rm -v "$PWD/artifacts:/work/artifacts" ghcr.io/macgyver13/bip375-interop:<hash> check --config baseline/interop.yaml --project harness --exhaustive
docker run --rm -v "$PWD/artifacts:/work/artifacts" ghcr.io/macgyver13/bip375-interop:<hash> regtest aggregate-then-derive
```

A `-dev` variant keeps the toolchains, so a mounted local checkout can be rebuilt and
tested without installing its toolchain on the host.

Notes:

- The jade image builds with ESP-IDF v5.5.5, which both jade pins ask for; local builds
  so far used v5.5.4.
- The flash image is ESP32 firmware, not a host binary, so the jade stage runs on the
  build platform and its output goes unchanged into amd64 and arm64 runtime images. Check
  early that Espressif publishes an aarch64 Linux qemu-xtensa.
- The coldcard simulator runs `--headless`: it needs the SDL2 library and PySDL2, but no
  X server and no `xvfb`.

### Phase 3: CI

GitHub Actions builds the components whose pin changed, then the runtime image for
linux/amd64 and linux/arm64, pushes to GHCR, and runs both profiles and both regtest legs.
Done when a machine with only Docker and a clone of this repo reproduces the goal above.

## Hazards found so far

- **GitButler workspace commits in submodules.** A submodule checkout managed by
  GitButler sits on a synthetic workspace commit, and a parent-repo commit that includes
  the submodule records that unfetchable commit (jade `sp-musig` did this with libwally).
  Leave submodule pointer changes out of commits unless the submodule is meant to move.
- **Rebases move worktrees.** Rebasing `sp-core` in the live jade checkout also moved the
  detached `Jade-sp-core` worktree, leaving its flash image stale. Preflight caught it
  (`expected X, found Y`). `fetch` checkouts keyed by commit avoid this.
- **Out-of-sync lock files upstream.** caravan's pinned `package-lock.json` does not match
  its `package.json`.
- **embit and a system libsecp256k1.** embit's ctypes bindings use the pre-0.2
  `schnorrsig_verify` signature and load any system libsecp256k1 they find. With 0.2 or
  later (Ubuntu 24.04 ships 0.2.0) the harness segfaults verifying a taproot signature.
  macOS never hits it: `find_library` does not search `/opt/homebrew`, so embit runs pure
  Python. Images keep libsecp256k1 off the loader path (unpacked under `/opt/secp`, with
  `PYSECP_SO` pointing coldcard at it). A Linux host with a system libsecp256k1 still
  crashes.
- **Jade's builder image is amd64 only.** `blockstream/jade_builder` has no arm64 build,
  so arm64 images install ESP-IDF at the pinned `ESP_IDF_COMMIT` themselves.
- **Tags containing `/` break Jade's `fwprep.py`.** It names the OTA image after the
  version (`git describe`), so a build exactly at `bip375-interop/...` writes into a
  missing directory. qemu does not need `fwprep.py`; `make_flash_img.sh` works from
  `build/jade.bin`.
- **Coldcard's `make setup` initializes every micropython submodule,** in full
  (`git submodule update --init`, no depth), whatever `sources.yaml` lists, so it needs
  network access and the tree ends up at 1.3 GB.
- **Caravan's production dependencies are most of its size:** `npm prune --omit=dev`
  only takes `node_modules` from 1.0 GB to 0.7 GB.
- **Coldcard's `make setup` is not rerunnable.** A second run's `ln -s $(PORT_TOP) l-port`
  follows the existing link and leaves an untracked `ports/unix/unix` in micropython.
  Refetch a coldcard whose build failed rather than rebuilding it in place.
- **Coldcard on arm64 Linux needs `-Wno-error=clobbered`** beyond the README's Ubuntu
  24.04 flags (MicroPython unix `main.c`).
- **micropython's `lib/lwip` cannot be cloned shallow** (savannah serves only dumb HTTP).
  The unix simulator needs only `axtls`, `berkeley-db-1.xx`, `libffi`, `libhydrogen` and
  `mbedtls` of micropython's libraries.
- **silent-pay depended on `slint` unconditionally** (fixed in `765af1c3`). Building
  `sp-demo` compiled the GUI stack, which on Linux needs fontconfig, xkbcommon, udev,
  libinput, gbm and seat headers. `slint`, `copypasta` and `rfd` are now behind a default
  `gui` feature that `sp-demo` turns off.
- **ckcc-protocol `dec724e2` is on no ref.** Coldcard `d210635d` pins it; GitHub serves it
  only through the fork network. A tag on `macgyver13/ckcc-protocol` would anchor it.

## Out of scope

- Physical devices (roadmap Milestone 4). A container cannot pass through USB signers
  portably.
- `musig2-sp-signet-treasury`: needs a funded signet UTXO, not a toolchain.

## Decisions

- Every fork involved is public, including rust-psbt.
- bip375-test-generator and bitsaga-seedsigner are not profile checkouts: no scenario
  that can run uses them (bitsaga's two MuSig2 scenarios have no initial PSBT).
- Two baseline profiles: the BIP-375 line is primary; the MuSig2 profile must also pass
  every BIP-375 expectation.
- Jade firmware is rebuilt from source in CI on every jade pin change.
- Components are shipped as published images. Users pull; they do not build.
- Source URLs live in a committed `sources.yaml`; `interop.lock` stays commit ids only.
- Tags are named per profile: `bip375-interop/baseline-<date>` and
  `bip375-interop/baseline-musig2-<date>`.
- The coldcard simulator runs headless with no `xvfb`.
