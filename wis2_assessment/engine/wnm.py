"""WNM — WIS2 Notification Message checks on captured origin/cache payloads."""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, Callable
from urllib.parse import urlparse

from ..messages import ParsedMessage
from .results import (
    CONFORMANCE,
    FAIL,
    NOT_OBSERVED,
    PASS,
    QUALITY,
    WARNING,
    TestResult,
    make_result,
)

RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
UTC_SUFFIX_RE = re.compile(r"(?:Z|[+-]00:00)$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
LINK_SCHEMES = {"http", "https", "ftp", "sftp"}
INTEGRITY_METHODS = {
    "sha256",
    "sha384",
    "sha512",
    "sha3-256",
    "sha3-384",
    "sha3-512",
}
CONTENT_ENCODINGS = {"utf-8", "utf8", "base64", "gzip"}
WNM_SIZE_LIMIT = 8192
CONTENT_ENCODED_LIMIT = 4096
MAX_EVIDENCE = 6


def payload_sha256(message: ParsedMessage) -> str:
    raw = message.payload_text or ""
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def evidence_for(message: ParsedMessage, **extra: Any) -> dict[str, Any]:
    payload = {
        "topic": message.topic,
        "message_id": message.message_id(),
        "payload_sha256": payload_sha256(message),
        "channel": message.channel,
    }
    payload.update(extra)
    return payload


def is_wnm_candidate(message: ParsedMessage) -> bool:
    if message.channel not in {"origin", "cache"}:
        return False
    if message.message_kind in {"ets", "kpi", "monitor"}:
        return False
    return True


def is_uuid(value: str) -> bool:
    text = (value or "").strip()
    if not UUID_RE.match(text):
        return False
    try:
        uuid.UUID(text)
    except (ValueError, TypeError):
        return False
    return True


def rfc3339_utc(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or not RFC3339_RE.match(value):
        return False
    return bool(UTC_SUFFIX_RE.search(value))


def geometry_ok(value: Any) -> tuple[bool, str]:
    if value is None:
        return True, "null"
    if not isinstance(value, dict):
        return False, "geometry must be null, Point, or Polygon"
    kind = value.get("type")
    coords = value.get("coordinates")
    if kind == "Point":
        if not isinstance(coords, list) or len(coords) < 2:
            return False, "Point needs [lon, lat]"
        try:
            lon, lat = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            return False, "Point coordinates are not numbers"
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return False, f"Point out of range ({lon}, {lat})"
        return True, "Point"
    if kind == "Polygon":
        if not isinstance(coords, list) or not coords:
            return False, "Polygon needs a ring of coordinates"
        return True, "Polygon"
    return False, f"unsupported geometry type {kind!r}"


def _aggregate(
    test_id: str,
    requirement: str,
    title: str,
    classification: str,
    messages: list[ParsedMessage],
    check: Callable[[ParsedMessage], tuple[str, str, str, dict[str, Any]]],
    *,
    empty_recommendation: str,
    warn_is_ok: bool = False,
) -> TestResult:
    if not messages:
        return make_result(
            test_id,
            requirement,
            title,
            classification,
            NOT_OBSERVED,
            received="no origin/cache notification messages yet",
            recommendation=empty_recommendation,
        )
    failed: list[dict[str, Any]] = []
    warned: list[dict[str, Any]] = []
    first_expected = ""
    first_received = ""
    for msg in messages:
        status, expected, received, extra = check(msg)
        if not first_expected:
            first_expected = expected
        if status == FAIL:
            if not first_received:
                first_received = received
            failed.append(evidence_for(msg, detail=received, **extra))
        elif status == WARNING:
            warned.append(evidence_for(msg, detail=received, **extra))
    if failed:
        return make_result(
            test_id,
            requirement,
            title,
            classification,
            FAIL,
            expected=first_expected,
            received=first_received or f"{len(failed)} of {len(messages)} message(s) failed",
            evidence={"failures": failed[:MAX_EVIDENCE], "failed": len(failed), "checked": len(messages)},
            recommendation=f"Correct the failing WNM ({test_id}). Captured payload hashes are in the evidence.",
        )
    if warned and not warn_is_ok:
        return make_result(
            test_id,
            requirement,
            title,
            QUALITY if classification == CONFORMANCE else classification,
            WARNING,
            expected=first_expected,
            received=f"{len(warned)} of {len(messages)} message(s) have warnings",
            evidence={"warnings": warned[:MAX_EVIDENCE], "checked": len(messages)},
            recommendation=warned[0].get("detail", ""),
        )
    return make_result(
        test_id,
        requirement,
        title,
        classification,
        PASS,
        expected=first_expected,
        received=f"{len(messages)} notification(s) checked",
        evidence={"checked": len(messages)},
    )


def run_wnm_tests(messages: list[ParsedMessage]) -> list[TestResult]:
    candidates = [msg for msg in messages if is_wnm_candidate(msg)]
    empty = "Capture origin or cache notifications before WNM tests can run."

    def check_json(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        size = len((msg.payload_text or "").encode("utf-8", errors="replace"))
        if msg.parse_error:
            return FAIL, "JSON object", msg.parse_error, {"size": size}
        if size > WNM_SIZE_LIMIT:
            return WARNING, f"WNM ≤ {WNM_SIZE_LIMIT} bytes", f"{size} bytes", {"size": size}
        return PASS, "valid JSON object", "ok", {"size": size}

    def check_required(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        if msg.parse_error:
            return FAIL, "id, type, geometry, properties, links", msg.parse_error, {}
        missing: list[str] = []
        if "id" not in msg.body:
            missing.append("id")
        if "type" not in msg.body:
            missing.append("type")
        if "geometry" not in msg.body:
            missing.append("geometry")
        if not isinstance(msg.body.get("properties"), dict):
            missing.append("properties")
        if not isinstance(msg.body.get("links"), list):
            missing.append("links")
        props = msg.properties()
        if "pubtime" not in props:
            missing.append("properties.pubtime")
        if "data_id" not in props:
            missing.append("properties.data_id")
        has_dt = "datetime" in props
        has_range = "start_datetime" in props and "end_datetime" in props
        if not has_dt and not has_range:
            missing.append("properties.datetime or start_datetime+end_datetime")
        if "conformsTo" not in msg.body and "version" not in msg.body:
            missing.append("conformsTo or version")
        if missing:
            return FAIL, "WNM core properties", "missing " + ", ".join(missing), {}
        return PASS, "WNM core properties", "present", {}

    def check_type(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        if msg.parse_error:
            return FAIL, 'type = "Feature"', msg.parse_error, {}
        got = msg.body.get("type")
        if got != "Feature":
            return FAIL, 'type = "Feature"', f"type={got!r}", {}
        return PASS, 'type = "Feature"', "Feature", {}

    def check_id(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        value = msg.message_id()
        if not is_uuid(value):
            return FAIL, "RFC 4122 UUID", value or "(missing)", {}
        return PASS, "RFC 4122 UUID", value, {}

    def check_pubtime(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        value = msg.properties().get("pubtime")
        if not rfc3339_utc(value) or value is None:
            return FAIL, "properties.pubtime RFC3339 UTC", repr(value), {}
        return PASS, "properties.pubtime RFC3339 UTC", str(value), {}

    def check_geometry(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        if "geometry" not in msg.body:
            return FAIL, "geometry Point | Polygon | null", "missing", {}
        ok, detail = geometry_ok(msg.body.get("geometry"))
        if not ok:
            return FAIL, "geometry Point | Polygon | null", detail, {}
        return PASS, "geometry Point | Polygon | null", detail, {}

    def check_links(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        links = msg.links()
        if not links:
            return FAIL, "links[] with href+rel; one of canonical|update|deletion", "no links", {}
        for index, link in enumerate(links):
            if not link.get("href") or not link.get("rel"):
                return FAIL, "each link has href and rel", f"link[{index}] incomplete", {}
            scheme = urlparse(str(link.get("href"))).scheme.lower()
            if scheme not in LINK_SCHEMES:
                return FAIL, "href scheme http(s) or ftp(s)", f"scheme={scheme!r}", {}
        special = [str(link.get("rel")) for link in links if link.get("rel") in {"canonical", "update", "deletion"}]
        if len(special) != 1:
            return FAIL, "exactly one canonical, update, or deletion link", f"found {special}", {}
        return PASS, "valid WNM links", special[0], {}

    def check_content(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        content = msg.properties().get("content")
        if content is None:
            return PASS, "optional properties.content", "absent", {}
        if not isinstance(content, dict):
            return FAIL, "content.encoding/value/size", "content is not an object", {}
        encoding = str(content.get("encoding") or "").lower()
        if encoding not in CONTENT_ENCODINGS:
            return FAIL, "encoding utf-8 | base64 | gzip", f"encoding={encoding!r}", {}
        if "value" not in content:
            return FAIL, "content.value present", "missing value", {}
        encoded = str(content.get("value") or "")
        if len(encoded.encode("utf-8")) > CONTENT_ENCODED_LIMIT:
            return FAIL, f"encoded content ≤ {CONTENT_ENCODED_LIMIT} bytes", str(len(encoded)), {}
        return PASS, "valid optional content", encoding, {}

    def check_integrity(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        integrity = msg.properties().get("integrity")
        if integrity is None:
            return WARNING, "optional integrity.method + value", "not provided", {}
        if not isinstance(integrity, dict):
            return FAIL, "integrity {method, value}", "not an object", {}
        method = str(integrity.get("method") or "").lower()
        value = integrity.get("value")
        if method not in INTEGRITY_METHODS or not value:
            return FAIL, f"method in {sorted(INTEGRITY_METHODS)} and base64 value", f"{method!r}", {}
        return PASS, "valid integrity block", method, {}

    def check_cache_prop(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        if "cache" not in msg.properties():
            return PASS, "optional boolean properties.cache", "absent (default true)", {}
        value = msg.properties().get("cache")
        if not isinstance(value, bool):
            return FAIL, "properties.cache boolean", f"{value!r}", {}
        return PASS, "properties.cache boolean", str(value).lower(), {}

    def check_topic_rel(msg: ParsedMessage) -> tuple[str, str, str, dict[str, Any]]:
        if msg.parse_error:
            return FAIL, "topic matches WNM kind", msg.parse_error, {}
        if msg.is_metadata_topic() and not (msg.metadata_id() or msg.data_id()):
            return FAIL, "metadata topic has metadata_id or data_id", "neither present", {}
        if msg.is_data_topic() and not msg.data_id():
            return FAIL, "data topic has properties.data_id", "missing data_id", {}
        if msg.is_data_topic() and not msg.metadata_id():
            return WARNING, "data WNM SHOULD have metadata_id", "missing metadata_id", {}
        if msg.channel == "cache" and msg.is_data_topic() and not msg.global_cache():
            return WARNING, "cache data WNM SHOULD set properties.global-cache", "missing", {}
        return PASS, "topic and WNM properties agree", msg.topic, {}

    checks: list[tuple[str, str, str, str, Callable, bool]] = [
        ("WNM-001", "WIS2 Notification Message / JSON", "Valid JSON", CONFORMANCE, check_json, False),
        ("WNM-002", "WIS2 Notification Message / required fields", "Required fields", CONFORMANCE, check_required, False),
        ("WNM-003", "WIS2 Notification Message / type", "Valid message type", CONFORMANCE, check_type, False),
        ("WNM-004", "WIS2 Notification Message / id", "Valid id", CONFORMANCE, check_id, False),
        ("WNM-005", "WIS2 Notification Message / pubtime", "Valid timestamp", CONFORMANCE, check_pubtime, False),
        ("WNM-006", "WIS2 Notification Message / geometry", "Valid geometry", CONFORMANCE, check_geometry, False),
        ("WNM-007", "WIS2 Notification Message / links", "Valid links", CONFORMANCE, check_links, False),
        ("WNM-008", "WIS2 Notification Message / content", "Valid content", CONFORMANCE, check_content, False),
        ("WNM-009", "WIS2 Notification Message / integrity", "Valid integrity information", QUALITY, check_integrity, False),
        ("WNM-010", "WIS2 Notification Message / cache", "Valid cache property", CONFORMANCE, check_cache_prop, False),
        ("WNM-011", "WIS2 Notification Message / topic relationship", "Valid topic/message relationship", CONFORMANCE, check_topic_rel, False),
    ]
    results: list[TestResult] = []
    for test_id, requirement, title, classification, fn, warn_ok in checks:
        results.append(
            _aggregate(
                test_id,
                requirement,
                title,
                classification,
                candidates,
                fn,
                empty_recommendation=empty,
                warn_is_ok=warn_ok,
            )
        )
    return results
