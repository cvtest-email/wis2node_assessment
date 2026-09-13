"""Query the WIS2Dev Global Discovery Catalogue (OGC API — Records)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_GDC_URL = "https://gdc.wis2dev.io/collections/wis2-discovery-metadata/items"


@dataclass
class GdcRecord:
    identifier: str
    title: str
    description: str = ""
    data_policy: str = ""
    centre_id: str = ""
    themes: list[str] = field(default_factory=list)
    mqtt_channel: str = ""
    canonical_href: str = ""

    def dataset_type_label(self) -> str:
        policy = self.data_policy or "core"
        theme = self.themes[0] if self.themes else ""
        kind = ""
        topic = self.mqtt_channel or ""
        for marker in ("/data/core/", "/data/recommended/", "/data/"):
            if marker in topic:
                kind = topic.split(marker, 1)[-1]
                break
        remainder = kind
        if policy and remainder.startswith(f"{policy}/"):
            remainder = remainder[len(policy) + 1 :]
        if theme and remainder.startswith(f"{theme}/"):
            remainder = remainder[len(theme) + 1 :]
        parts = [p for p in (policy, theme, remainder) if p]
        label = " / ".join(parts) if parts else "dataset"
        if self.title:
            return f"{self.identifier} — {self.title} [{label}]"
        return f"{self.identifier} [{label}]"


@dataclass
class GdcSnapshot:
    centre_id: str
    url: str
    ok: bool = False
    error: str = ""
    number_matched: int = 0
    records: list[GdcRecord] = field(default_factory=list)
    queried_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("raw", None)
        payload["records"] = [asdict(item) for item in self.records]
        return payload


def gdc_search_url(centre_id: str, base_url: str = DEFAULT_GDC_URL, quoted: bool = True) -> str:
    query = f'"{centre_id}"' if quoted else centre_id
    params = urlencode({"q": query, "f": "json", "limit": 100})
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{params}"


def _parse_record(feature: dict[str, Any]) -> GdcRecord:
    props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    themes: list[str] = []
    for theme in props.get("themes") or []:
        if not isinstance(theme, dict):
            continue
        for concept in theme.get("concepts") or []:
            if isinstance(concept, dict) and concept.get("id"):
                themes.append(str(concept["id"]))
    channel = ""
    canonical = ""
    for link in feature.get("links") or []:
        if not isinstance(link, dict):
            continue
        if link.get("rel") == "canonical" and not canonical:
            canonical = str(link.get("href") or "")
        if not channel:
            channel = str(link.get("channel") or link.get("name") or "")
    identifier = str(
        feature.get("id")
        or props.get("identifier")
        or props.get("id")
        or ""
    )
    return GdcRecord(
        identifier=identifier,
        title=str(props.get("title") or ""),
        description=str(props.get("description") or ""),
        data_policy=str(props.get("wmo:dataPolicy") or props.get("dataPolicy") or ""),
        centre_id=str(props.get("centre-id") or props.get("centre_id") or ""),
        themes=themes,
        mqtt_channel=channel,
        canonical_href=canonical,
    )


def _belongs_to_centre(record: GdcRecord, centre_id: str) -> bool:
    centre = (centre_id or "").lower()
    if not centre:
        return True
    if record.centre_id.lower() == centre:
        return True
    blob = f"{record.identifier} {record.mqtt_channel}".lower()
    return centre in blob


def query_gdc(centre_id: str, base_url: str = DEFAULT_GDC_URL, timeout: int = 20) -> GdcSnapshot:
    centre = (centre_id or "").strip()
    snapshot = GdcSnapshot(
        centre_id=centre,
        url=gdc_search_url(centre, base_url, quoted=True),
        queried_at=datetime.now(timezone.utc).isoformat(),
    )
    if not centre:
        snapshot.error = "centre-id is required for the GDC search"
        return snapshot

    last_error = ""
    for quoted in (True, False):
        url = gdc_search_url(centre, base_url, quoted=quoted)
        snapshot.url = url
        try:
            request = Request(
                url,
                headers={
                    "Accept": "application/geo+json, application/json",
                    "User-Agent": "wis2-node-assessment/1.2",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = f"HTTP {exc.code} for {url}"
            continue
        except URLError as exc:
            last_error = f"GDC unreachable: {exc.reason}"
            continue
        except (json.JSONDecodeError, TimeoutError, OSError) as exc:
            last_error = f"GDC query failed: {exc}"
            continue

        snapshot.raw = body if isinstance(body, dict) else {}
        features = snapshot.raw.get("features") if isinstance(snapshot.raw, dict) else []
        records = [_parse_record(item) for item in features or [] if isinstance(item, dict)]
        records = [item for item in records if _belongs_to_centre(item, centre)]
        snapshot.records = records
        snapshot.number_matched = int(snapshot.raw.get("numberMatched") or len(records) or 0)
        if not snapshot.number_matched:
            snapshot.number_matched = len(records)
        snapshot.ok = bool(records)
        snapshot.error = "" if snapshot.ok else "no WCMP2 records matched this centre-id"
        if snapshot.ok or quoted is False:
            return snapshot

    snapshot.error = last_error or snapshot.error or "GDC query failed"
    snapshot.ok = False
    return snapshot
