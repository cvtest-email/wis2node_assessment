"""Offline checks for the Phase 1 engine. Run: python3 -m wis2_assessment.engine.selfcheck"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import SessionConfig
from ..messages import parse_mqtt_message
from ..store import MessageStore
from .runner import run_engine
from .wnm import run_wnm_tests
from .wth import data_topic_ok, metadata_topic_ok, valid_centre_id


VALID_WNM = {
    "id": "31e9d66a-cd83-4174-9429-b932f1abe1be",
    "conformsTo": ["http://wis.wmo.int/spec/wnm/1/conf/core"],
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [147.18, -9.44]},
    "properties": {
        "pubtime": "2026-09-14T00:17:32Z",
        "datetime": "2026-09-14T00:15:00Z",
        "data_id": "wis2/pg-pngnws/data/core/weather/surface-based-observations/synop/demo",
        "metadata_id": "urn:wmo:md:pg-pngnws:synop-3hourly",
        "integrity": {
            "method": "sha256",
            "value": "dGhpc2lzbm90YXJlYWxkaWdlc3RpdGlzZGVtbw==",
        },
        "cache": True,
    },
    "links": [
        {
            "href": "https://example.invalid/data/demo.bufr",
            "rel": "canonical",
            "type": "application/bufr",
        }
    ],
}


def _msg(topic: str, body: dict, channel: str = "origin"):
    return parse_mqtt_message(
        channel,
        topic,
        json.dumps(body),
        received_at=datetime.now(timezone.utc),
    )


def main() -> int:
    errors: list[str] = []
    if not valid_centre_id("pg-pngnws"):
        errors.append("valid_centre_id rejected pg-pngnws")
    if valid_centre_id("pngnws"):
        errors.append("valid_centre_id accepted pngnws")
    if not metadata_topic_ok("origin/a/wis2/pg-pngnws/metadata", "origin", "pg-pngnws"):
        errors.append("metadata topic rejected")
    ok, reason = data_topic_ok(
        "origin/a/wis2/pg-pngnws/data/core/weather/surface-based-observations/synop",
        "origin",
        "pg-pngnws",
    )
    if not ok:
        errors.append(f"valid data topic failed: {reason}")
    ok, _ = data_topic_ok("origin/a/wis2/pg-pngnws/data/core/weather", "origin", "pg-pngnws")
    if ok:
        errors.append("short data topic should fail (need level 8)")

    good = _msg(
        "origin/a/wis2/pg-pngnws/data/core/weather/surface-based-observations/synop",
        VALID_WNM,
    )
    wnm = {item.test_id: item.result for item in run_wnm_tests([good])}
    for test_id, result in wnm.items():
        if result != "PASS":
            errors.append(f"{test_id} on valid WNM: {result}")

    bad = dict(VALID_WNM)
    bad["id"] = "not-a-uuid"
    bad_msg = _msg(
        "origin/a/wis2/pg-pngnws/data/core/weather/surface-based-observations/synop",
        bad,
    )
    wnm_bad = {item.test_id: item.result for item in run_wnm_tests([bad_msg])}
    if wnm_bad.get("WNM-004") != "FAIL":
        errors.append(f"WNM-004 should FAIL on bad id, got {wnm_bad.get('WNM-004')}")

    store = MessageStore()
    store.add(good)
    store.add(
        _msg(
            "origin/a/wis2/pg-pngnws/metadata",
            VALID_WNM,
        )
    )
    config = SessionConfig(
        centre_id="pg-pngnws",
        origin_topic="origin/a/wis2/pg-pngnws/#",
        cache_topic="cache/a/wis2/pg-pngnws/#",
        monitor_topic="monitor/a/wis2/pg-pngnws",
        skip_gdc=True,
        skip_http=True,
        gisc="GISC Melbourne",
    )
    config.apply_centre_defaults()
    report = run_engine(store, config, probe_http=False, live_session=False)
    ids = {item.test_id for item in report.results}
    for required in (
        "WTH-001",
        "WTH-004",
        "WNM-001",
        "MQTT-001",
        "GDC-001",
        "HTTP-001",
    ):
        if required not in ids:
            errors.append(f"missing {required} in engine report")
    if report.overall() not in {"INCOMPLETE", "PASS WITH WARNINGS", "PASS", "FAIL"}:
        errors.append(f"unexpected overall {report.overall()}")

    if errors:
        print("selfcheck FAILED")
        for item in errors:
            print(f"  - {item}")
        return 1
    print("selfcheck OK")
    print(f"  tests={len(report.results)} overall={report.overall()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
