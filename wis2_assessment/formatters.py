"""MQTT Explorer-style formatting for WIS2 messages."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Group, RenderableType
from rich.json import JSON
from rich.table import Table
from rich.text import Text

from .messages import ParsedMessage, format_geometry, local_timestamp


KIND_LABELS = {
    "data": "DATA",
    "metadata": "META",
    "ets": "ETS",
    "kpi": "KPI",
    "monitor": "MON",
    "other": "MSG",
}

KIND_COLORS = {
    "data": "green",
    "metadata": "blue",
    "ets": "yellow",
    "kpi": "yellow",
    "monitor": "yellow",
    "other": "white",
}


def _trunc(value: str, width: int = 64) -> str:
    value = value or ""
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def format_list_item(message: ParsedMessage, verbose: bool = False) -> Text:
    kind = message.message_kind
    color = KIND_COLORS.get(kind, "white")
    stamp = local_timestamp(message.received_at).split(" ")[-1]
    line = Text()
    line.append(stamp, style="dim")
    line.append("  ")
    line.append(f"{KIND_LABELS.get(kind, 'MSG'):<4}", style=f"bold {color}")
    line.append("  ")

    if kind in {"data", "metadata"}:
        title = message.dataset_name() or message.topic_leaf()
        line.append(title, style="bold")
        if message.wigos_id():
            line.append("  ")
            line.append(message.wigos_id(), style="cyan")
        if message.retained:
            line.append("  RET", style="dim italic")
        extra = Text()
        extra.append("\n         ")
        extra.append(_trunc(message.data_id() or message.metadata_id(), 72), style="dim")
        if verbose:
            extra.append("\n         ")
            if message.datetime_obs():
                extra.append(f"obs {message.datetime_obs()}  ", style="dim")
            if message.pubtime():
                extra.append(f"pub {message.pubtime()}", style="dim")
            cache = message.global_cache()
            if cache:
                extra.append(f"  cache={cache}", style="magenta")
            href = message.canonical_href()
            if href:
                extra.append("\n         ")
                extra.append(_trunc(href, 80), style="underline dim")
        line.append(extra)
        return line

    if kind == "ets":
        summary = message.ets_summary()
        line.append(message.dataset_name() or "WCMP2 ETS", style="bold")
        line.append("  ")
        failed = summary.get("FAILED", 0)
        style = "bold green" if failed == 0 else "bold red"
        line.append(
            f"P{summary.get('PASSED', 0)} F{failed} S{summary.get('SKIPPED', 0)} W{summary.get('WARNING', 0)}",
            style=style,
        )
        if message.severity():
            line.append(f"  {message.severity()}", style="dim")
        extra = Text("\n         ")
        extra.append(_trunc(message.metadata_id() or message.report_source(), 72), style="dim")
        line.append(extra)
        return line

    if message.channel == "monitor" or kind in {"kpi", "monitor"}:
        error = message.is_monitor_error()
        line.append("ERR " if error else "EVT ", style="bold red" if error else "bold yellow")
        line.append(_trunc(message.monitor_title() or message.event_type() or message.topic, 52), style="bold")
        extra = Text("\n         ")
        extra.append(_trunc(message.event_type() or message.severity() or message.topic, 72), style="dim")
        line.append(extra)
        return line

    line.append(_trunc(message.topic, 60))
    return line


def format_properties_table(message: ParsedMessage) -> Table:
    table = Table(
        show_header=False,
        box=None,
        pad_edge=False,
        expand=True,
        padding=(0, 1, 0, 0),
    )
    table.add_column("key", style="bold #bd93f9", no_wrap=True, min_width=14)
    table.add_column("value", overflow="fold")

    def row(key: str, value: Any) -> None:
        if value in (None, "", [], {}):
            return
        table.add_row(key, str(value))

    row("topic", message.topic)
    row("received", local_timestamp(message.received_at))
    row("kind", message.message_kind)
    row("id", message.message_id())
    row("data_id", message.data_id())
    row("metadata_id", message.metadata_id())
    row("datetime", message.datetime_obs())
    row("pubtime", message.pubtime())
    row("WIGOS", message.wigos_id())
    row("global-cache", message.global_cache())
    row("generated_by", message.generated_by())
    row("geometry", format_geometry(message.geometry()))
    row("severity", message.severity())
    row("source", message.report_source())
    row("event_type", message.event_type())
    row("monitor", message.monitor_title())
    if message.retained:
        row("retained", "yes")

    summary = message.ets_summary()
    if summary:
        row(
            "ETS",
            (
                f"PASSED {summary.get('PASSED', 0)}  "
                f"FAILED {summary.get('FAILED', 0)}  "
                f"SKIPPED {summary.get('SKIPPED', 0)}  "
                f"WARNING {summary.get('WARNING', 0)}"
            ),
        )

    for link in message.links():
        rel = link.get("rel", "link")
        media = link.get("type", "")
        length = link.get("length")
        href = link.get("href", "")
        meta = media
        if length not in (None, ""):
            meta = f"{media}  {length} B" if media else f"{length} B"
        label = f"{rel}"
        table.add_row(label, f"{href}" + (f"\n{meta}" if meta else ""))

    if message.parse_error:
        table.add_row("error", message.parse_error)
    return table


def format_detail(message: ParsedMessage | None) -> RenderableType:
    if message is None:
        return Text("Select a message to inspect payload details.", style="dim")
    payload: RenderableType
    if message.body:
        payload = JSON(json.dumps(message.body, ensure_ascii=False), indent=2)
    else:
        payload = Text(message.payload_text or "(empty payload)", style="red")
    return Group(
        format_properties_table(message),
        Text(""),
        payload,
    )


def format_status_line(channel: str, topic: str, count: int, status: str) -> str:
    mark = "●" if status in {"connected", "subscribed"} else "○"
    return f"{mark} {channel.upper()}  {topic}  {count} msg"


def checklist_rows(assessment) -> list[tuple[str, bool, str]]:
    centre = assessment.centre_id or "<centre-id>"
    gdc_detail = "not checked"
    if assessment.gdc is not None:
        if assessment.gdc.error and not assessment.gdc_ok:
            gdc_detail = assessment.gdc.error
        else:
            gdc_detail = f"{len(assessment.gdc.records)} WCMP2 record(s)"
    ets_detail = "not yet observed"
    if assessment.ets_reports:
        ets_detail = f"{len(assessment.ets_reports)} report(s)"
        if not assessment.ets_ok:
            ets_detail += ", failures present"
    error_detail = (
        f"{len(assessment.monitor_errors)} follow-up item(s)"
        if assessment.monitor_errors
        else "none"
    )
    return [
        (
            f'GDC q="{centre}"',
            assessment.gdc_ok,
            gdc_detail,
        ),
        (
            f"origin/a/wis2/{centre}/metadata",
            assessment.origin_metadata_ok,
            f"{len(assessment.origin_metadata)} observed",
        ),
        (
            f"origin/a/wis2/{centre}/data/#",
            assessment.origin_data_ok,
            f"{len(assessment.origin_data)} observed",
        ),
        (
            f"cache/a/wis2/{centre}/metadata",
            assessment.cache_metadata_ok,
            f"{len(assessment.cache_metadata)} observed",
        ),
        (
            f"cache/a/wis2/{centre}/data/core/#",
            assessment.cache_data_core_ok,
            f"{len(assessment.cache_data_core)} observed",
        ),
        (
            "monitor WCMP2 ETS",
            assessment.ets_ok,
            ets_detail,
        ),
        (
            "monitor errors",
            not assessment.monitor_errors,
            error_detail,
        ),
    ]


def format_checklist(assessment) -> Text:
    text = Text()
    text.append("WIS2 Node Assessment", style="bold")
    if assessment.centre_id:
        text.append(f"  {assessment.centre_id}", style="cyan")
    text.append("\n")
    for topic, ok, detail in checklist_rows(assessment):
        text.append("  ")
        if topic == "monitor errors" and assessment.monitor_errors:
            mark, style = "[ERR]  ", "bold red"
        elif ok:
            mark, style = "[PASS] ", "bold green"
        else:
            mark, style = "[WAIT] ", "bold yellow"
        text.append(mark, style=style)
        text.append(topic)
        text.append(f"  {detail}", style="dim")
        text.append("\n")
    datasets = assessment.unique_datasets(
        assessment.origin_metadata or assessment.cache_metadata
    )
    if not datasets and assessment.gdc:
        datasets = [record.identifier.rsplit(":", 1)[-1] for record in assessment.gdc.records]
    if datasets:
        text.append("  datasets: ", style="dim")
        text.append(", ".join(datasets), style="bold")
        text.append("\n")
    caches = assessment.global_cache_ids()
    if caches:
        text.append("  global-cache: ", style="dim")
        text.append(", ".join(caches), style="magenta")
        text.append("\n")
    status = "COMPLETE" if assessment.complete else "IN PROGRESS"
    if assessment.official_complete and not assessment.complete:
        status = "CHECKS MET — MONITOR FOLLOW-UP REQUIRED"
    style = "bold green" if assessment.complete else "bold yellow"
    if assessment.monitor_errors:
        style = "bold red"
    text.append("  status: ", style="dim")
    text.append(status, style=style)
    return text


def format_connection_banner(url: str, status: str) -> Text:
    text = Text()
    text.append("Broker  ", style="dim")
    text.append(url, style="bold")
    text.append("   ")
    color = "green" if "connected" in status.lower() else "yellow"
    if "fail" in status.lower() or "error" in status.lower() or "denied" in status.lower():
        color = "red"
    text.append(status, style=color)
    return text
