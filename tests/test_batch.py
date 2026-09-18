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


def test_batch_report_carries_labels_when_given(tmp_path: Path):
    batch = BatchRun(tmp_path, "jade")
    batch.add(CaseResult("fails", "failed", "signature missing"))

    manifest, report = batch.finalize({"fails": "REGRESSION"})

    payload = json.loads(manifest.read_text())
    assert payload["results"][0]["label"] == "REGRESSION"
    assert payload["label_counts"] == {"REGRESSION": 1}
    assert "REGRESSION" in report.read_text()


def test_batch_report_omits_labels_by_default(tmp_path: Path):
    batch = BatchRun(tmp_path, "jade")
    batch.add(CaseResult("passes", "passed"))

    manifest, _ = batch.finalize()

    payload = json.loads(manifest.read_text())
    assert "label" not in payload["results"][0]
    assert "label_counts" not in payload
