"""Interactive setup wizard and command-line entry point."""

from __future__ import annotations

import argparse
import sys
from getpass import getpass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from . import __version__
from .config import (
    DEFAULT_HOST,
    DEFAULT_PASSWORD,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    SessionConfig,
    apply_parsed_command,
    centre_from_topic,
    load_saved_config,
    looks_like_mosquitto_command,
    normalize_centre_id,
    parse_mosquitto_command,
    save_config,
    strip_leading_slash,
    topics_for_centre,
)
from .report import load_jsonl, write_evidence
from .store import MessageStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wis2_assess",
        description=(
            "WIS2 Node Assessment console for GISC. Subscribes to origin, cache and "
            "monitor on the WIS2Dev Global Broker, checks WCMP2 in the Global Discovery "
            "Catalogue, and writes the official assessment report."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default=None, help="Broker host (mosquitto_sub -h)")
    parser.add_argument("--port", type=int, default=None, help="Broker port (mosquitto_sub -p)")
    parser.add_argument("--username", "--user", dest="username", default=None, help="Username (mosquitto_sub -u)")
    parser.add_argument("--password", default=None, help="Password (mosquitto_sub -P)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose message summaries (mosquitto_sub -v)")
    parser.add_argument(
        "--centre",
        "--center",
        "--centre-id",
        "--center-id",
        dest="centre_id",
        default=None,
        help="WIS2 centre-id of the node under assessment (the only node-specific value required)",
    )
    parser.add_argument("--origin-topic", default=None, help="Origin subscription topic")
    parser.add_argument("--cache-topic", default=None, help="Cache subscription topic")
    parser.add_argument("--monitor-topic", default=None, help="Monitor subscription topic")
    parser.add_argument("--origin-cmd", default=None, help="Full mosquitto_sub command for origin")
    parser.add_argument("--cache-cmd", default=None, help="Full mosquitto_sub command for cache")
    parser.add_argument("--monitor-cmd", default=None, help="Full mosquitto_sub command for monitor")
    tls = parser.add_mutually_exclusive_group()
    tls.add_argument("--tls", dest="use_tls", action="store_true", help="Enable TLS")
    tls.add_argument("--no-tls", dest="use_tls", action="store_false", help="Disable TLS")
    parser.set_defaults(use_tls=None)
    parser.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification")
    parser.add_argument("--cafile", default=None, help="CA bundle for TLS")
    parser.add_argument("--gisc", default=None, help="GISC name used in the generated report")
    parser.add_argument("--gdc-url", default=None, help="GDC items URL")
    parser.add_argument("--skip-gdc", action="store_true", help="Do not query the Global Discovery Catalogue")
    parser.add_argument(
        "--skip-http",
        action="store_true",
        help="Do not probe canonical data-server URLs when writing the report",
    )
    parser.add_argument("-o", "--output-dir", default=None, help="Directory for reports and evidence")
    parser.add_argument("--headless", action="store_true", help="Collect without the interactive TUI")
    parser.add_argument("--duration", type=int, default=0, help="Headless collection time in seconds (0 = until Ctrl+C)")
    parser.add_argument("--from-jsonl", default=None, help="Generate a report from a previously captured messages.jsonl")
    parser.add_argument("--no-save-config", action="store_true", help="Do not remember last-used settings")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _ask(label: str, default: str, console: Console) -> str:
    suffix = f" [bold]{default}[/]" if default else ""
    value = Prompt.ask(f"{label}{suffix}", default=default or None, show_default=False, console=console)
    return (value or default or "").strip()


def _ask_centre_id(console: Console, current: str = "") -> str:
    console.print()
    console.print("[bold]WIS2 centre-id[/]  (the only node-specific value required)")
    console.print("  Topics will be built as:")
    console.print("    origin/a/wis2/[cyan]<centre-id>[/]/#")
    console.print("    cache/a/wis2/[cyan]<centre-id>[/]/#")
    console.print("    monitor/a/wis2/[cyan]<centre-id>[/]")
    while True:
        raw = _ask("  centre-id", current, console)
        if looks_like_mosquitto_command(raw):
            parsed = parse_mosquitto_command(raw)
            centre = normalize_centre_id(str(parsed.get("topic") or ""))
        else:
            centre = normalize_centre_id(raw)
        if centre and centre not in {"<centre-id>", "<center_id>", "centre-id", "center_id"}:
            return centre
        console.print("[red]  Required. Use the WIS2 centre-id, for example au-bom or jp-jma.[/]")


def _fill_from_saved(config: SessionConfig, saved: dict) -> None:
    config.host = saved.get("host") or config.host
    config.port = int(saved.get("port") or config.port)
    config.username = saved.get("username") or config.username
    config.verbose = bool(saved.get("verbose", config.verbose))
    config.use_tls = bool(saved.get("use_tls", config.use_tls))
    config.tls_insecure = bool(saved.get("tls_insecure", config.tls_insecure))
    config.cafile = saved.get("cafile") or config.cafile
    config.gdc_url = saved.get("gdc_url") or config.gdc_url
    config.output_dir = saved.get("output_dir") or config.output_dir


def _apply_cli(config: SessionConfig, args: argparse.Namespace) -> None:
    if args.origin_cmd:
        apply_parsed_command(config, parse_mosquitto_command(args.origin_cmd), "origin")
    if args.cache_cmd:
        apply_parsed_command(config, parse_mosquitto_command(args.cache_cmd), "cache")
    if args.monitor_cmd:
        apply_parsed_command(config, parse_mosquitto_command(args.monitor_cmd), "monitor")
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.username:
        config.username = args.username
    if args.password is not None:
        config.password = args.password
    if args.centre_id:
        config.centre_id = normalize_centre_id(args.centre_id)
    if args.origin_topic:
        config.origin_topic = strip_leading_slash(args.origin_topic)
    if args.cache_topic:
        config.cache_topic = strip_leading_slash(args.cache_topic)
    if args.monitor_topic:
        config.monitor_topic = strip_leading_slash(args.monitor_topic)
    if args.verbose:
        config.verbose = True
    if args.use_tls is not None:
        config.use_tls = args.use_tls
    if args.insecure:
        config.tls_insecure = True
    if args.cafile:
        config.cafile = args.cafile
    if args.gisc:
        config.gisc = args.gisc
    if args.gdc_url:
        config.gdc_url = args.gdc_url
    if args.skip_gdc:
        config.skip_gdc = True
    if args.skip_http:
        config.skip_http = True
    if args.output_dir:
        config.output_dir = args.output_dir
    config.apply_centre_defaults()


def _maybe_parse_field(config: SessionConfig, value: str, channel: str | None = None) -> str:
    if looks_like_mosquitto_command(value):
        parsed = parse_mosquitto_command(value)
        apply_parsed_command(config, parsed, channel)
        return str(parsed.get("topic") or "")
    return strip_leading_slash(value)


def run_wizard(config: SessionConfig, console: Console) -> SessionConfig:
    console.print()
    console.print(
        Panel.fit(
            Text.from_markup(
                "[bold]WIS2 Node Assessment[/] for GISC approval\n"
                "Works with any WIS2 Node / wis2box. Enter the node's [cyan]centre-id[/].\n"
                "Broker [cyan]mqtts://gb.wis2dev.io:8883[/]  "
                "user/password [cyan]everyone/everyone[/]  "
                "GDC [cyan]https://gdc.wis2dev.io[/]\n"
                "[dim]Publish metadata first, then data. Allow ~30 minutes for GDC/GB metadata checks.[/]"
            ),
            border_style="cyan",
        )
    )
    config.centre_id = _ask_centre_id(console, config.centre_id)
    built = topics_for_centre(config.centre_id)
    config.origin_topic = built["origin"]
    config.cache_topic = built["cache"]
    config.monitor_topic = built["monitor"]
    console.print()
    console.print("[bold]Subscriptions[/]  (built from centre-id)")
    console.print(f"  (1) origin   {config.origin_topic}")
    console.print(f"  (2) cache    {config.cache_topic}")
    console.print(f"  (3) monitor  {config.monitor_topic}")
    if Confirm.ask("  Edit these topics?", default=False, console=console):
        origin_in = _ask("  origin  (-t)", config.origin_topic, console)
        parsed_topic = _maybe_parse_field(config, origin_in, "origin")
        if parsed_topic:
            config.origin_topic = parsed_topic
        elif origin_in:
            config.origin_topic = strip_leading_slash(origin_in)
        cache_in = _ask("  cache   (-t)", config.cache_topic, console)
        parsed_topic = _maybe_parse_field(config, cache_in, "cache")
        if parsed_topic:
            config.cache_topic = parsed_topic
        elif cache_in:
            config.cache_topic = strip_leading_slash(cache_in)
        monitor_in = _ask("  monitor (-t)", config.monitor_topic, console)
        parsed_topic = _maybe_parse_field(config, monitor_in, "monitor")
        if parsed_topic:
            config.monitor_topic = parsed_topic
        elif monitor_in:
            config.monitor_topic = strip_leading_slash(monitor_in)
        extracted = (
            centre_from_topic(config.origin_topic)
            or centre_from_topic(config.cache_topic)
            or centre_from_topic(config.monitor_topic)
        )
        if extracted:
            config.centre_id = extracted

    console.print()
    console.print("[bold]Connection[/]  (same flags as mosquitto_sub; WIS2Dev defaults)")

    host_in = _ask("  -h  host", config.host or DEFAULT_HOST, console)
    if looks_like_mosquitto_command(host_in):
        apply_parsed_command(config, parse_mosquitto_command(host_in), "origin")
    else:
        config.host = host_in or DEFAULT_HOST

    port_in = _ask("  -p  port", str(config.port or DEFAULT_PORT), console)
    if looks_like_mosquitto_command(port_in):
        apply_parsed_command(config, parse_mosquitto_command(port_in))
    else:
        try:
            config.port = int(port_in or DEFAULT_PORT)
        except ValueError:
            config.port = DEFAULT_PORT

    user_in = _ask("  -u  username", config.username or DEFAULT_USERNAME, console)
    if looks_like_mosquitto_command(user_in):
        apply_parsed_command(config, parse_mosquitto_command(user_in))
    else:
        config.username = user_in or DEFAULT_USERNAME

    console.print("  -P  password  [hidden, Enter keeps default 'everyone']")
    password = getpass("      password: ")
    if looks_like_mosquitto_command(password):
        apply_parsed_command(config, parse_mosquitto_command(password))
    elif password:
        config.password = password
    else:
        config.password = config.password or DEFAULT_PASSWORD

    config.verbose = Confirm.ask("  -v  verbose summaries", default=config.verbose, console=console)
    config.use_tls = Confirm.ask(
        "      use TLS (mqtts)",
        default=bool(config.use_tls or config.port == 8883),
        console=console,
    )
    if config.use_tls:
        config.tls_insecure = Confirm.ask(
            "      skip certificate verification (--insecure)",
            default=config.tls_insecure,
            console=console,
        )

    console.print()
    console.print("[bold]GISC[/]  (who is performing this assessment)")
    console.print("  Type the GISC name. There is no default.")
    while True:
        gisc = _ask("  GISC name", "", console)
        if gisc:
            config.gisc = gisc
            break
        console.print("[red]  Required. Example: GISC Exeter, GISC Offenbach, GISC Melbourne.[/]")
    config.apply_centre_defaults()
    return config


def _summary_table(config: SessionConfig) -> Table:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("k", style="bold cyan")
    table.add_column("v")
    table.add_row("broker", config.broker_url())
    table.add_row("username", config.username)
    table.add_row("centre-id", config.centre_id)
    table.add_row("origin", config.origin_topic)
    table.add_row("cache", config.cache_topic)
    table.add_row("monitor", config.monitor_topic)
    table.add_row("GDC", config.gdc_url)
    table.add_row("verbose", "yes" if config.verbose else "no")
    table.add_row("TLS verify", "off" if config.tls_insecure else "on" if config.use_tls else "n/a")
    table.add_row("GISC", config.gisc)
    return table


def run_headless(config: SessionConfig, duration: int, console: Console) -> int:
    from .formatters import format_list_item
    from .mqtt_client import WIS2MqttClient
    from .store import MessageStore

    store = MessageStore()
    console.print("[bold]Collecting MQTT notifications[/]  (Ctrl+C to write the report)")

    def on_message(message) -> None:
        store.add(message)
        console.print(f"[{message.channel}] ", end="")
        console.print(format_list_item(message, verbose=config.verbose))

    def on_status(kind: str, text: str) -> None:
        if kind == "connection":
            store.set_connection(text)
        elif kind == "subscription":
            lowered = text.lower()
            for channel in ("origin", "cache", "monitor"):
                if text.startswith(channel) or f"{channel}:" in lowered:
                    store.set_subscription(channel, text)
                    break
        style = "red" if "fail" in text.lower() or "denied" in text.lower() else "dim"
        console.print(f"{kind}: {text}", style=style)

    client = WIS2MqttClient(config, on_message, on_status)
    client.start()
    try:
        if duration > 0:
            import time

            time.sleep(duration)
        else:
            import time

            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        console.print("\nStopping…")
    finally:
        client.stop()
    directory = write_evidence(store, config)
    console.print(f"[green]Report:[/] {directory / 'REPORT.txt'}")
    console.print(f"[green]Evidence:[/] {directory}")
    return 0


def run_from_jsonl(path: Path, config: SessionConfig, console: Console) -> int:
    store = MessageStore()
    for message in load_jsonl(path):
        store.add(message)
        if not config.centre_id:
            config.centre_id = centre_from_topic(message.topic)
    config.apply_centre_defaults()
    directory = write_evidence(store, config)
    report = (directory / "REPORT.txt").read_text(encoding="utf-8")
    console.print(Panel(report, title=f"Report — {config.centre_id}", border_style="green"))
    console.print(f"[green]Written:[/] {directory}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()
    saved = load_saved_config()
    config = SessionConfig()
    if saved:
        _fill_from_saved(config, saved)
    _apply_cli(config, args)

    if args.from_jsonl:
        path = Path(args.from_jsonl)
        if not path.is_file():
            console.print(f"[red]File not found:[/] {path}")
            return 2
        return run_from_jsonl(path, config, console)

    interactive = sys.stdin.isatty() and not args.headless
    if interactive and not (args.host and (args.centre_id or args.origin_cmd)):
        config = run_wizard(config, console)
    else:
        if not config.centre_id and config.origin_topic:
            config.centre_id = centre_from_topic(config.origin_topic)
        config.apply_centre_defaults()
        if not config.password:
            config.password = DEFAULT_PASSWORD

    if not config.centre_id or not config.origin_topic:
        console.print("[red]centre-id and origin topic are required.[/]")
        return 2

    if config.port == 8883 and args.use_tls is None:
        config.use_tls = True

    console.print()
    console.print(Panel(_summary_table(config), title="Session", border_style="cyan"))
    if interactive and not args.headless:
        if not Confirm.ask("Connect and open the live console?", default=True, console=console):
            return 0

    if not args.no_save_config:
        try:
            save_config(config)
        except OSError:
            pass

    if args.headless:
        return run_headless(config, args.duration, console)

    try:
        from .tui import WIS2AssessmentApp
    except ImportError as exc:
        console.print(
            "[red]The interactive console needs the 'textual' package.[/]\n"
            "Install with:  pip install -r requirements.txt\n"
            f"Details: {exc}"
        )
        return 1

    app = WIS2AssessmentApp(config)
    app.run()
    return 0
