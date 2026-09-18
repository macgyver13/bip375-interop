from pathlib import Path

import pytest

from bip375_interop.catalog import discover
from bip375_interop.errors import ConfigurationError
from bip375_interop.expectations import Expectation, load_expectations, require_coverage

ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "expectations.yaml"
    path.write_text(body)
    return path


def test_load_expectations_reads_status_reason_and_reference(tmp_path: Path):
    path = _write(tmp_path, """
scenarios:
  case-a: {status: supported, reason: passes on the pinned revisions}
  case-b: {status: finding, reason: device accepts it, reference: docs/runbook.md}
""")

    expectations = load_expectations(path)

    assert expectations["case-a"] == Expectation("supported", "passes on the pinned revisions", None)
    assert expectations["case-b"].reference == "docs/runbook.md"


@pytest.mark.parametrize("body, message", [
    ("scenarios: {a: {status: maybe, reason: x}}", "status"),
    ("scenarios: {a: {status: supported}}", "reason"),
    ("scenarios: {a: {status: supported, reason: ''}}", "reason"),
    ("scenarios: [a]", "scenarios"),
    ("other: {}", "scenarios"),
])
def test_load_expectations_rejects_invalid_entries(tmp_path: Path, body: str, message: str):
    with pytest.raises(ConfigurationError, match=message):
        load_expectations(_write(tmp_path, body))


def test_require_coverage_reports_unknown_and_missing_scenarios():
    expectations = {
        "known": Expectation("supported", "ok", None),
        "stale": Expectation("supported", "ok", None),
    }

    with pytest.raises(ConfigurationError) as exc:
        require_coverage(expectations, ["known", "new"])

    assert "missing: new" in str(exc.value)
    assert "unknown: stale" in str(exc.value)


def test_require_coverage_accepts_exact_match():
    require_coverage({"a": Expectation("supported", "ok", None)}, ["a"])


def test_repository_expectations_cover_every_scenario():
    expectations = load_expectations(ROOT / "expectations.yaml")
    names = [entry.scenario.name for entry in discover(ROOT / "scenarios")]

    require_coverage(expectations, names)
