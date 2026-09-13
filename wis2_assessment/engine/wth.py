"""WTH — WIS2 Topic Hierarchy checks on configured and observed topics."""

from __future__ import annotations

import re

from ..messages import ParsedMessage
from .results import (
    CONFORMANCE,
    FAIL,
    INTEROPERABILITY,
    NOT_OBSERVED,
    PASS,
    TestResult,
    make_result,
)

CHANNELS = {"origin", "cache"}
VERSIONS = {"a"}
SYSTEMS = {"wis2"}
NOTIFICATION_TYPES = {"data", "metadata"}
DATA_POLICIES = {"core", "recommended"}
DISCIPLINES = {
    "atmospheric-composition",
    "climate",
    "cryosphere",
    "hydrology",
    "ocean",
    "space-weather",
    "weather",
}
CENTRE_RE = re.compile(r"^[a-z]{2,}[a-z0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_topic(topic: str) -> dict[str, str | list[str]]:
    parts = [p for p in (topic or "").strip("/").split("/") if p]
    parsed: dict[str, str | list[str]] = {"parts": parts, "raw": topic or ""}
    if not parts:
        return parsed
    parsed["channel"] = parts[0]
    if len(parts) >= 2:
        parsed["version"] = parts[1]
    if len(parts) >= 3:
        parsed["system"] = parts[2]
    if len(parts) >= 4:
        parsed["centre_id"] = parts[3]
    if len(parts) >= 5:
        parsed["notification_type"] = parts[4]
    if len(parts) >= 6:
        parsed["data_policy"] = parts[5]
    if len(parts) >= 7:
        parsed["discipline"] = parts[6]
    if len(parts) >= 8:
        parsed["leaf"] = "/".join(parts[7:])
    return parsed


def valid_centre_id(centre_id: str) -> bool:
    return bool(centre_id) and bool(CENTRE_RE.match(centre_id))


def prefix_ok(parsed: dict[str, str | list[str]], channel: str, centre_id: str) -> bool:
    return (
        parsed.get("channel") == channel
        and parsed.get("version") == "a"
        and parsed.get("system") == "wis2"
        and parsed.get("centre_id") == centre_id
    )


def metadata_topic_ok(topic: str, channel: str, centre_id: str) -> bool:
    expected = f"{channel}/a/wis2/{centre_id}/metadata"
    return (topic or "").strip("/") == expected


def data_topic_ok(topic: str, channel: str, centre_id: str) -> tuple[bool, str]:
    parsed = parse_topic(topic)
    parts = parsed.get("parts") or []
    if not prefix_ok(parsed, channel, centre_id):
        return False, f"expected {channel}/a/wis2/{centre_id}/data/…"
    if parsed.get("notification_type") != "data":
        return False, "level 5 must be data"
    if parsed.get("data_policy") not in DATA_POLICIES:
        return False, "level 6 must be core or recommended"
    if parsed.get("discipline") not in DISCIPLINES:
        return False, (
            "level 7 must be an earth-system discipline "
            "(weather, climate, hydrology, ocean, cryosphere, "
            "atmospheric-composition, space-weather)"
        )
    if len(parts) < 8:
        return False, "data SHALL be published to at least level 8 (sub-discipline)"
    return True, ""


def _sample_topics(messages: list[ParsedMessage], limit: int = 6) -> list[str]:
    seen: list[str] = []
    for msg in messages:
        if msg.topic not in seen:
            seen.append(msg.topic)
        if len(seen) >= limit:
            break
    return seen


def run_wth_tests(
    *,
    centre_id: str,
    origin_topic: str,
    cache_topic: str,
    monitor_topic: str,
    origin_metadata: list[ParsedMessage],
    origin_data: list[ParsedMessage],
    cache_metadata: list[ParsedMessage],
    cache_data: list[ParsedMessage],
    cache_data_core: list[ParsedMessage],
    monitor_messages: list[ParsedMessage],
    gdc_identifiers: list[str],
) -> list[TestResult]:
    results: list[TestResult] = []
    centre = (centre_id or "").strip()

    built_ok = (
        origin_topic == f"origin/a/wis2/{centre}/#"
        and cache_topic == f"cache/a/wis2/{centre}/#"
        and monitor_topic in {f"monitor/a/wis2/{centre}", f"monitor/a/wis2/{centre}/#"}
        and valid_centre_id(centre)
    )
    results.append(
        make_result(
            "WTH-001",
            "WIS2 Topic Hierarchy / centre topic structure",
            "Centre topic structure",
            CONFORMANCE,
            PASS if built_ok else FAIL,
            expected=f"origin|cache|monitor /a/wis2/{centre}/… and tld-centre-name",
            received=(
                f"origin={origin_topic}; cache={cache_topic}; "
                f"monitor={monitor_topic}; centre-id={centre}"
            ),
            recommendation=""
            if built_ok
            else "Use the endorsed centre-id form tld-centre-name and the standard topic prefixes.",
        )
    )

    origin_msgs = origin_metadata + origin_data
    if not origin_msgs:
        results.append(
            make_result(
                "WTH-002",
                "WIS2 Topic Hierarchy / origin",
                "Origin topic validity",
                CONFORMANCE,
                NOT_OBSERVED,
                expected=f"origin/a/wis2/{centre}/…",
                received="no origin notifications yet",
                recommendation="Wait for the node to publish, or confirm the centre-id.",
            )
        )
    else:
        bad = [msg.topic for msg in origin_msgs if not prefix_ok(parse_topic(msg.topic), "origin", centre)]
        results.append(
            make_result(
                "WTH-002",
                "WIS2 Topic Hierarchy / origin",
                "Origin topic validity",
                CONFORMANCE,
                FAIL if bad else PASS,
                expected=f"origin/a/wis2/{centre}/…",
                received="; ".join(_sample_topics(origin_msgs)),
                evidence={"invalid_topics": list(dict.fromkeys(bad))[:8], "observed": len(origin_msgs)},
                recommendation="" if not bad else "Correct origin topics to origin/a/wis2/{centre-id}/…",
            )
        )

    if not origin_metadata:
        results.append(
            make_result(
                "WTH-003",
                "WIS2 Topic Hierarchy / metadata",
                "Metadata topic validity",
                INTEROPERABILITY,
                NOT_OBSERVED,
                expected=f"origin/a/wis2/{centre}/metadata",
                received="no origin metadata notifications yet",
                recommendation="The node should publish discovery metadata before data.",
            )
        )
    else:
        bad = [msg.topic for msg in origin_metadata if not metadata_topic_ok(msg.topic, "origin", centre)]
        results.append(
            make_result(
                "WTH-003",
                "WIS2 Topic Hierarchy / metadata",
                "Metadata topic validity",
                CONFORMANCE,
                FAIL if bad else PASS,
                expected=f"origin/a/wis2/{centre}/metadata (exactly level 5)",
                received="; ".join(_sample_topics(origin_metadata)),
                evidence={"invalid_topics": list(dict.fromkeys(bad))[:8]},
                recommendation=""
                if not bad
                else "Publish metadata exactly on origin/a/wis2/{centre-id}/metadata.",
            )
        )

    if not origin_data:
        results.append(
            make_result(
                "WTH-004",
                "WIS2 Topic Hierarchy / data",
                "Data topic validity",
                INTEROPERABILITY,
                NOT_OBSERVED,
                expected=f"origin/a/wis2/{centre}/data/{{core|recommended}}/{{discipline}}/…",
                received="no origin data notifications yet",
                recommendation="Publish data notifications to at least WTH level 8.",
            )
        )
    else:
        failures: list[str] = []
        for msg in origin_data:
            ok, reason = data_topic_ok(msg.topic, "origin", centre)
            if not ok:
                failures.append(f"{msg.topic} ({reason})")
        results.append(
            make_result(
                "WTH-004",
                "WIS2 Topic Hierarchy / data",
                "Data topic validity",
                CONFORMANCE,
                FAIL if failures else PASS,
                expected="channel/a/wis2/{centre-id}/data/{core|recommended}/{discipline}/{sub-discipline…}",
                received="; ".join(_sample_topics(origin_data)),
                evidence={"invalid": failures[:8], "observed": len(origin_data)},
                recommendation=""
                if not failures
                else "Correct the data topic hierarchy (policy, discipline, and at least level 8).",
            )
        )

    if not cache_metadata and not cache_data_core:
        results.append(
            make_result(
                "WTH-005",
                "WIS2 Topic Hierarchy / cache",
                "Cache topic behaviour",
                INTEROPERABILITY,
                NOT_OBSERVED,
                expected=f"cache/a/wis2/{centre}/metadata and cache/…/data/core/#",
                received="no cache notifications yet",
                recommendation=(
                    "Allow time for the Global Cache. Core data should reappear on cache/a/wis2/…"
                ),
            )
        )
    else:
        bad_meta = [
            msg.topic
            for msg in cache_metadata
            if not metadata_topic_ok(msg.topic, "cache", centre)
        ]
        bad_data = []
        for msg in cache_data:
            ok, reason = data_topic_ok(msg.topic, "cache", centre)
            if not ok:
                bad_data.append(f"{msg.topic} ({reason})")
        observed_core = bool(cache_data_core)
        observed_meta = bool(cache_metadata)
        if bad_meta or bad_data:
            status = FAIL
            rec = "Cache topics must follow the same WTH as origin, with channel cache."
        elif observed_meta and observed_core:
            status = PASS
            rec = ""
        else:
            status = NOT_OBSERVED
            rec = "Both cache metadata and cache data/core should be observed for a complete assessment."
        results.append(
            make_result(
                "WTH-005",
                "WIS2 Topic Hierarchy / cache",
                "Cache topic behaviour",
                INTEROPERABILITY,
                status,
                expected=f"cache/a/wis2/{centre}/metadata and cache/…/data/core/…",
                received=(
                    f"metadata={len(cache_metadata)} data/core={len(cache_data_core)}"
                ),
                evidence={"invalid_metadata": bad_meta[:6], "invalid_data": bad_data[:6]},
                recommendation=rec,
            )
        )

    monitor_expected = f"monitor/a/wis2/{centre}"
    monitor_ok = monitor_topic.rstrip("/") in {monitor_expected, f"{monitor_expected}/#"}
    if monitor_messages:
        foreign = [
            msg.topic
            for msg in monitor_messages
            if not msg.topic.startswith(monitor_expected)
        ]
        status = FAIL if foreign or not monitor_ok else PASS
        received = "; ".join(_sample_topics(monitor_messages))
        rec = "" if status == PASS else f"Monitor events belong on {monitor_expected}."
    elif monitor_ok:
        status = PASS
        received = f"subscribed {monitor_topic} (no events yet; often normal)"
        rec = ""
    else:
        status = FAIL
        received = monitor_topic
        rec = f"Subscribe to {monitor_expected}."
    results.append(
        make_result(
            "WTH-006",
            "WIS2 Topic Hierarchy / monitor",
            "Monitor topic behaviour",
            INTEROPERABILITY,
            status,
            expected=monitor_expected,
            received=received,
            recommendation=rec,
        )
    )

    mismatches: list[str] = []
    if not valid_centre_id(centre):
        mismatches.append(f"configured centre-id {centre!r} is not tld-centre-name")
    urn_prefix = f"urn:wmo:md:{centre}:"
    for msg in origin_metadata + origin_data + cache_metadata + cache_data:
        mid = msg.metadata_id()
        if mid and not mid.lower().startswith(urn_prefix):
            if ":md:" in mid.lower():
                mismatches.append(f"metadata_id {mid} does not use centre-id {centre}")
        topic_centre = str(parse_topic(msg.topic).get("centre_id") or "")
        if topic_centre and topic_centre != centre:
            mismatches.append(f"topic centre-id {topic_centre} != {centre}")
    for identifier in gdc_identifiers:
        if identifier and centre not in identifier.lower():
            mismatches.append(f"GDC identifier {identifier} does not include {centre}")
    unique = list(dict.fromkeys(mismatches))
    if not origin_msgs and not cache_metadata and not gdc_identifiers:
        consistency = NOT_OBSERVED
        rec = "Centre-id consistency is checked once notifications or GDC records are present."
    elif unique:
        consistency = FAIL
        rec = "Use the same endorsed centre-id in topics, metadata_id (urn:wmo:md:{centre-id}:…), and GDC records."
    else:
        consistency = PASS
        rec = ""
    results.append(
        make_result(
            "WTH-007",
            "WIS2 Topic Hierarchy / centre-id",
            "Centre-id consistency",
            CONFORMANCE,
            consistency,
            expected=f"topics, urn:wmo:md:{centre}:…, and GDC identifiers agree",
            received="; ".join(unique[:6]) if unique else f"consistent with {centre}",
            evidence={"mismatches": unique[:12]},
            recommendation=rec,
        )
    )
    return results
