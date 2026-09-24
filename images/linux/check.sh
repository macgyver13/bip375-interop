#!/bin/bash
# A profile's `check --exhaustive` inside bip375-check, against that profile's lock and
# expectations: PROFILE=baseline (default) or PROFILE=baseline-musig2, whose image builds
# check.Dockerfile with --build-arg BASE_IMAGE=bip375-mixed-musig2 (see regtest.sh).
#
# Build (each image builds on the ones before):
#   docker build -t bip375-coldcard -f images/linux/coldcard.Dockerfile images/linux
#   docker build -t bip375-jade     -f images/linux/jade.Dockerfile     images/linux
#   docker build -t bip375-mixed    -f images/linux/mixed.Dockerfile    images/linux
#   docker build -t bip375-sp-demo  -f images/linux/sp-demo.Dockerfile  images/linux
#   ctx=$(mktemp -d) && git archive HEAD spdk-cli | tar -x -C "$ctx" \
#     && docker build -t bip375-check -f images/linux/check.Dockerfile "$ctx"
#   (MuSig2 profile: after bip375-mixed-musig2, build the same with -t bip375-check-musig2
#   --build-arg BASE_IMAGE=bip375-mixed-musig2.)
# Run from the repo root (the harness is mounted read-only; with Colima it must be under
# your home directory, the only path it shares with its VM):
#   docker run --rm -i -v "$PWD:/repo:ro" bip375-check bash -s < images/linux/check.sh
#   docker run --rm -i -e PROFILE=baseline-musig2 -v "$PWD:/repo:ro" bip375-check-musig2 \
#     bash -s < images/linux/check.sh
set -euo pipefail
PROFILE=${PROFILE:-baseline}
# The host's spdk-cli/target holds host binaries; the image's build takes its place.
rm -rf /work && mkdir /work
(cd /repo && tar --exclude=./artifacts --exclude=./spdk-cli/target --exclude=__pycache__ -cf - .) | tar -C /work -xf -
mkdir -p /work/spdk-cli/target/release && cp /opt/spdk-cli/spdk-cli /work/spdk-cli/target/release/
# Harness venv. embit is editable so preflight sees it imported from its checkout.
# SeedSigner goes in without its own embit pin, with the Mac's versions of the rest, so
# it signs with the pinned embit like everything else.
python3 -m venv /opt/h && /opt/h/bin/pip install -q pyyaml cbor2 pyserial \
  Pillow==12.3.0 qrcode==7.3.1 urtypes==1.0.1 pyzbar==0.1.9 \
  && /opt/h/bin/pip install -q -e /embit && /opt/h/bin/pip install -q --no-deps -e /seedsigner
mkdir -p /cfg /out && cat > /cfg/interop.yaml <<YAML
artifact_root: /out/artifacts
checkouts:
  coldcard: {path: /cc}
  jade: {path: /jade}
  seedsigner: {path: /seedsigner}
  caravan: {path: /caravan}
  spdk: {path: /spdk}
  embit: {path: /embit}
YAML
grep '^suites:' /work/$PROFILE/interop.yaml >> /cfg/interop.yaml || true
cp /work/$PROFILE/interop.lock /work/$PROFILE/expectations.yaml /cfg/
# Not on PATH: the Jade worker must find qemu-xtensa under IDF_TOOLS_PATH.
unset BIP375_JADE_QEMU
cd /work
h() { PYTHONPATH=/work/src /opt/h/bin/python -m bip375_interop.cli --config /cfg/interop.yaml "$@"; }
echo "== doctor"; h doctor | /opt/h/bin/python -c 'import json,sys; [print(s["name"], s["revision"][:8], "dirty" if s["dirty"] else "clean", s["byproducts"]) for s in json.load(sys.stdin)]'
echo "== check --exhaustive"; h check --project harness --exhaustive
