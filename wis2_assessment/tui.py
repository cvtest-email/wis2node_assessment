"""Tmux-style three-pane MQTT Explorer for WIS2 Node Assessment."""

from __future__ import annotations

import threading
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Header, ListItem, ListView, Static

from .brokers import (
    STAGE_CUSTOM,
    STAGE_PRODUCTION,
    STAGE_WIS2DEV,
    WIS2DEV_HOST,
    apply_broker,
    broker_label,
    failover_stages,
    initial_stage_index,
    production_hosts,
    slot_for_host,
)
from .config import SessionConfig
from .engine import run_engine
from .formatters import (
    format_checklist,
    format_connection_banner,
    format_detail,
    format_list_item,
    format_operational_gdc,
)
from .messages import ParsedMessage
from .mqtt_client import WIS2MqttClient
from .report import write_evidence
from .store import MessageStore

MAX_VISIBLE = 400


class MessageItem(ListItem):
    def __init__(self, message: ParsedMessage, verbose: bool) -> None:
        self.message = message
        super().__init__(Static(format_list_item(message, verbose=verbose)))


class BrokerStrip(Vertical):
    def __init__(self, channel: str, slot: str, **kwargs) -> None:
        self.channel = channel
        self.slot = slot
        super().__init__(**kwargs)
        self.border_title = slot

    def compose(self) -> ComposeResult:
        yield ListView(id=f"{self.channel}-{self.slot}-list")

    def set_heading(self, title: str) -> None:
        self.border_title = title


class ChannelPane(Vertical):
    def __init__(self, channel: str, topic: str, **kwargs) -> None:
        self.channel = channel
        self.topic = topic
        super().__init__(**kwargs)
        self.border_title = f"{channel.upper()}  {topic}"

    def compose(self) -> ComposeResult:
        yield BrokerStrip(
            self.channel,
            "top",
            id=f"{self.channel}-top",
            classes="broker-strip top",
        )
        yield BrokerStrip(
            self.channel,
            "bottom",
            id=f"{self.channel}-bottom",
            classes="broker-strip bottom",
        )

    def update_title(self, count: int, status: str) -> None:
        mark = "●" if "subscribed" in status or "connected" in status else "○"
        if "denied" in status or "fail" in status:
            mark = "✖"
        self.border_title = f"{mark} {self.channel.upper()}  {self.topic}  {count}"


class WIS2AssessmentApp(App):
    TITLE = "WIS2 Node Assessment"
    CSS = """
    Screen {
        background: #1b1d23;
        layout: vertical;
    }
    Header {
        background: #11131a;
        height: 3;
        color: #f8f8f2;
    }
    HeaderIcon {
        dock: left;
        width: auto;
        min-width: 16;
        height: 3;
        margin: 0 1;
        padding: 0 1;
        background: #2c3340;
        color: #f8f8f2;
        text-style: bold;
        border: tall #6272a4;
        content-align: center middle;
    }
    HeaderIcon:hover {
        background: #3d4a6b;
        color: #8be9fd;
        border: tall #8be9fd;
    }
    HeaderClock, HeaderClockSpace {
        height: 3;
        content-align: center middle;
    }
    Footer {
        background: #11131a;
        color: #f8f8f2;
        height: 1;
    }
    FooterKey.-command-palette {
        dock: right;
        background: #2c3340;
        color: #f8f8f2;
        border-left: tall #6272a4;
        padding: 0 1;
        height: 1;
    }
    FooterKey.-command-palette .footer-key--key {
        background: #6272a4;
        color: #f8f8f2;
        text-style: bold;
        padding: 0 1;
    }
    FooterKey.-command-palette .footer-key--description {
        color: #8be9fd;
        background: #2c3340;
        padding: 0 1 0 0;
    }
    FooterKey.-command-palette:hover {
        background: #3d4a6b;
        border-left: tall #8be9fd;
    }
    #status-row {
        height: 3;
        background: #11131a;
        padding: 0 1;
    }
    #status-bar {
        width: 1fr;
        height: 3;
        padding: 1 1 0 0;
        color: #f8f8f2;
        background: #11131a;
    }
    #btn-wis2dev, #btn-production {
        margin: 0 1;
        min-width: 20;
        background: #2c3340;
        color: #f8f8f2;
        border: tall #6272a4;
        text-style: bold;
    }
    #btn-wis2dev:hover, #btn-production:hover {
        background: #3d4a6b;
        border: tall #8be9fd;
        color: #8be9fd;
    }
    Button.-primary {
        background: #3d4a6b;
        color: #8be9fd;
        border: tall #8be9fd;
        text-style: bold;
    }
    #channels {
        height: 1fr;
    }
    .pane {
        width: 1fr;
        height: 1fr;
        border: tall #3d4450;
        background: #14161c;
    }
    .pane.origin { border-title-color: #8be9fd; }
    .pane.cache { border-title-color: #ff79c6; }
    .pane.monitor { border-title-color: #f1fa8c; }
    .broker-strip {
        height: 1fr;
        border: tall #2a2d34;
    }
    .pane.single .broker-strip.bottom {
        display: none;
        height: 0;
    }
    .pane.dual .broker-strip.top { border-title-color: #8be9fd; }
    .pane.dual .broker-strip.bottom { border-title-color: #50fa7b; }
    ListView {
        background: #14161c;
        height: 1fr;
    }
    ListItem {
        height: auto;
        padding: 0 1;
        border-bottom: solid #2a2d34;
    }
    ListView > ListItem.--highlight {
        background: #2c3340;
    }
    #lower {
        height: 22;
    }
    #detail-pane {
        width: 3fr;
        height: 1fr;
        border: tall #6272a4;
        border-title-color: #6272a4;
        background: #14161c;
    }
    #checklist-pane {
        width: 2fr;
        height: 1fr;
        border: tall #50fa7b;
        border-title-color: #50fa7b;
        background: #14161c;
    }
    #ops-pane {
        width: 2fr;
        height: 1fr;
        border: tall #ffb86c;
        border-title-color: #ffb86c;
        background: #1c1812;
    }
    #detail, #checklist, #ops {
        padding: 0 1;
        height: auto;
        min-height: 100%;
    }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "write_report", "Report"),
        Binding("s", "save_evidence", "Save"),
        Binding("v", "toggle_verbose", "Verbose"),
        Binding("g", "refresh_gdc", "WIS2Dev GDC"),
        Binding("c", "refresh_ops", "Ops GDC"),
        Binding("p", "switch_production", "Production WIS"),
        Binding("d", "switch_wis2dev", "WIS2Dev"),
        Binding("1", "focus_channel('origin')", "Origin", show=False),
        Binding("2", "focus_channel('cache')", "Cache", show=False),
        Binding("3", "focus_channel('monitor')", "Monitor", show=False),
    ]

    def __init__(self, config: SessionConfig, auto_connect: bool = True) -> None:
        super().__init__()
        self.config = config
        self.store = MessageStore()
        self.verbose = config.verbose
        self.mqtt: WIS2MqttClient | None = None
        self._mqtt_clients: list[WIS2MqttClient] = []
        self.auto_connect = auto_connect
        self._counts = {"origin": 0, "cache": 0, "monitor": 0}
        self._slot_counts = {
            "origin": {"top": 0, "bottom": 0},
            "cache": {"top": 0, "bottom": 0},
            "monitor": {"top": 0, "bottom": 0},
        }
        self._sub_status = {"origin": "idle", "cache": "idle", "monitor": "idle"}
        self._conn_status = "disconnected"
        self._selected: ParsedMessage | None = None
        self._output_dir = None
        self._closing = False
        self._gdc_busy = False
        self._ops_busy = False
        self._stages = failover_stages(config)
        self._stage_index = initial_stage_index(config)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, icon="Palette")
        with Horizontal(id="status-row"):
            yield Static(id="status-bar")
            yield Button("WIS2Dev", id="btn-wis2dev")
            yield Button("Production WIS", id="btn-production")
        with Horizontal(id="channels"):
            for channel, topic in self.config.topics().items():
                yield ChannelPane(
                    channel,
                    topic,
                    id=f"{channel}-pane",
                    classes=f"pane {channel} single",
                )
        with Horizontal(id="lower"):
            with VerticalScroll(id="detail-pane") as detail_pane:
                detail_pane.border_title = "MESSAGE DETAIL"
                yield Static(id="detail")
            with VerticalScroll(id="checklist-pane") as checklist_pane:
                checklist_pane.border_title = "WIS2DEV ASSESSMENT  ·  gdc.wis2dev.io"
                yield Static(id="checklist")
            with VerticalScroll(id="ops-pane") as ops_pane:
                ops_pane.border_title = "OPERATIONAL GDC  ·  NOT WIS2DEV"
                yield Static(id="ops")
        yield Footer()

    def on_mount(self) -> None:
        self._update_banner()
        self._refresh_checklist()
        self._refresh_ops()
        if not self.auto_connect:
            return
        self.action_refresh_ops()
        if not self.config.skip_gdc:
            self.action_refresh_gdc()
            self.set_interval(60, self.action_refresh_gdc)
        self._start_mqtt()
        self._refresh_switch_buttons()

    def on_unmount(self) -> None:
        self._closing = True
        self._stop_mqtt()

    def _call_ui(self, method, *args) -> None:
        if self._closing or not self.is_running:
            return
        try:
            app_thread = getattr(self, "_thread_id", None)
            on_app_thread = app_thread is None or threading.get_ident() == app_thread
            if on_app_thread:
                method(*args)
            else:
                self.call_from_thread(method, *args)
        except RuntimeError:
            if self._closing or not self.is_running:
                return
            try:
                method(*args)
            except Exception:
                pass
        except Exception:
            pass

    def _current_stage(self) -> str:
        if 0 <= self._stage_index < len(self._stages):
            return self._stages[self._stage_index]
        return STAGE_WIS2DEV

    def _current_broker_label(self) -> str:
        stage = self._current_stage()
        if stage == STAGE_PRODUCTION:
            return "Production (France + Brazil)"
        if stage == STAGE_WIS2DEV:
            return broker_label(WIS2DEV_HOST)
        return broker_label(self.config.host)

    def _set_pane_mode(self, dual: bool) -> None:
        for channel in ("origin", "cache", "monitor"):
            try:
                pane = self.query_one(f"#{channel}-pane", ChannelPane)
            except Exception:
                continue
            if dual:
                pane.add_class("dual")
                pane.remove_class("single")
            else:
                pane.add_class("single")
                pane.remove_class("dual")
            if dual:
                hosts = production_hosts()
            elif self._current_stage() == STAGE_CUSTOM:
                hosts = [(broker_label(self.config.host), self.config.host)]
            else:
                hosts = [("WIS2Dev", WIS2DEV_HOST)]
            top_name, top_host = hosts[0]
            try:
                pane.query_one(f"#{channel}-top", BrokerStrip).set_heading(
                    f"{top_name}  {top_host}"
                )
            except Exception:
                pass
            if dual and len(hosts) > 1:
                bottom_name, bottom_host = hosts[1]
                try:
                    pane.query_one(f"#{channel}-bottom", BrokerStrip).set_heading(
                        f"{bottom_name}  {bottom_host}"
                    )
                except Exception:
                    pass

    def _update_banner(self) -> None:
        try:
            self.query_one("#status-bar", Static).update(
                format_connection_banner(
                    self.config.broker_url(),
                    self._conn_status,
                    self._current_broker_label(),
                )
            )
        except Exception:
            return

    def _start_mqtt(self) -> None:
        self._stop_mqtt()
        stage = self._current_stage()
        dual = stage == STAGE_PRODUCTION
        self._set_pane_mode(dual)
        if dual:
            apply_broker(self.config, production_hosts()[0][1])
            targets = production_hosts()
        else:
            if stage == STAGE_WIS2DEV:
                apply_broker(self.config, WIS2DEV_HOST)
            targets = [(self._current_broker_label(), self.config.host)]
        for _label, host in targets:
            client = WIS2MqttClient(
                self.config,
                self._on_mqtt_message,
                lambda kind, text, bound=host: self._on_mqtt_status_from(kind, text, bound),
                host=host,
            )
            self._mqtt_clients.append(client)
            try:
                client.start()
            except Exception as exc:
                self._conn_status = f"{host}: connect failed: {exc}"
                self.notify(str(exc), severity="error")
        self.mqtt = self._mqtt_clients[0] if self._mqtt_clients else None
        if dual:
            self._conn_status = (
                "connecting to globalbroker.meteo.fr and globalbroker.inmet.gov.br"
            )
        self._update_banner()
        self._refresh_switch_buttons()

    def _on_mqtt_status_from(self, kind: str, text: str, host: str) -> None:
        self._call_ui(self.handle_status, kind, text, host)

    def _stop_mqtt(self) -> None:
        clients = list(self._mqtt_clients)
        self._mqtt_clients = []
        self.mqtt = None
        for client in clients:
            try:
                client.stop()
            except Exception:
                pass

    def _switch_stage(self, index: int, reason: str) -> None:
        if index < 0 or index >= len(self._stages):
            return
        if index == self._stage_index and self._mqtt_clients:
            return
        self._stage_index = index
        stage = self._current_stage()
        if stage == STAGE_PRODUCTION:
            apply_broker(self.config, production_hosts()[0][1])
        elif stage == STAGE_WIS2DEV:
            apply_broker(self.config, WIS2DEV_HOST)
        self._sub_status = {"origin": "switching", "cache": "switching", "monitor": "switching"}
        self._conn_status = f"switching to {self._current_broker_label()}"
        self._update_banner()
        self.notify(f"{reason} {self._current_broker_label()}", timeout=8)
        self._start_mqtt()
        self._refresh_switch_buttons()

    def _refresh_switch_buttons(self) -> None:
        stage = self._current_stage()
        try:
            wis2 = self.query_one("#btn-wis2dev", Button)
            prod = self.query_one("#btn-production", Button)
        except Exception:
            return
        wis2.variant = "primary" if stage == STAGE_WIS2DEV else "default"
        prod.variant = "primary" if stage == STAGE_PRODUCTION else "default"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-production":
            self.action_switch_production()
        elif event.button.id == "btn-wis2dev":
            self.action_switch_wis2dev()

    def action_switch_production(self) -> None:
        if STAGE_PRODUCTION not in self._stages:
            self._stages = [STAGE_WIS2DEV, STAGE_PRODUCTION]
        index = self._stages.index(STAGE_PRODUCTION)
        self._switch_stage(index, "Switched to production WIS.")

    def action_switch_wis2dev(self) -> None:
        if STAGE_WIS2DEV not in self._stages:
            self._stages = [STAGE_WIS2DEV, STAGE_PRODUCTION]
        index = self._stages.index(STAGE_WIS2DEV)
        self._switch_stage(index, "Switched to WIS2Dev.")

    def _on_mqtt_message(self, message: ParsedMessage) -> None:
        self._call_ui(self.handle_message, message)

    def _on_mqtt_status(self, kind: str, text: str) -> None:
        self._call_ui(self.handle_status, kind, text, self.config.host)

    def _refresh_checklist(self) -> None:
        try:
            engine = run_engine(self.store, self.config, probe_http=False, live_session=True)
            self.query_one("#checklist", Static).update(
                format_checklist(self.store.evaluate(self.config.centre_id), engine)
            )
        except Exception:
            return

    def handle_status(self, kind: str, text: str, host: str = "") -> None:
        if self._closing:
            return
        prefix = f"{host}: " if host else ""
        display = prefix + text
        if kind == "connection":
            self._conn_status = display
            self.store.set_connection(display)
        elif kind == "subscription":
            lowered = text.lower()
            for channel in ("origin", "cache", "monitor"):
                if text.startswith(channel) or f"{channel}:" in lowered:
                    self._sub_status[channel] = display
                    self.store.set_subscription(channel, display)
                    try:
                        pane = self.query_one(f"#{channel}-pane", ChannelPane)
                        pane.update_title(self._counts[channel], display)
                    except Exception:
                        pass
                    break
        self._update_banner()
        self._refresh_checklist()
        if "denied" in text.lower() or "fail" in text.lower():
            self.notify(display, severity="error")

    def handle_message(self, message: ParsedMessage) -> None:
        if self._closing:
            return
        self.store.add(message)
        channel = message.channel if message.channel in self._counts else "origin"
        slot = slot_for_host(message.broker_host) if message.broker_host else "top"
        if self._current_stage() != STAGE_PRODUCTION:
            slot = "top"
        self._counts[channel] = self._counts.get(channel, 0) + 1
        self._slot_counts[channel][slot] = self._slot_counts.get(channel, {}).get(slot, 0) + 1
        try:
            list_view = self.query_one(f"#{channel}-{slot}-list", ListView)
        except Exception:
            try:
                list_view = self.query_one(f"#{channel}-top-list", ListView)
            except Exception:
                return
        list_view.append(MessageItem(message, verbose=self.verbose))
        while len(list_view) > MAX_VISIBLE:
            try:
                list_view.remove_items([0])
            except Exception:
                break
        if list_view.index is None and len(list_view.children):
            list_view.index = 0
        try:
            pane = self.query_one(f"#{channel}-pane", ChannelPane)
            pane.update_title(self._counts[channel], self._sub_status.get(channel, ""))
        except Exception:
            pass
        self._refresh_checklist()
        if message.channel == "monitor" and message.is_monitor_error():
            self.notify(message.monitor_title() or "Monitor error", severity="error")
        if self._selected is None:
            self._selected = message
            self.query_one("#detail", Static).update(format_detail(message))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, MessageItem):
            self._selected = item.message
            self.query_one("#detail", Static).update(format_detail(item.message))

    def action_focus_channel(self, channel: str) -> None:
        self.query_one(f"#{channel}-top-list", ListView).focus()

    def _refresh_ops(self) -> None:
        try:
            checks = self.store.global_caches
            self.query_one("#ops", Static).update(
                format_operational_gdc(self.config.centre_id, checks)
            )
        except Exception:
            return

    def action_refresh_gdc(self) -> None:
        if self._gdc_busy:
            return
        self._gdc_busy = True
        threading.Thread(target=self._gdc_thread, daemon=True).start()

    def action_refresh_ops(self) -> None:
        if self._ops_busy:
            return
        self._ops_busy = True
        threading.Thread(target=self._ops_thread, daemon=True).start()

    def _gdc_thread(self) -> None:
        from .gdc import query_gdc

        try:
            snapshot = query_gdc(self.config.centre_id, self.config.gdc_url)
        except Exception as exc:
            self._call_ui(self._gdc_failed, str(exc))
            return
        self._call_ui(self.apply_gdc, snapshot)

    def _ops_thread(self) -> None:
        from .gdc import query_global_caches

        try:
            checks = query_global_caches(self.config.centre_id)
        except Exception as exc:
            self._call_ui(self._ops_failed, str(exc))
            return
        self._call_ui(self.apply_ops, checks)

    def _gdc_failed(self, detail: str) -> None:
        self._gdc_busy = False
        if self._closing:
            return
        self.notify(f"WIS2Dev GDC query failed: {detail}", severity="error")

    def _ops_failed(self, detail: str) -> None:
        self._ops_busy = False
        if self._closing:
            return
        self.notify(f"Operational GDC query failed: {detail}", severity="error")

    def apply_gdc(self, snapshot) -> None:
        self._gdc_busy = False
        if self._closing:
            return
        self.store.set_gdc(snapshot)
        try:
            self._refresh_checklist()
        except Exception:
            return
        if snapshot.ok:
            self.notify(f"WIS2Dev GDC: {len(snapshot.records)} WCMP2 record(s)", timeout=4)
        else:
            self.notify(snapshot.error or "WIS2Dev GDC: no WCMP2 records yet", severity="warning")

    def apply_ops(self, checks) -> None:
        self._ops_busy = False
        if self._closing:
            return
        self.store.set_global_caches(checks)
        try:
            self._refresh_ops()
        except Exception:
            return
        parts = []
        any_error = False
        for check in checks:
            if check.snapshot.ok:
                parts.append(f"{check.name} {len(check.snapshot.records)}")
            else:
                any_error = True
                parts.append(f"{check.name} 0")
        severity = "warning" if any_error else "information"
        self.notify("Operational GDC (not WIS2Dev): " + " · ".join(parts), timeout=6, severity=severity)

    def action_toggle_verbose(self) -> None:
        self.verbose = not self.verbose
        self.notify("Verbose summaries on" if self.verbose else "Compact summaries on")

    def action_write_report(self) -> None:
        self._save(notify_report=True)

    def action_save_evidence(self) -> None:
        self._save(notify_report=False)

    def _save(self, notify_report: bool) -> None:
        directory = write_evidence(self.store, self.config)
        self._output_dir = directory
        report_path = directory / "REPORT.txt"
        if notify_report:
            self.notify(f"Report written to {report_path}", timeout=6)
        else:
            self.notify(f"Evidence saved to {directory}", timeout=6)

    def action_quit(self) -> None:
        if self.store.snapshot() and self._output_dir is None:
            try:
                self._output_dir = write_evidence(self.store, self.config)
            except OSError:
                pass
        self.exit()
