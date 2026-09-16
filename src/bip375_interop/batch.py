"""Durable batch result records and a small self-contained HTML report."""

from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
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

    def finalize(self) -> tuple[Path, Path]:
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
        manifest = self.path / "report.json"
        manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        rows = "\n".join(_html_row(item) for item in self.results)
        report = self.path / "report.html"
        score = "N/A" if payload["score"] is None else f"{payload['score']}%"
        report.write_text(
            "<!doctype html><title>BIP375 Interop regression report</title>"
            f"<h1>Regression health: {score}</h1>"
            f"<p>{counts['passed']}/{required} structurally verified cases passed; "
            f"{counts['completed']} completed but need verification; {counts['blocked']} blocked.</p>"
            "<table><tr><th>Case</th><th>Status</th><th>Reason</th><th>Artifact</th></tr>"
            + rows + "</table>\n"
        )
        return manifest, report


def _html_row(result: CaseResult) -> str:
    artifact = ""
    if result.artifact:
        path = Path(result.artifact).resolve()
        artifact = f'<a href="{html.escape(path.as_uri())}">manifest</a>'
    return (
        f"<tr><td>{html.escape(result.name)}</td>"
        f"<td>{html.escape(result.status)}</td>"
        f"<td>{html.escape(result.reason or '')}</td><td>{artifact}</td></tr>"
    )
