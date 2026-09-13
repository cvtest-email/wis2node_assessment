"""Named test results, classifications, and overall verdict."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


PASS = "PASS"
FAIL = "FAIL"
WARNING = "WARNING"
SKIP = "SKIP"
NOT_OBSERVED = "NOT_OBSERVED"

CONFORMANCE = "CONFORMANCE"
INTEROPERABILITY = "INTEROPERABILITY"
PERFORMANCE = "PERFORMANCE"
QUALITY = "QUALITY"
OPERATIONAL = "OPERATIONAL"

OVERALL_PASS = "PASS"
OVERALL_WARN = "PASS WITH WARNINGS"
OVERALL_FAIL = "FAIL"
OVERALL_INCOMPLETE = "INCOMPLETE"

GROUP_ORDER = ("WTH", "WNM", "WCMP2", "MQTT", "HTTP", "GDC")

# Official GISC scenario items that must be observed before approval.
REQUIRED_OBSERVED = frozenset(
    {
        "WTH-003",
        "WTH-004",
        "WTH-005",
        "GDC-001",
    }
)


@dataclass
class TestResult:
    test_id: str
    requirement: str
    title: str
    classification: str
    result: str
    timestamp: str = ""
    expected: str = ""
    received: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @property
    def group(self) -> str:
        return self.test_id.split("-", 1)[0]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_result(
    test_id: str,
    requirement: str,
    title: str,
    classification: str,
    result: str,
    *,
    expected: str = "",
    received: str = "",
    evidence: dict[str, Any] | None = None,
    recommendation: str = "",
) -> TestResult:
    return TestResult(
        test_id=test_id,
        requirement=requirement,
        title=title,
        classification=classification,
        result=result,
        expected=expected,
        received=received,
        evidence=evidence or {},
        recommendation=recommendation,
    )


@dataclass
class EngineReport:
    results: list[TestResult] = field(default_factory=list)
    generated_at: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def by_group(self) -> dict[str, list[TestResult]]:
        grouped: dict[str, list[TestResult]] = {}
        for item in self.results:
            grouped.setdefault(item.group, []).append(item)
        return grouped

    def counts(self) -> dict[str, int]:
        totals = {PASS: 0, FAIL: 0, WARNING: 0, SKIP: 0, NOT_OBSERVED: 0}
        for item in self.results:
            totals[item.result] = totals.get(item.result, 0) + 1
        totals["total"] = len(self.results)
        return totals

    def group_status(self, items: list[TestResult]) -> str:
        if any(item.result == FAIL for item in items):
            return FAIL
        if any(item.result == NOT_OBSERVED for item in items):
            return NOT_OBSERVED
        if any(item.result == WARNING for item in items):
            return WARNING
        if items and all(item.result == SKIP for item in items):
            return SKIP
        return PASS

    def matrix_rows(self) -> list[list[str]]:
        rows: list[list[str]] = []
        grouped = self.by_group()
        seen: set[str] = set()
        for name in GROUP_ORDER:
            items = grouped.get(name)
            if not items:
                continue
            seen.add(name)
            rows.append(self._matrix_row(name, items))
        for name, items in grouped.items():
            if name not in seen:
                rows.append(self._matrix_row(name, items))
        return rows

    def _matrix_row(self, name: str, items: list[TestResult]) -> list[str]:
        passed = sum(1 for item in items if item.result == PASS)
        failed = sum(1 for item in items if item.result == FAIL)
        return [
            name,
            str(len(items)),
            str(passed),
            str(failed),
            self.group_status(items),
        ]

    def overall(self) -> str:
        blocking = [
            item
            for item in self.results
            if item.classification in {CONFORMANCE, INTEROPERABILITY}
            and item.result == FAIL
        ]
        if blocking:
            return OVERALL_FAIL
        missing = [
            item
            for item in self.results
            if item.test_id in REQUIRED_OBSERVED and item.result == NOT_OBSERVED
        ]
        if missing:
            return OVERALL_INCOMPLETE
        if any(item.result == WARNING for item in self.results):
            return OVERALL_WARN
        if any(
            item.result == NOT_OBSERVED
            for item in self.results
            if item.classification in {CONFORMANCE, INTEROPERABILITY}
        ):
            return OVERALL_INCOMPLETE
        return OVERALL_PASS

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts()
        return {
            "generated_at": self.generated_at,
            "overall": self.overall(),
            "counts": counts,
            "matrix": [
                {
                    "group": row[0],
                    "tests": int(row[1]),
                    "passed": int(row[2]),
                    "failed": int(row[3]),
                    "status": row[4],
                }
                for row in self.matrix_rows()
            ],
            "results": [item.to_dict() for item in self.results],
        }
