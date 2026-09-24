#!/bin/bash
# Pinned doctor plus the mixed Coldcard/Jade BIP-375 scenarios, inside bip375-mixed.
#
# Build (each image builds on the one before):
#   docker build -t bip375-coldcard -f images/linux/coldcard.Dockerfile images/linux
#   docker build -t bip375-jade     -f images/linux/jade.Dockerfile     images/linux
#   docker build -t bip375-mixed    -f images/linux/mixed.Dockerfile    images/linux
# Run from the repo root (the harness and embit are mounted read-only; with Colima, both
# must be under your home directory, the only path it shares with its VM):
#   docker run --rm -i -v "$PWD:/repo:ro" -v ~/src/embit:/embit:ro bip375-mixed \
#     bash -s < images/linux/check.sh
set -euo pipefail
rm -rf /work && mkdir /work && (cd /repo && tar --exclude=./artifacts --exclude=__pycache__ -cf - .) | tar -C /work -xf -
python3 -m venv /opt/h && /opt/h/bin/pip install -q pyyaml cbor2 pyserial && cp -r /embit /tmp/embit && /opt/h/bin/pip install -q /tmp/embit
mkdir -p /cfg /out && cat > /cfg/interop.yaml <<YAML
artifact_root: /out/artifacts
checkouts:
  coldcard: {path: /cc}
  jade: {path: /jade}
YAML
printf 'checkouts:\n  coldcard: d210635d04606354e08e492f53908fe230d75462\n  jade: 3cf19c81ee6137a77dd4e0d880c3a4b1348810cf\n' > /cfg/interop.lock
# Not on PATH: the Jade worker must find qemu-xtensa under IDF_TOOLS_PATH.
unset BIP375_JADE_QEMU
cd /work
h() { PYTHONPATH=/work/src /opt/h/bin/python -m bip375_interop.cli --config /cfg/interop.yaml "$@"; }
echo "== doctor"; h doctor | /opt/h/bin/python -c 'import json,sys; [print(s["name"], s["revision"][:8], "dirty" if s["dirty"] else "clean", s["byproducts"]) for s in json.load(sys.stdin)]'
for s in bip375-coldcard-jade-two-way bip375-coldcard-jade-two-way-taproot; do
  echo "== run-generated $s"; h run-generated scenarios/$s.yaml
done
