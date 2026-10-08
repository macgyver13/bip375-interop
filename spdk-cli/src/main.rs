//! Validate BIP-375 PSBT snapshots by finalizing, cryptographically
//! verifying, and extracting each one.
//!
//! `finalize()` only assembles final_script_witness/final_script_sig from
//! whatever signature bytes are present -- it does not check they are
//! valid. `interpreter_check` is the step that actually runs rust-psbt's
//! miniscript interpreter (Schnorr/ECDSA verification against the real
//! sighash) against the finalized result. `extract_tx` is spdk's BIP-375
//! Transaction Extractor: it recomputes every silent payment output script
//! from the ECDH shares and DLEQ proofs, compares it with the PSBT, and then
//! extracts the transaction. A MuSig2 PSBT is the exception, as in spdk: the
//! Finalizer clears the participant shares that derivation needs, so it is
//! extracted with rust-psbt's `Extractor` directly. No embit involved
//! anywhere in this path.
//!
//! Note: an input that already carries PSBT_IN_FINAL_SCRIPTWITNESS (0x08)
//! is treated as already finalized, and `finalize()` passes that field
//! through unchanged rather than rebuilding it from the raw signature
//! field -- interpreter_check still verifies whatever ends up in the
//! final witness either way, so this doesn't weaken the check.
//!
//! usage: spdk-cli <psbt>...
//! Prints one JSON line per file: {"file", "ok", "error"}.

use std::fs;

use psbt::extractor::SpExtractorExt;
use psbt_v2::{Extractor, Finalizer, Psbt};
use secp256k1::Secp256k1;

fn validate_one(path: &str) -> Result<(), String> {
    // Output derivation multiplies points, so this context also signs.
    let secp = Secp256k1::new();
    let bytes = fs::read(path).map_err(|err| format!("read: {err}"))?;
    let psbt = Psbt::deserialize(&bytes).map_err(|err| format!("deserialize: {err}"))?;
    let musig2 = is_musig2(&psbt);
    let finalized = Finalizer::new(psbt)
        .map_err(|err| format!("finalize: {err}"))?
        .finalize(&secp)
        .map_err(|err| format!("finalize: {err}"))?;
    finalized
        .interpreter_check(&secp)
        .map_err(|err| format!("interpreter_check: {err}"))?;
    if musig2 {
        Extractor::new(finalized)
            .map_err(|err| format!("extract_tx: {err}"))?
            .extract_tx()
            .map_err(|err| format!("extract_tx: {err}"))?;
    } else {
        finalized
            .extract_tx(&secp)
            .map_err(|err| format!("extract_tx: {err}"))?;
    }
    Ok(())
}

/// PSBT_IN_MUSIG2_PARTICIPANT_PUBKEYS (BIP-373), which this rust-psbt keeps
/// among the unknown fields.
fn is_musig2(psbt: &Psbt) -> bool {
    psbt.inputs
        .iter()
        .any(|input| input.unknowns.keys().any(|key| key.type_value == 0x1a))
}

fn main() {
    // Exit 0 regardless of per-file outcome, matching caravan_validate.cjs's
    // contract: a rejected PSBT is reported through the "ok" field, not the
    // process exit code, so callers can distinguish "the validator crashed"
    // from "the validator ran and rejected a PSBT".
    for path in std::env::args().skip(1) {
        let error = validate_one(&path).err();
        println!(
            "{}",
            serde_json::json!({"file": path, "ok": error.is_none(), "error": error})
        );
    }
}
