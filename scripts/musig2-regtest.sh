#!/usr/bin/env bash
# One MuSig2-SP leg end to end on a throwaway regtest node: treasury wallet, silent-pay
# initial PSBT, the device scenario, finalize, broadcast, and an on-chain BIP-352 scan.
#
# usage: scripts/musig2-regtest.sh [aggregate-then-derive|derive-then-aggregate]
# env:   BITCOIND, BITCOIN_CLI   binaries (default: on PATH)
#        SILENT_PAY              silent-pay checkout (default: from interop.yaml)
#        RPC_PORT                regtest RPC port (default 18999)
#        ALLOW_DIRTY=1           pass --allow-dirty to the harness
#        WORK_DIR                keep scratch files here (default: a new temp dir)
#        INITIAL_ONLY=1          store the initial PSBT as scenarios/<scenario>.psbt and stop
set -euo pipefail

ARCH=${1:-aggregate-then-derive}
case "$ARCH" in
  aggregate-then-derive) SCENARIO=musig2-sp-coldcard-jade-two-way ;;
  derive-then-aggregate) SCENARIO=musig2-sp-jade-derive-first-two-way ;;
  *) echo "unknown key architecture: $ARCH (aggregate-then-derive or derive-then-aggregate)" >&2; exit 2 ;;
esac

ROOT=$(cd "$(dirname "$0")/.." && pwd)
BITCOIND=${BITCOIND:-bitcoind}
BITCOIN_CLI=${BITCOIN_CLI:-bitcoin-cli}
RPC_PORT=${RPC_PORT:-18999}
WORK=${WORK_DIR:-$(mktemp -d)}
DATADIR="$WORK/node"
COOKIE="$DATADIR/regtest/.cookie"
RPC=(--rpc-url "http://127.0.0.1:$RPC_PORT" --rpc-cookie "$COOKIE")

for bin in "$BITCOIND" "$BITCOIN_CLI"; do
  command -v "$bin" >/dev/null || { echo "$bin not found; set BITCOIND / BITCOIN_CLI" >&2; exit 2; }
done

SILENT_PAY=${SILENT_PAY:-$(python3 - "$ROOT/interop.yaml" <<'PY'
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))["checkouts"]["silent-pay"]["path"])
PY
)}

harness() {
  PYTHONPATH="$ROOT/src" python3 -m bip375_interop.cli ${ALLOW_DIRTY:+--allow-dirty} "$@"
}
sp_demo() {
  local bin=$1; shift
  (cd "$SILENT_PAY" && cargo run -q -p sp-demo --bin "$bin" -- "$@")
}

cleanup() {
  "$BITCOIN_CLI" -regtest -datadir="$DATADIR" -rpcport="$RPC_PORT" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "$DATADIR"
echo "scratch: $WORK"
"$BITCOIND" -regtest -datadir="$DATADIR" -daemon -fallbackfee=0.0001 -rpcport="$RPC_PORT" >/dev/null
for _ in $(seq 60); do
  "$BITCOIN_CLI" -regtest -datadir="$DATADIR" -rpcport="$RPC_PORT" getblockchaininfo >/dev/null 2>&1 && break
  sleep 1
done

echo "== treasury wallet ($ARCH)"
harness treasury-wallet test-a test-b --network regtest --key-architecture "$ARCH" --out "$WORK/wallet.toml"

# The recipient is the first demo recipient, re-encoded with regtest's sprt HRP (embit only
# knows sp/tsp). build_round1 takes the production schema; verify_onchain also needs the
# recipient's scan key, so it gets a second file.
cat > "$WORK/recipients.toml" <<'TOML'
[[recipients]]
label = "recipient-1"
amount_sat = 180000
address = "sprt1qqvaathesys45acsp5t0rwnquu2jtgamj0gawjlnxe9ez9pencv7kyqhvzu65efrhy97dnny8n4qsvczg5fvlsdke993m0yplnwsyced22v6kmyr8"
TOML
{ cat "$WORK/recipients.toml"; echo 'scan_key_hex = "998f86a68e7382cab144db4ebbdd93f6c39fb0ec800a6368f7b521312e56c0dc"'; } \
  > "$WORK/recipients-scan.toml"

echo "== initial PSBT (silent-pay)"
sp_demo build_round1 --wallet "$WORK/wallet.toml" --recipients "$WORK/recipients.toml" \
  --out-dir "$WORK/initial" "${RPC[@]}"

if [ -n "${INITIAL_ONLY:-}" ]; then
  cp "$WORK/initial/initial.psbt" "$ROOT/scenarios/$SCENARIO.psbt"
  echo "WROTE scenarios/$SCENARIO.psbt"
  exit 0
fi

echo "== scenario $SCENARIO"
harness run "scenarios/$SCENARIO.yaml" --psbt "$WORK/initial/initial.psbt" > "$WORK/run.json"
FINAL=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["final_psbt"])' "$WORK/run.json")
OUT=$(dirname "$FINAL")

echo "== finalize"
sp_demo finalize "$FINAL" | tee "$WORK/finalize.out"
TXID=$(awk '/^txid:/{print $2}' "$WORK/finalize.out")
[ -n "$TXID" ] || { echo "finalize produced no txid" >&2; exit 1; }

echo "== broadcast and confirm"
sp_demo broadcast_final --wallet "$WORK/wallet.toml" --tx-hex-file "$OUT/musig2-sp-final-hex.txt" "${RPC[@]}"

echo "== on-chain scan"
sp_demo verify_onchain "$OUT/musig2-sp-final.psbt" --recipients "$WORK/recipients-scan.toml" --txid "$TXID" "${RPC[@]}"

echo "PASS $SCENARIO $ARCH txid=$TXID artifacts=$OUT"
