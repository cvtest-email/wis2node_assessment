"""Generate a GISC-style WIS2 Node Assessment report from captured MQTT traffic."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import SessionConfig
from .engine import EngineReport, run_engine
from .store import Assessment, MessageStore


def _plain_table(rows: list[list[str]]) -> str:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines: list[str] = []
    for index, row in enumerate(rows):
        line = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        lines.append(line)
        if index == 0:
            lines.append("  ".join("-" * widths[i] for i in range(len(widths))))
    return "\n".join(lines)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _mark(ok: bool) -> str:
    return "observed" if ok else "not yet observed"


def _verdict_mark(status: str) -> str:
    if status == "PASS":
        return "✓"
    if status in {"FAIL", "NOT_OBSERVED"}:
        return "✗" if status == "FAIL" else "·"
    if status in {"WARNING", "PASS WITH WARNINGS", "INCOMPLETE"}:
        return "⚠"
    return "·"


def format_verdict_block(
    engine: EngineReport,
    assessment: Assessment,
    config: SessionConfig,
    generated_at: datetime,
) -> str:
    centre = assessment.centre_id or config.centre_id or "<centre-id>"
    gisc = config.gisc or "the responsible GISC"
    counts = engine.counts()
    overall = engine.overall()
    lines = [
        "====================================================",
        "             WIS2 NODE ASSESSMENT",
        "====================================================",
        f"Centre ID:    {centre}",
        f"Assessment:   {generated_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"GISC:         {gisc}",
        "Version:      1.2.0 (Phase 1 conformance engine)",
        "",
        "Overall Result",
        "----------------------------------------------------",
    ]
    for row in engine.matrix_rows():
        lines.append(f"{_verdict_mark(row[4])} {row[0]:<12} {row[4]}")
    lines.append("")
    lines.append(f"OVERALL: {overall}")
    lines.append("")
    lines.append(f"Tests: {counts.get('total', 0)}")
    lines.append(f"Passed: {counts.get('PASS', 0)}")
    lines.append(f"Failed: {counts.get('FAIL', 0)}")
    lines.append(f"Warnings: {counts.get('WARNING', 0)}")
    lines.append(f"Not observed: {counts.get('NOT_OBSERVED', 0)}")
    lines.append(f"Skipped: {counts.get('SKIP', 0)}")
    lines.append("====================================================")
    lines.append("")
    lines.append("Test group                          Tests  Passed  Failed  Status")
    lines.append("----------------------------------  -----  ------  ------  ------")
    for row in engine.matrix_rows():
        lines.append(
            f"{row[0]:<34}  {row[1]:>5}  {row[2]:>6}  {row[3]:>6}  {row[4]}"
        )
    lines.append("")
    interesting = [
        item
        for item in engine.results
        if item.result in {"FAIL", "WARNING"}
    ]
    if interesting:
        lines.append("Findings")
        lines.append("--------")
        for item in interesting:
            lines.append("")
            lines.append(f"Test ID:       {item.test_id}")
            lines.append(f"Requirement:   {item.requirement}")
            lines.append(f"Test:          {item.title}")
            lines.append(f"Class:         {item.classification}")
            lines.append(f"Result:        {item.result}")
            lines.append(f"Timestamp:     {item.timestamp}")
            if item.expected:
                lines.append(f"Expected:      {item.expected}")
            if item.received:
                lines.append(f"Received:      {item.received}")
            if item.evidence:
                topic = ""
                failures = item.evidence.get("failures") or item.evidence.get("urls") or []
                if failures and isinstance(failures, list) and isinstance(failures[0], dict):
                    topic = failures[0].get("topic") or failures[0].get("url") or ""
                    payload = failures[0].get("payload_sha256") or ""
                    if topic:
                        lines.append(f"Evidence:      {topic}")
                    if payload:
                        lines.append(f"Payload:       SHA256({payload})")
            if item.recommendation:
                lines.append(f"Recommendation: {item.recommendation}")
        lines.append("")
    return "\n".join(lines)


def generate_report_text(
    assessment: Assessment,
    config: SessionConfig,
    generated_at: datetime | None = None,
    engine: EngineReport | None = None,
) -> str:
    centre = assessment.centre_id or config.centre_id or "<centre-id>"
    gisc = config.gisc or "the responsible GISC"
    now = generated_at or datetime.now(timezone.utc)
    origin_meta_topic = f"origin/a/wis2/{centre}/metadata"
    origin_data_generic = f"origin/a/wis2/{centre}/data/#"
    cache_meta_topic = f"cache/a/wis2/{centre}/metadata"
    cache_data_topic = f"cache/a/wis2/{centre}/data/core/#"
    monitor_topic = config.monitor_topic or f"monitor/a/wis2/{centre}"
    gdc_url = (
        assessment.gdc.url
        if assessment.gdc
        else f'https://gdc.wis2dev.io/collections/wis2-discovery-metadata/items?q="{centre}"'
    )

    origin_datasets = assessment.unique_datasets(assessment.origin_metadata)
    cache_datasets = assessment.unique_datasets(assessment.cache_metadata)
    ets_ids = [item.metadata_id for item in assessment.ets_reports if item.metadata_id]
    if not ets_ids:
        ets_ids = assessment.unique_metadata_ids(assessment.origin_metadata)

    lines: list[str] = []
    if engine is not None:
        lines.append(format_verdict_block(engine, assessment, config, now).rstrip())
        lines.append("")
    lines.append(f"WIS2 Node Assessment report for {centre}")
    lines.append(f"Generated: {now.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append(f"GISC: {gisc}")
    lines.append(f"WIS2Dev Global Broker: {config.broker_url()}")
    lines.append("WIS2Dev Global Discovery Catalogue: https://gdc.wis2dev.io/collections/wis2-discovery-metadata/items")
    lines.append("")
    lines.append("This report follows the WIS2 Node Assessment Scenario for GISC.")
    lines.append("The Test Global Broker republishes only valid WNM and verifies that")
    lines.append("metadata is present for notifications on origin/a/wis2/<centre-id>/data.")
    lines.append("Invalid WNM, missing metadata, or invalid WCMP2 is reported on")
    lines.append(f"{monitor_topic}.")
    lines.append("")

    lines.append("1. WCMP2 in the Global Discovery Catalogue")
    lines.append("Official check:")
    lines.append(gdc_url)
    if assessment.gdc_ok and assessment.gdc:
        count = len(assessment.gdc.records)
        lines.append(
            f"WCMP2 discovery metadata for {centre} is present in the WIS2Dev GDC "
            f"({count} record(s)):"
        )
        for record in assessment.gdc.records:
            lines.append(record.dataset_type_label())
        lines.append(
            "The Test GDC only ingests valid WCMP2 advertised on cache/a/wis2/+/metadata, "
            "so these records demonstrate accepted discovery metadata."
        )
    elif assessment.gdc and assessment.gdc.error:
        lines.append(f"GDC search did not yet confirm WCMP2 for {centre}: {assessment.gdc.error}")
        lines.append(
            "Allow around 30 minutes after metadata publication before the Global Broker "
            "and GDC can validate corresponding data notifications."
        )
    else:
        lines.append(f"The GDC has not yet been queried, or no WCMP2 records were found for {centre}.")

    lines.append("")
    lines.append("2. Origin notifications")
    if assessment.origin_metadata_ok:
        lines.append("Metadata notifications were observed on:")
        lines.append(origin_meta_topic)
        if origin_datasets:
            lines.append("for the following dataset(s):")
            lines.extend(origin_datasets)
    else:
        lines.append(f"Metadata notifications were not yet observed on {origin_meta_topic}.")
        lines.append("Coordinate with the WIS2 Node contact to publish discovery metadata first.")

    lines.append("")
    if assessment.origin_data_ok:
        data_topics = []
        for msg in assessment.origin_data:
            if msg.topic not in data_topics:
                data_topics.append(msg.topic)
        lines.append("Data notifications were observed on:")
        for topic in data_topics[:8]:
            lines.append(topic)
        example = assessment.example_data()
        if example:
            lines.append("For example, a data notification contained:")
            if example.metadata_id():
                lines.append(f"metadata_id: {example.metadata_id()}")
            if example.data_id():
                lines.append(f"data_id: {example.data_id()}")
            if example.wigos_id():
                lines.append(f"wigos_station_identifier: {example.wigos_id()}")
    else:
        lines.append(f"Data notifications were not yet observed on {origin_data_generic}.")
        if assessment.origin_metadata_ok and not assessment.gdc_ok:
            lines.append(
                "Approximately 30 minutes are needed after metadata publication before "
                "the Test Global Broker can validate that published data has corresponding metadata."
            )

    lines.append("")
    lines.append("3. Cache notifications")
    lines.append("The Test Global Cache subscribes to origin/a/wis2/+/data/core/# on the Test GB.")
    if assessment.cache_metadata_ok:
        lines.append("Metadata notifications were observed on:")
        lines.append(cache_meta_topic)
        if cache_datasets:
            lines.append("for the dataset(s):")
            lines.extend(cache_datasets)
    else:
        lines.append(f"Metadata notifications were not yet observed on {cache_meta_topic}.")

    lines.append("")
    if assessment.cache_data_core_ok:
        cache_topics = []
        for msg in assessment.cache_data_core:
            if msg.topic not in cache_topics:
                cache_topics.append(msg.topic)
        lines.append("Core data notifications were observed on:")
        for topic in cache_topics[:8]:
            lines.append(topic)
        caches = assessment.global_cache_ids()
        if caches:
            lines.append("The cached notifications identify the Global Cache as:")
            lines.extend(caches)
        example_cache = next((m for m in reversed(assessment.cache_data_core) if m.canonical_href()), None)
        if example_cache and example_cache.canonical_href():
            lines.append("Canonical link after Global Cache ingest:")
            lines.append(example_cache.canonical_href())
    else:
        lines.append(f"Core data notifications were not yet observed on {cache_data_topic}.")

    lines.append("")
    lines.append("4. Monitor topic (errors, missing metadata, WCMP2 ETS)")
    lines.append(f"GISC follow-up topic: {monitor_topic}")
    if assessment.monitor_errors:
        lines.append("The following monitor events require follow-up with the WIS2 Node contact:")
        for msg in assessment.monitor_errors:
            title = msg.monitor_title() or msg.event_type() or "monitor event"
            severity = msg.severity() or "ERROR"
            lines.append(f"{severity}: {title}")
            if msg.metadata_id():
                lines.append(f"  metadata_id: {msg.metadata_id()}")
            if msg.event_type():
                lines.append(f"  type: {msg.event_type()}")
    else:
        lines.append("No invalid WNM, missing-metadata, or invalid WCMP2 errors were observed.")

    if assessment.ets_reports:
        lines.append("")
        lines.append("WCMP2 ETS (Evaluation/Testing Suite) reports were received from")
        sources = sorted({item.source for item in assessment.ets_reports if item.source})
        source = sources[0] if sources else "the Global Discovery Catalogue"
        lines.append(f"{source} on {monitor_topic} for:")
        for metadata_id in ets_ids:
            lines.append(metadata_id)
        lines.append("")
        if assessment.ets_ok:
            n_tests = assessment.ets_reports[0].passed + assessment.ets_reports[0].failed
            count_words = {1: "one record", 2: "two records", 3: "three records"}
            count_label = count_words.get(
                len(assessment.ets_reports),
                f"{len(assessment.ets_reports)} records",
            )
            lines.append(f"All {count_label} passed all {n_tests or 12} WCMP2 validation tests:")
        else:
            lines.append("ETS results (failures require corrective action):")
        lines.append("")
        table = [["Dataset", "Passed", "Failed", "Skipped", "Warning"]]
        for item in assessment.ets_reports:
            table.append(
                [
                    item.dataset or item.metadata_id,
                    str(item.passed),
                    str(item.failed),
                    str(item.skipped),
                    str(item.warning),
                ]
            )
        lines.append(_plain_table(table))
        generated = next((item.generated_by for item in assessment.ets_reports if item.generated_by), "")
        generated = generated.split("(")[0].strip()
        severity = next((item.severity for item in assessment.ets_reports if item.severity), "INFO")
        extra = f" by {generated}" if generated else ""
        lines.append(
            f"The reports were generated{extra} by the WIS2Dev Global Discovery Catalogue, "
            f"with severity reported as {severity}."
        )
        failed_tests: list[str] = []
        for item in assessment.ets_reports:
            for test in item.tests:
                if str(test.get("code", "")).upper() == "FAILED":
                    failed_tests.append(
                        f"{item.dataset}: {test.get('id', '')} — {test.get('message', '')}".strip(" —")
                    )
        if failed_tests:
            lines.append("Failed tests:")
            lines.extend(failed_tests)
    else:
        lines.append("No WCMP2 ETS reports have been observed yet on the monitor topic.")

    lines.append("")
    lines.append("5. Assessment status")
    lines.append("The WIS2 Node Assessment is considered complete when all of the following are demonstrated:")
    lines.append(f'WCMP2 in GDC  {gdc_url} — {_mark(assessment.gdc_ok)}')
    lines.append(f"origin/a/wis2/{centre}/metadata — {_mark(assessment.origin_metadata_ok)}")
    lines.append(f"origin/a/wis2/{centre}/data/# — {_mark(assessment.origin_data_ok)}")
    lines.append(f"cache/a/wis2/{centre}/metadata — {_mark(assessment.cache_metadata_ok)}")
    lines.append(f"cache/a/wis2/{centre}/data/core/# — {_mark(assessment.cache_data_core_ok)}")
    ets_mark = "observed, all tests passed" if assessment.ets_ok else _mark(bool(assessment.ets_reports))
    if assessment.ets_reports and not assessment.ets_ok:
        ets_mark = "observed, with failures — follow up required"
    lines.append(f"monitor/a/wis2/{centre} (ETS evidence) — {ets_mark}")
    lines.append(
        f"monitor/a/wis2/{centre} errors — "
        + ("follow-up required" if assessment.monitor_errors else "none observed")
    )
    lines.append("")
    lines.append("MQTT observations, GDC search results and WCMP2 ETS reports have been retained as evidence.")
    lines.append("")

    dataset_types = assessment.published_dataset_types()
    if assessment.complete:
        lines.append("Finalizing the WIS2 Node Assessment")
        lines.append(
            f"{gisc} can inform the WMO Secretariat of successful completion of the "
            f"WIS2 Node Assessment for {centre}."
        )
        if dataset_types:
            lines.append("Datasets published during the test:")
            lines.extend(dataset_types)
    elif assessment.official_complete and assessment.monitor_errors:
        lines.append(
            "The five official checklist items have been observed, but monitor-topic "
            "errors still require follow-up with the WIS2 Node contact before informing "
            "the WMO Secretariat."
        )
    else:
        lines.append(
            "Collection is still in progress. Additional notifications or GDC ingest "
            f"may be required before {gisc} can inform the WMO Secretariat that the "
            f"WIS2 Node Assessment for {centre} is complete."
        )
        if assessment.origin_metadata_ok and not assessment.gdc_ok:
            lines.append(
                "Note: approximately 30 minutes are needed for metadata to be used by "
                "the Global Broker to validate that published data has corresponding metadata."
            )
    lines.append("")
    return "\n".join(lines)


def generate_report_markdown(
    assessment: Assessment,
    config: SessionConfig,
    generated_at: datetime | None = None,
    engine: EngineReport | None = None,
) -> str:
    centre = assessment.centre_id or config.centre_id or "<centre-id>"
    lines = [
        f"# WIS2 Node Assessment — `{centre}`",
        "",
        "```",
        generate_report_text(assessment, config, generated_at=generated_at, engine=engine).rstrip(),
        "```",
        "",
        "## ETS summary",
        "",
    ]
    if assessment.ets_reports:
        rows = [
            [
                item.dataset or item.metadata_id,
                str(item.passed),
                str(item.failed),
                str(item.skipped),
                str(item.warning),
            ]
            for item in assessment.ets_reports
        ]
        lines.append(_md_table(["Dataset", "Passed", "Failed", "Skipped", "Warning"], rows))
    else:
        lines.append("_No ETS reports captured._")
    lines.append("")
    lines.append("## MQTT checklist")
    lines.append("")
    rows = [
        [f'GDC q="{centre}"', _mark(assessment.gdc_ok), str(len(assessment.gdc.records) if assessment.gdc else 0)],
        [f"origin/a/wis2/{centre}/metadata", _mark(assessment.origin_metadata_ok), str(len(assessment.origin_metadata))],
        [f"origin/a/wis2/{centre}/data/#", _mark(assessment.origin_data_ok), str(len(assessment.origin_data))],
        [f"cache/a/wis2/{centre}/metadata", _mark(assessment.cache_metadata_ok), str(len(assessment.cache_metadata))],
        [f"cache/a/wis2/{centre}/data/core/#", _mark(assessment.cache_data_core_ok), str(len(assessment.cache_data_core))],
        [f"monitor/a/wis2/{centre} ETS", _mark(assessment.ets_ok), str(len(assessment.ets_reports))],
        [f"monitor/a/wis2/{centre} errors", "none" if not assessment.monitor_errors else "follow-up required", str(len(assessment.monitor_errors))],
    ]
    lines.append(_md_table(["Check", "Status", "Messages"], rows))
    lines.append("")
    return "\n".join(lines)


def evidence_dir(config: SessionConfig, when: datetime | None = None) -> Path:
    when = when or datetime.now().astimezone()
    centre = config.centre_id or "wis2node"
    stamp = when.strftime("%Y%m%dT%H%M%S")
    if config.output_dir:
        base = Path(config.output_dir).expanduser()
    else:
        base = Path.cwd() / "wis2_assessment_output"
    return base / f"{centre}_{stamp}"


def write_evidence(
    store: MessageStore,
    config: SessionConfig,
    directory: Path | None = None,
) -> Path:
    directory = directory or evidence_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    if store.gdc is None and not config.skip_gdc:
        from .gdc import query_gdc

        store.set_gdc(query_gdc(config.centre_id, config.gdc_url))
    assessment = store.evaluate(config.centre_id)
    engine = run_engine(store, config, probe_http=not config.skip_http)
    messages = store.snapshot()

    jsonl_path = directory / "messages.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for msg in messages:
            handle.write(json.dumps(msg.to_record(), ensure_ascii=False) + "\n")

    grouped = {"origin": [], "cache": [], "monitor": []}
    for msg in messages:
        grouped.setdefault(msg.channel, []).append(msg.to_record())
    for channel, rows in grouped.items():
        (directory / f"{channel}.json").write_text(
            json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    report_txt = generate_report_text(assessment, config, engine=engine)
    report_md = generate_report_markdown(assessment, config, engine=engine)
    (directory / "REPORT.txt").write_text(report_txt, encoding="utf-8")
    (directory / "REPORT.md").write_text(report_md, encoding="utf-8")

    if assessment.gdc is not None:
        (directory / "gdc.json").write_text(
            json.dumps(assessment.gdc.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    summary = {
        "centre_id": config.centre_id,
        "broker": config.broker_url(),
        "gdc_url": assessment.gdc.url if assessment.gdc else config.gdc_url,
        "topics": config.topics(),
        "complete": assessment.complete,
        "official_complete": assessment.official_complete,
        "overall": engine.overall(),
        "test_counts": engine.counts(),
        "dataset_types": assessment.published_dataset_types(),
        "counts": {
            "origin_metadata": len(assessment.origin_metadata),
            "origin_data": len(assessment.origin_data),
            "cache_metadata": len(assessment.cache_metadata),
            "cache_data_core": len(assessment.cache_data_core),
            "ets_reports": len(assessment.ets_reports),
            "monitor_errors": len(assessment.monitor_errors),
            "gdc_records": len(assessment.gdc.records) if assessment.gdc else 0,
            "total_messages": len(messages),
        },
    }
    (directory / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "tests.json").write_text(
        json.dumps(engine.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return directory


def load_jsonl(path: Path) -> list[ParsedMessage]:
    from datetime import datetime as dt

    from .messages import parse_mqtt_message

    messages: list[ParsedMessage] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        payload = row.get("payload")
        if isinstance(payload, dict):
            payload_text = json.dumps(payload)
        else:
            payload_text = str(payload or "")
        received = row.get("received_at")
        when = None
        if received:
            try:
                when = dt.fromisoformat(received)
            except ValueError:
                when = None
        messages.append(
            parse_mqtt_message(
                channel=row.get("channel") or "other",
                topic=row.get("topic") or "",
                payload=payload_text,
                retained=bool(row.get("retained")),
                qos=int(row.get("qos") or 0),
                received_at=when,
            )
        )
    return messages
