"""Durable batch result records and a small self-contained HTML report."""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from uuid import uuid4


@dataclass(frozen=True)
class CaseResult:
    name: str
    status: str
    reason: str | None = None
    artifact: str | None = None


class BatchRun:
    def __init__(self, root: Path, project: str):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.path = root / "batches" / f"{stamp}-{project}-{uuid4().hex[:8]}"
        self.path.mkdir(parents=True, exist_ok=False)
        self.results: list[CaseResult] = []

    def add(self, result: CaseResult) -> None:
        self.results.append(result)

    def finalize(self, labels: Mapping[str, str] | None = None) -> tuple[Path, Path]:
        counts = {
            status: sum(item.status == status for item in self.results)
            for status in ("passed", "failed", "blocked", "completed")
        }
        required = counts["passed"] + counts["failed"]
        payload = {
            "schema_version": 1,
            "counts": counts,
            "required": required,
            "score": None if required == 0 else round(100 * counts["passed"] / required),
            "results": [asdict(item) for item in self.results],
        }
        if labels is not None:
            for row in payload["results"]:
                row["label"] = labels[row["name"]]
            payload["label_counts"] = {
                label: sum(value == label for value in labels.values())
                for label in sorted(set(labels.values()))
            }
        manifest = self.path / "report.json"
        manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        rows = "\n".join(_html_row(item, None if labels is None else labels[item.name]) for item in self.results)
        report = self.path / "report.html"
        headline = "Coverage" if labels is None else _label_headline(labels)
        report.write_text(
            "<!doctype html><title>BIP375 Interop regression report</title>"
            f"<h1>{html.escape(headline)}</h1>"
            f"<p>{html.escape(_coverage_sentence(counts))}</p>"
            "<table><tr><th>Case</th><th>Status</th><th>Label</th><th>Reason</th><th>Artifact</th></tr>"
            + rows + "</table>\n"
        )
        return manifest, report


def _label_headline(labels: Mapping[str, str]) -> str:
    """Same verdict ``check`` prints in ``labels``, as ``CHANGED 1, STEADY 24``."""

    if not labels:
        return "no cases"
    counts: dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{label} {counts[label]}" for label in sorted(counts))


def _coverage_sentence(counts: Mapping[str, int]) -> str:
    return (
        f"{counts['passed']} passed with a validator, "
        f"{counts['completed']} completed without one, "
        f"{counts['failed']} failed, {counts['blocked']} blocked."
    )


def _html_row(result: CaseResult, label: str | None) -> str:
    artifact = ""
    if result.artifact:
        path = Path(result.artifact).resolve()
        artifact = f'<a href="{html.escape(path.as_uri())}">manifest</a>'
    return (
        f"<tr><td>{html.escape(result.name)}</td>"
        f"<td>{html.escape(result.status)}</td><td>{html.escape(label or '')}</td>"
        f"<td>{html.escape(result.reason or '')}</td><td>{artifact}</td></tr>"
    )
