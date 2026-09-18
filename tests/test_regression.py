import json
from pathlib import Path

import pytest

from bip375_interop.batch import BatchRun, CaseResult
from bip375_interop.expectations import Expectation
from bip375_interop.regression import classify, label_results, previous_results


def _exp(status: str) -> Expectation:
    return Expectation(status, "reason", None)


@pytest.mark.parametrize("outcome, expected, label", [
    (CaseResult("c", "passed"), "supported", "STEADY"),
    (CaseResult("c", "completed"), "supported", "STEADY"),
    (CaseResult("c", "failed", "boom"), "supported", "REGRESSION"),
    (CaseResult("c", "passed"), "finding", "FIXED"),
    (CaseResult("c", "passed"), "unsupported", "FIXED"),
    (CaseResult("c", "failed", "boom"), "finding", "STEADY"),
    (CaseResult("c", "failed", "boom"), "unsupported", "STEADY"),
    (CaseResult("c", "failed", "boom"), "unclassified", "UNCLASSIFIED"),
    (CaseResult("c", "passed"), "unclassified", "UNCLASSIFIED"),
    (CaseResult("c", "blocked", "needs psbt"), "needs-external-psbt", "STEADY"),
    (CaseResult("c", "blocked", "needs psbt"), "supported", "NOT-RUN"),
])
def test_classify_against_expectation(outcome: CaseResult, expected: str, label: str):
    assert classify(outcome, _exp(expected), None) == label


def test_classify_flags_a_scenario_with_no_expectation_as_new():
    assert classify(CaseResult("c", "passed"), None, None) == "NEW"


def test_classify_flags_a_changed_failure_reason_against_the_previous_run():
    now = CaseResult("c", "failed", "second reason")
    previous = {"status": "failed", "reason": "first reason"}

    assert classify(now, _exp("finding"), previous) == "CHANGED"
    assert classify(now, _exp("finding"), {"status": "failed", "reason": "second reason"}) == "STEADY"


def test_regression_outranks_changed():
    now = CaseResult("c", "failed", "new reason")

    assert classify(now, _exp("supported"), {"status": "failed", "reason": "old"}) == "REGRESSION"


def test_label_results_maps_each_case_and_uses_previous_by_name():
    results = [CaseResult("a", "failed", "x"), CaseResult("b", "passed")]
    previous = {"a": {"status": "failed", "reason": "old"}}

    labels = label_results(results, {"a": _exp("finding"), "b": _exp("supported")}, previous)

    assert labels == {"a": "CHANGED", "b": "STEADY"}


def test_previous_results_picks_the_newest_earlier_batch_for_the_same_project(tmp_path: Path):
    def make(project: str, cases: list[CaseResult]) -> BatchRun:
        batch = BatchRun(tmp_path, project)
        for case in cases:
            batch.add(case)
        batch.finalize()
        return batch

    make("jade", [CaseResult("a", "failed", "jade only")])
    make("harness", [CaseResult("a", "passed")])
    newest_earlier = make("harness", [CaseResult("a", "failed", "second")])
    current = BatchRun(tmp_path, "harness")

    previous = previous_results(tmp_path, "harness", current.path)

    assert previous == {"a": {"status": "failed", "reason": "second"}}
    assert newest_earlier.path != current.path


def test_previous_results_is_none_without_an_earlier_batch(tmp_path: Path):
    current = BatchRun(tmp_path, "harness")

    assert previous_results(tmp_path, "harness", current.path) is None


def test_previous_results_ignores_batches_that_never_finalized(tmp_path: Path):
    BatchRun(tmp_path, "harness")  # crashed before writing report.json
    current = BatchRun(tmp_path, "harness")

    assert previous_results(tmp_path, "harness", current.path) is None
