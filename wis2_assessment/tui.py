"""Tmux-style three-pane MQTT Explorer for WIS2 Node Assessment."""

from __future__ import annotations

import threading
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, ListItem, ListView, Static

from .config import SessionConfig
from .engine import run_engine
from .formatters import (
    format_checklist,
    format_connection_banner,
    format_detail,
    format_list_item,
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


class ChannelPane(Vertical):
    def __init__(self, channel: str, topic: str, **kwargs) -> None:
        self.channel = channel
        self.topic = topic
        super().__init__(**kwargs)
        self.border_title = f"{channel.upper()}  {topic}"

    def compose(self) -> ComposeResult:
        yield ListView(id=f"{self.channel}-list")

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
    }
    Footer {
        background: #11131a;
    }
    #status-bar {
        height: 1;
        padding: 0 1;
        color: #f8f8f2;
        background: #11131a;
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
        height: 18;
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
    #detail, #checklist {
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
        Binding("g", "refresh_gdc", "GDC"),
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
        self.auto_connect = auto_connect
        self._counts = {"origin": 0, "cache": 0, "monitor": 0}
        self._sub_status = {"origin": "idle", "cache": "idle", "monitor": "idle"}
        self._conn_status = "disconnected"
        self._selected: ParsedMessage | None = None
        self._output_dir = None
        self._closing = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="status-bar")
        with Horizontal(id="channels"):
            for channel, topic in self.config.topics().items():
                yield ChannelPane(
                    channel,
                    topic,
                    id=f"{channel}-pane",
                    classes=f"pane {channel}",
                )
        with Horizontal(id="lower"):
            with VerticalScroll(id="detail-pane") as detail_pane:
                detail_pane.border_title = "MESSAGE DETAIL"
                yield Static(id="detail")
            with VerticalScroll(id="checklist-pane") as checklist_pane:
                checklist_pane.border_title = "ASSESSMENT ENGINE"
                yield Static(id="checklist")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#status-bar", Static).update(
            format_connection_banner(self.config.broker_url(), "connecting")
        )
        self._refresh_checklist()
        if not self.auto_connect:
            return
        if not self.config.skip_gdc:
            self.action_refresh_gdc()
            self.set_interval(60, self.action_refresh_gdc)
        self.mqtt = WIS2MqttClient(self.config, self._on_mqtt_message, self._on_mqtt_status)
        try:
            self.mqtt.start()
        except Exception as exc:
            self._conn_status = f"connect failed: {exc}"
            self.query_one("#status-bar", Static).update(
                format_connection_banner(self.config.broker_url(), self._conn_status)
            )
            self.notify(str(exc), severity="error")

    def on_unmount(self) -> None:
        self._closing = True
        mqtt = self.mqtt
        self.mqtt = None
        if mqtt is not None:
            try:
                mqtt.stop()
            except Exception:
                pass

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

    def _on_mqtt_message(self, message: ParsedMessage) -> None:
        self._call_ui(self.handle_message, message)

    def _on_mqtt_status(self, kind: str, text: str) -> None:
        self._call_ui(self.handle_status, kind, text)

    def _refresh_checklist(self) -> None:
        try:
            engine = run_engine(self.store, self.config, probe_http=False, live_session=True)
            self.query_one("#checklist", Static).update(
                format_checklist(self.store.evaluate(self.config.centre_id), engine)
            )
        except Exception:
            return

    def handle_status(self, kind: str, text: str) -> None:
        if self._closing:
            return
        if kind == "connection":
            self._conn_status = text
            self.store.set_connection(text)
        elif kind == "subscription":
            lowered = text.lower()
            for channel in ("origin", "cache", "monitor"):
                if text.startswith(channel) or f"{channel}:" in lowered:
                    self._sub_status[channel] = text
                    self.store.set_subscription(channel, text)
                    try:
                        pane = self.query_one(f"#{channel}-pane", ChannelPane)
                        pane.update_title(self._counts[channel], text)
                    except Exception:
                        pass
                    break
        try:
            self.query_one("#status-bar", Static).update(
                format_connection_banner(self.config.broker_url(), self._conn_status)
            )
        except Exception:
            return
        self._refresh_checklist()
        if "denied" in text.lower() or "fail" in text.lower():
            self.notify(text, severity="error")

    def handle_message(self, message: ParsedMessage) -> None:
        if self._closing:
            return
        self.store.add(message)
        channel = message.channel if message.channel in self._counts else "origin"
        self._counts[channel] = self._counts.get(channel, 0) + 1
        try:
            list_view = self.query_one(f"#{channel}-list", ListView)
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
        pane = self.query_one(f"#{channel}-pane", ChannelPane)
        pane.update_title(self._counts[channel], self._sub_status.get(channel, ""))
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
        self.query_one(f"#{channel}-list", ListView).focus()

    def action_refresh_gdc(self) -> None:
        threading.Thread(target=self._gdc_thread, daemon=True).start()

    def _gdc_thread(self) -> None:
        from .gdc import query_gdc

        snapshot = query_gdc(self.config.centre_id, self.config.gdc_url)
        self._call_ui(self.apply_gdc, snapshot)

    def apply_gdc(self, snapshot) -> None:
        if self._closing:
            return
        self.store.set_gdc(snapshot)
        try:
            self._refresh_checklist()
        except Exception:
            return
        if snapshot.ok:
            self.notify(f"GDC: {len(snapshot.records)} WCMP2 record(s)", timeout=4)
        else:
            self.notify(snapshot.error or "GDC: no WCMP2 records yet", severity="warning")

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
