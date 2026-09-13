"""GDC presence checks. Schema validation stays with GDC ETS / pywcmp."""

from __future__ import annotations

from ..gdc import GdcSnapshot
from .results import (
    FAIL,
    INTEROPERABILITY,
    NOT_OBSERVED,
    PASS,
    SKIP,
    TestResult,
    make_result,
)


def run_gdc_tests(
    *,
    centre_id: str,
    snapshot: GdcSnapshot | None,
    skip_gdc: bool,
    ets_ok: bool,
    ets_count: int,
    monitor_errors: int,
) -> list[TestResult]:
    if skip_gdc:
        exists = make_result(
            "GDC-001",
            "WCMP2 / Global Discovery Catalogue",
            "Metadata exists",
            INTEROPERABILITY,
            SKIP,
            received="GDC query skipped",
        )
    elif snapshot is None:
        exists = make_result(
            "GDC-001",
            "WCMP2 / Global Discovery Catalogue",
            "Metadata exists",
            INTEROPERABILITY,
            NOT_OBSERVED,
            expected=f'GDC items?q="{centre_id}" returns WCMP2 records',
            received="GDC has not been queried yet",
            recommendation="Press g in the console, or wait for the automatic GDC refresh.",
        )
    elif snapshot.ok and snapshot.records:
        exists = make_result(
            "GDC-001",
            "WCMP2 / Global Discovery Catalogue",
            "Metadata exists",
            INTEROPERABILITY,
            PASS,
            expected=f'WCMP2 records for {centre_id}',
            received=f"{len(snapshot.records)} record(s) from {snapshot.url}",
            evidence={
                "url": snapshot.url,
                "identifiers": [item.identifier for item in snapshot.records[:12]],
            },
        )
    else:
        exists = make_result(
            "GDC-001",
            "WCMP2 / Global Discovery Catalogue",
            "Metadata exists",
            INTEROPERABILITY,
            FAIL if snapshot.error and "unreachable" in snapshot.error.lower() else NOT_OBSERVED,
            expected=f'WCMP2 records for {centre_id}',
            received=snapshot.error or "no matching records",
            evidence={"url": snapshot.url},
            recommendation=(
                "Publish valid WCMP2 on origin/…/metadata and allow about 30 minutes "
                "for GDC ingest from cache/…/metadata."
            ),
        )

    if ets_count == 0:
        ets = make_result(
            "GDC-002",
            "WCMP2 ETS (GDC Evaluation/Testing Suite)",
            "GDC ETS reports",
            INTEROPERABILITY,
            NOT_OBSERVED,
            expected="monitor ETS reports with FAILED=0",
            received="no ETS reports yet",
            recommendation=(
                "ETS is produced by the GDC after it processes cached metadata. "
                "This tool does not replace wis2-gdc or pywcmp."
            ),
        )
    elif ets_ok:
        ets = make_result(
            "GDC-002",
            "WCMP2 ETS (GDC Evaluation/Testing Suite)",
            "GDC ETS reports",
            INTEROPERABILITY,
            PASS,
            expected="monitor ETS reports with FAILED=0",
            received=f"{ets_count} ETS report(s), all passed",
        )
    else:
        ets = make_result(
            "GDC-002",
            "WCMP2 ETS (GDC Evaluation/Testing Suite)",
            "GDC ETS reports",
            INTEROPERABILITY,
            FAIL,
            expected="monitor ETS reports with FAILED=0",
            received=f"{ets_count} ETS report(s) with failures; monitor errors={monitor_errors}",
            recommendation="Open the ETS table in the report and correct the WCMP2 record.",
        )
    return [exists, ets]
