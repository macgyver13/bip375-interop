#!/bin/bash
# Both MuSig2-SP regtest legs (scripts/musig2-regtest.sh) inside bip375-regtest, against
# the MuSig2 profile's coldcard and jade pins.
#
# Build (each image builds on the ones before):
#   docker build -t bip375-coldcard-musig2 --build-arg CC_TAG=bip375-interop/baseline-2026-09-22 \
#     -f images/linux/coldcard.Dockerfile images/linux
#   docker build -t bip375-jade-musig2 --build-arg JADE_TAG=bip375-interop/baseline-musig2-2026-09-23 \
#     -f images/linux/jade.Dockerfile images/linux
#   docker build -t bip375-mixed-musig2 --build-arg COLDCARD_IMAGE=bip375-coldcard-musig2 \
#     --build-arg JADE_IMAGE=bip375-jade-musig2 -f images/linux/mixed.Dockerfile images/linux
#   docker build -t bip375-sp-demo -f images/linux/sp-demo.Dockerfile images/linux
#   docker build -t bip375-regtest -f images/linux/regtest.Dockerfile images/linux
# Run from the repo root, mounting as for check.sh:
#   docker run --rm -i -v "$PWD:/repo:ro" bip375-regtest bash -s < images/linux/regtest.sh
set -euo pipefail
rm -rf /work && mkdir /work && (cd /repo && tar --exclude=./artifacts --exclude=__pycache__ -cf - .) | tar -C /work -xf -
python3 -m venv /opt/h && /opt/h/bin/pip install -q pyyaml cbor2 pyserial && /opt/h/bin/pip install -q -e /embit
mkdir -p /cfg /out && cat > /cfg/interop.yaml <<YAML
artifact_root: /out/artifacts
checkouts:
  coldcard: {path: /cc}
  jade: {path: /jade}
YAML
printf 'checkouts:\n  coldcard: 80a27ae5326fc703de4c9372285dadef9152047b\n  jade: a724e3767d4fdf27c289f70d88b91b3e17e8d3c6\n' > /cfg/interop.lock
unset BIP375_JADE_QEMU
cd /work
export PATH=/opt/h/bin:$PATH CONFIG=/cfg/interop.yaml
for arch in aggregate-then-derive derive-then-aggregate; do
  WORK_DIR=/out/$arch scripts/musig2-regtest.sh $arch 2>&1 | tail -4
done
