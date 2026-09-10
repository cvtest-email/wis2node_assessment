"""Parse and summarise WIS2 Notification Messages and WME monitoring events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


URN_DATASET_RE = re.compile(r"urn:wmo:md:[^:]+:(.+)$")


@dataclass
class ParsedMessage:
    channel: str
    topic: str
    payload_text: str
    received_at: datetime
    retained: bool = False
    qos: int = 0
    body: dict[str, Any] = field(default_factory=dict)
    parse_error: str = ""

    @property
    def message_kind(self) -> str:
        if self.body.get("type") == "Feature":
            if self.topic_leaf() == "metadata" or "/metadata" in f"/{self.topic}":
                return "metadata"
            return "data"
        event_type = str(self.body.get("type") or "")
        if "wcmp2.ets" in event_type or self._content().get("report_type") == "ets":
            return "ets"
        if "wcmp2.kpi" in event_type or self._content().get("report_type") == "kpi":
            return "kpi"
        if "specversion" in self.body:
            return "monitor"
        return "other"

    def topic_leaf(self) -> str:
        parts = [p for p in self.topic.strip("/").split("/") if p]
        return parts[-1] if parts else ""

    def topic_rest(self) -> list[str]:
        parts = [p for p in self.topic.strip("/").split("/") if p]
        if len(parts) >= 4 and parts[1] == "a" and parts[2] == "wis2":
            return parts[4:]
        return parts

    def is_metadata_topic(self) -> bool:
        rest = self.topic_rest()
        return bool(rest) and rest[0] == "metadata"

    def is_data_topic(self) -> bool:
        rest = self.topic_rest()
        return bool(rest) and rest[0] == "data"

    def is_data_core_topic(self) -> bool:
        rest = self.topic_rest()
        return len(rest) >= 2 and rest[0] == "data" and rest[1] == "core"

    def properties(self) -> dict[str, Any]:
        props = self.body.get("properties")
        return props if isinstance(props, dict) else {}

    def _content(self) -> dict[str, Any]:
        data = self.body.get("data")
        if isinstance(data, dict):
            content = data.get("content")
            if isinstance(content, dict):
                return content
            return data
        return {}

    def data_id(self) -> str:
        return str(self.properties().get("data_id") or self._content().get("data_id") or "")

    def metadata_id(self) -> str:
        return str(
            self.properties().get("metadata_id")
            or self._content().get("metadata_id")
            or ""
        )

    def dataset_name(self) -> str:
        mid = self.metadata_id() or self.data_id()
        match = URN_DATASET_RE.search(mid)
        if match:
            return match.group(1)
        if ":" in mid:
            return mid.rsplit(":", 1)[-1]
        if "/" in mid:
            return mid.rstrip("/").split("/")[-1]
        return mid or self.topic_leaf()

    def datetime_obs(self) -> str:
        return str(self.properties().get("datetime") or self._content().get("datetime") or "")

    def pubtime(self) -> str:
        return str(self.properties().get("pubtime") or self.body.get("time") or "")

    def wigos_id(self) -> str:
        props = self.properties()
        return str(
            props.get("wigos_station_identifier")
            or props.get("wigos-station-identifier")
            or ""
        )

    def global_cache(self) -> str:
        return str(self.properties().get("global-cache") or "")

    def generated_by(self) -> str:
        return str(self.body.get("generated_by") or self._content().get("generated_by") or "")

    def geometry(self) -> dict[str, Any] | None:
        geom = self.body.get("geometry")
        return geom if isinstance(geom, dict) else None

    def links(self) -> list[dict[str, Any]]:
        links = self.body.get("links")
        if isinstance(links, list):
            return [item for item in links if isinstance(item, dict)]
        nested = self._content().get("links")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        return []

    def link(self, rel: str) -> dict[str, Any] | None:
        for item in self.links():
            if item.get("rel") == rel:
                return item
        return None

    def canonical_href(self) -> str:
        link = self.link("canonical") or self.link("update")
        return str(link.get("href") or "") if link else ""

    def message_id(self) -> str:
        return str(self.body.get("id") or "")

    def severity(self) -> str:
        data = self.body.get("data")
        if isinstance(data, dict):
            return str(data.get("severity") or "")
        return ""

    def ets_summary(self) -> dict[str, int]:
        summary = self._content().get("summary")
        if not isinstance(summary, dict):
            return {}
        out: dict[str, int] = {}
        for key in ("PASSED", "FAILED", "SKIPPED", "WARNING"):
            try:
                out[key] = int(summary.get(key, 0) or 0)
            except (TypeError, ValueError):
                out[key] = 0
        return out

    def ets_tests(self) -> list[dict[str, Any]]:
        tests = self._content().get("tests")
        if isinstance(tests, list):
            return [t for t in tests if isinstance(t, dict)]
        return []

    def report_source(self) -> str:
        return str(self.body.get("source") or self._content().get("report_by") or "")

    def event_type(self) -> str:
        return str(self.body.get("type") or "")

    def monitor_title(self) -> str:
        content = self._content()
        for key in ("title", "message", "summary", "description"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        data = self.body.get("data")
        if isinstance(data, dict):
            for key in ("title", "message", "description"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return self.event_type()

    def is_monitor_error(self) -> bool:
        severity = self.severity().upper()
        if severity in {"ERROR", "CRITICAL", "FATAL", "FAIL"}:
            return True
        if self.message_kind == "ets" and self.ets_summary().get("FAILED", 0) > 0:
            return True
        event = self.event_type().lower()
        return any(token in event for token in ("error", "invalid", "reject", "fail"))

    def to_record(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "topic": self.topic,
            "received_at": self.received_at.isoformat(),
            "retained": self.retained,
            "qos": self.qos,
            "kind": self.message_kind,
            "payload": self.body if self.body else self.payload_text,
            "parse_error": self.parse_error,
        }


def parse_payload(text: str) -> tuple[dict[str, Any], str]:
    raw = (text or "").strip()
    if not raw:
        return {}, "empty payload"
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"invalid JSON: {exc}"
    if not isinstance(body, dict):
        return {}, "JSON payload is not an object"
    return body, ""


def parse_mqtt_message(
    channel: str,
    topic: str,
    payload: bytes | str,
    retained: bool = False,
    qos: int = 0,
    received_at: datetime | None = None,
) -> ParsedMessage:
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            text = payload.decode("utf-8", errors="replace")
    else:
        text = payload
    body, error = parse_payload(text)
    return ParsedMessage(
        channel=channel,
        topic=topic,
        payload_text=text,
        received_at=received_at or datetime.now(timezone.utc),
        retained=retained,
        qos=qos,
        body=body,
        parse_error=error,
    )


def format_coord(lat: float, lon: float) -> str:
    lat_hem = "S" if lat < 0 else "N"
    lon_hem = "W" if lon < 0 else "E"
    return f"{abs(lat):.4f}°{lat_hem} {abs(lon):.4f}°{lon_hem}"


def format_geometry(geom: dict[str, Any] | None) -> str:
    if not geom:
        return ""
    kind = geom.get("type")
    coords = geom.get("coordinates")
    if kind == "Point" and isinstance(coords, list) and len(coords) >= 2:
        lon, lat = float(coords[0]), float(coords[1])
        return f"Point {format_coord(lat, lon)}"
    if kind == "Polygon" and isinstance(coords, list) and coords:
        ring = coords[0] if coords and isinstance(coords[0], list) else coords
        lons: list[float] = []
        lats: list[float] = []
        for point in ring:
            if isinstance(point, list) and len(point) >= 2:
                lons.append(float(point[0]))
                lats.append(float(point[1]))
        if lons and lats:
            return (
                f"Polygon {format_coord(min(lats), min(lons))}"
                f" → {format_coord(max(lats), max(lons))}"
            )
    return str(kind or "")


def local_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def short_id(value: str, length: int = 8) -> str:
    return value[:length] if value else ""
