import json

from bip375_interop.batch import BatchRun, CaseResult


def test_batch_score_excludes_blocked_cases_from_denominator(tmp_path: Path):
    batch = BatchRun(tmp_path, "jade")
    batch.add(CaseResult("passes", "passed"))
    batch.add(CaseResult("fails", "failed", "signature missing"))
    batch.add(CaseResult("external", "blocked", "requires PSBT"))

    manifest, report = batch.finalize()

    payload = json.loads(manifest.read_text())
    assert payload["required"] == 2
    assert payload["score"] == 50
    assert payload["counts"] == {
        "blocked": 1, "completed": 0, "failed": 1, "passed": 1,
    }
    assert "1/2 structurally verified cases passed" in report.read_text()
