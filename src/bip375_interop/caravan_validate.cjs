// Parse PSBT snapshots with Caravan's PsbtV2, whose constructor runs its full
// BIP-375 validation (field shapes, DLEQ proofs, output script derivation).
//
// usage: node caravan_validate.cjs <caravan-psbt dist/index.js> <psbt>...
// Prints one JSON line per file: {"file", "ok", "error"}.

const fs = require("fs");

const [distPath, ...files] = process.argv.slice(2);
const { PsbtV2 } = require(distPath);

for (const file of files) {
  let error = null;
  try {
    new PsbtV2(fs.readFileSync(file));
  } catch (exc) {
    error = String(exc && exc.message ? exc.message : exc);
  }
  process.stdout.write(JSON.stringify({ file, ok: error === null, error }) + "\n");
}
