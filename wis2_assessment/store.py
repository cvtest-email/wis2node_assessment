"""Thread-safe message store and assessment evaluation."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable

from .gdc import GdcSnapshot
from .messages import ParsedMessage


@dataclass
class ETSRecord:
    metadata_id: str
    dataset: str
    passed: int
    failed: int
    skipped: int
    warning: int
    severity: str
    generated_by: str
    source: str
    datetime: str
    tests: list[dict] = field(default_factory=list)
    topic: str = ""

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.passed > 0


@dataclass
class Assessment:
    centre_id: str
    origin_metadata: list[ParsedMessage] = field(default_factory=list)
    origin_data: list[ParsedMessage] = field(default_factory=list)
    cache_metadata: list[ParsedMessage] = field(default_factory=list)
    cache_data: list[ParsedMessage] = field(default_factory=list)
    cache_data_core: list[ParsedMessage] = field(default_factory=list)
    ets_reports: list[ETSRecord] = field(default_factory=list)
    monitor_other: list[ParsedMessage] = field(default_factory=list)
    monitor_errors: list[ParsedMessage] = field(default_factory=list)
    gdc: GdcSnapshot | None = None

    @property
    def origin_metadata_ok(self) -> bool:
        return bool(self.origin_metadata)

    @property
    def origin_data_ok(self) -> bool:
        return bool(self.origin_data)

    @property
    def cache_metadata_ok(self) -> bool:
        return bool(self.cache_metadata)

    @property
    def cache_data_core_ok(self) -> bool:
        return bool(self.cache_data_core)

    @property
    def gdc_ok(self) -> bool:
        return bool(self.gdc and self.gdc.ok and self.gdc.records)

    @property
    def ets_ok(self) -> bool:
        return bool(self.ets_reports) and all(item.all_passed for item in self.ets_reports)

    @property
    def mqtt_checks_ok(self) -> bool:
        return all(
            [
                self.origin_metadata_ok,
                self.origin_data_ok,
                self.cache_metadata_ok,
                self.cache_data_core_ok,
            ]
        )

    @property
    def official_complete(self) -> bool:
        """Five checks from the WIS2 Node Assessment Scenario."""
        return self.gdc_ok and self.mqtt_checks_ok

    @property
    def complete(self) -> bool:
        return self.official_complete and not self.monitor_errors and (
            not self.ets_reports or self.ets_ok
        )

    def unique_metadata_ids(self, messages: Iterable[ParsedMessage]) -> list[str]:
        seen: list[str] = []
        for msg in messages:
            value = msg.metadata_id() or msg.data_id()
            if value and value not in seen:
                seen.append(value)
        return seen

    def unique_datasets(self, messages: Iterable[ParsedMessage]) -> list[str]:
        seen: list[str] = []
        for msg in messages:
            name = msg.dataset_name()
            if name and name not in seen:
                seen.append(name)
        return seen

    def global_cache_ids(self) -> list[str]:
        seen: list[str] = []
        for msg in self.cache_metadata + self.cache_data:
            value = msg.global_cache()
            if value and value not in seen:
                seen.append(value)
        return seen

    def example_data(self) -> ParsedMessage | None:
        for collection in (self.origin_data, self.cache_data):
            for msg in reversed(collection):
                if msg.metadata_id() and msg.data_id():
                    return msg
        return None

    def published_dataset_types(self) -> list[str]:
        labels: list[str] = []
        if self.gdc and self.gdc.records:
            for record in self.gdc.records:
                label = record.dataset_type_label()
                if label not in labels:
                    labels.append(label)
            return labels
        for name in self.unique_datasets(self.origin_metadata or self.cache_metadata):
            if name not in labels:
                labels.append(name)
        topics: list[str] = []
        for msg in self.origin_data + self.cache_data_core:
            rest = msg.topic_rest()
            if rest[:1] == ["data"]:
                path = "/".join(rest[1:])
                if path and path not in topics:
                    topics.append(path)
        for path in topics:
            if path not in labels:
                labels.append(path)
        return labels


class MessageStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.messages: list[ParsedMessage] = []
        self.connection_status: dict[str, str] = {}
        self.subscription_status: dict[str, str] = {}
        self.gdc: GdcSnapshot | None = None

    def add(self, message: ParsedMessage) -> ParsedMessage:
        with self._lock:
            self.messages.append(message)
        return message

    def snapshot(self) -> list[ParsedMessage]:
        with self._lock:
            return list(self.messages)

    def set_gdc(self, snapshot: GdcSnapshot) -> None:
        with self._lock:
            self.gdc = snapshot

    def set_connection(self, status: str) -> None:
        with self._lock:
            self.connection_status["broker"] = status

    def set_subscription(self, topic: str, status: str) -> None:
        with self._lock:
            self.subscription_status[topic] = status

    def evaluate(self, centre_id: str) -> Assessment:
        messages = self.snapshot()
        with self._lock:
            gdc = self.gdc
        assessment = Assessment(centre_id=centre_id, gdc=gdc)
        ets_by_metadata: dict[str, ETSRecord] = {}
        for msg in messages:
            if msg.channel == "origin" and msg.is_metadata_topic():
                assessment.origin_metadata.append(msg)
            elif msg.channel == "origin" and msg.is_data_topic():
                assessment.origin_data.append(msg)
            elif msg.channel == "cache" and msg.is_metadata_topic():
                assessment.cache_metadata.append(msg)
            elif msg.channel == "cache" and msg.is_data_topic():
                assessment.cache_data.append(msg)
                if msg.is_data_core_topic():
                    assessment.cache_data_core.append(msg)
            elif msg.channel == "monitor":
                if msg.is_monitor_error() and msg.message_kind != "ets":
                    assessment.monitor_errors.append(msg)
                if msg.message_kind == "ets":
                    summary = msg.ets_summary()
                    metadata_id = msg.metadata_id()
                    record = ETSRecord(
                        metadata_id=metadata_id,
                        dataset=msg.dataset_name(),
                        passed=summary.get("PASSED", 0),
                        failed=summary.get("FAILED", 0),
                        skipped=summary.get("SKIPPED", 0),
                        warning=summary.get("WARNING", 0),
                        severity=msg.severity(),
                        generated_by=msg.generated_by(),
                        source=msg.report_source(),
                        datetime=msg.datetime_obs() or msg.pubtime(),
                        tests=msg.ets_tests(),
                        topic=msg.topic,
                    )
                    ets_by_metadata[metadata_id or msg.message_id()] = record
                    if not record.all_passed:
                        assessment.monitor_errors.append(msg)
                else:
                    assessment.monitor_other.append(msg)
        assessment.ets_reports = list(ets_by_metadata.values())
        return assessment
