"""Classify a batch run against expectations and the previous run of the same project."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from .batch import CaseResult
from .expectations import Expectation

STEADY = "STEADY"
PASSING = {"passed", "completed"}
# Labels that fail a run. NOT-RUN, CHANGED and FIXED are reported but do not fail it.
FAILING_LABELS = {"REGRESSION", "UNCLASSIFIED", "NEW"}


def classify(
    result: CaseResult, expectation: Expectation | None, previous: Mapping[str, str | None] | None
) -> str:
    """One label per case: REGRESSION, FIXED, CHANGED, STEADY, NOT-RUN, NEW or UNCLASSIFIED.

    CHANGED means the outcome matches its expectation class but differs from the
    previous run's status or reason.
    """

    if expectation is None:
        return "NEW"
    if expectation.status == "unclassified":
        return "UNCLASSIFIED"
    if expectation.status == "supported":
        if result.status == "failed":
            return "REGRESSION"
        if result.status == "blocked":
            return "NOT-RUN"
    elif result.status in PASSING:
        return "FIXED"
    if previous is not None and (previous["status"], previous["reason"]) != (result.status, result.reason):
        return "CHANGED"
    return STEADY


def label_results(
    results: Sequence[CaseResult],
    expectations: Mapping[str, Expectation],
    previous: Mapping[str, Mapping[str, str | None]] | None,
) -> dict[str, str]:
    return {
        result.name: classify(
            result, expectations.get(result.name), None if previous is None else previous.get(result.name)
        )
        for result in results
    }


def expected_counts(results: Sequence[CaseResult], labels: Mapping[str, str]) -> dict[str, int]:
    """Failed and blocked cases whose outcome matches their expectation (STEADY or CHANGED)."""

    return {
        status: sum(
            result.status == status and labels[result.name] in (STEADY, "CHANGED")
            for result in results
        )
        for status in ("failed", "blocked")
    }


def variance_records(
    results: Sequence[CaseResult],
    labels: Mapping[str, str],
    expectations: Mapping[str, Expectation],
    previous: Mapping[str, Mapping[str, str | None]] | None,
) -> list[dict]:
    """Non-STEADY cases, with the fields that explain the label.

    ``expectation`` is null when the case has none (``NEW``). ``previous`` is
    null when no earlier batch of this project recorded the scenario.
    """

    records = []
    for result in results:
        label = labels[result.name]
        if label == STEADY:
            continue
        expectation = expectations.get(result.name)
        prior = None if previous is None else previous.get(result.name)
        records.append({
            "scenario": result.name,
            "label": label,
            "status": result.status,
            "reason": result.reason,
            "expectation": (
                None if expectation is None
                else {"status": expectation.status, "reason": expectation.reason}
            ),
            "previous": None if prior is None else {"status": prior["status"], "reason": prior["reason"]},
        })
    return records


def previous_results(
    artifact_root: Path, project: str, current: Path
) -> dict[str, dict[str, str | None]] | None:
    """Results of the newest finalized batch for this project that precedes `current`."""

    batches = artifact_root / "batches"
    if not batches.is_dir():
        return None
    earlier = sorted(
        path for path in batches.iterdir()
        # Batch names are <stamp>-<project>-<id>; a project name may contain "-".
        if path.name.split("-", 1)[-1].rsplit("-", 1)[0] == project
        and path.name < current.name and (path / "report.json").is_file()
    )
    if not earlier:
        return None
    payload = json.loads((earlier[-1] / "report.json").read_text())
    return {
        item["name"]: {"status": item["status"], "reason": item.get("reason")}
        for item in payload["results"]
    }


def has_failures(labels: Mapping[str, str]) -> bool:
    return any(label in FAILING_LABELS for label in labels.values())
