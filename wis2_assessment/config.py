"""Connection settings and mosquitto_sub command parsing."""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_HOST = "gb.wis2dev.io"
DEFAULT_PORT = 8883
DEFAULT_USERNAME = "everyone"
DEFAULT_PASSWORD = "everyone"
DEFAULT_GISC = ""
DEFAULT_GDC_URL = "https://gdc.wis2dev.io/collections/wis2-discovery-metadata/items"

CONFIG_PATH = Path.home() / ".config" / "wis2-assess.json"

MOSQUITTO_FLAG_MAP = {
    "-h": "host",
    "--host": "host",
    "-p": "port",
    "--port": "port",
    "-u": "username",
    "--username": "username",
    "-P": "password",
    "--pw": "password",
    "-t": "topic",
    "--topic": "topic",
    "--cafile": "cafile",
}


@dataclass
class SessionConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    username: str = DEFAULT_USERNAME
    password: str = DEFAULT_PASSWORD
    centre_id: str = ""
    origin_topic: str = ""
    cache_topic: str = ""
    monitor_topic: str = ""
    verbose: bool = True
    use_tls: bool = True
    tls_insecure: bool = False
    cafile: str = ""
    gisc: str = DEFAULT_GISC
    gdc_url: str = DEFAULT_GDC_URL
    skip_gdc: bool = False
    output_dir: str = ""
    client_id: str = ""

    def topics(self) -> dict[str, str]:
        return {
            "origin": self.origin_topic,
            "cache": self.cache_topic,
            "monitor": self.monitor_topic,
        }

    def apply_centre_defaults(self) -> None:
        centre = normalize_centre_id(self.centre_id)
        self.centre_id = centre
        if centre:
            built = topics_for_centre(centre)
            if not self.origin_topic:
                self.origin_topic = built["origin"]
            if not self.cache_topic:
                self.cache_topic = built["cache"]
            if not self.monitor_topic:
                self.monitor_topic = built["monitor"]

    def scheme(self) -> str:
        return "mqtts" if self.use_tls else "mqtt"

    def broker_url(self) -> str:
        return f"{self.scheme()}://{self.host}:{self.port}"

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["password"] = "********" if self.password else ""
        return data


def strip_leading_slash(topic: str) -> str:
    """Global brokers deny subscriptions that start with '/'."""
    return (topic or "").strip().lstrip("/")


def looks_like_mosquitto_command(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    first = shlex.split(stripped)[0] if stripped else ""
    return first.startswith("mosquitto_sub") or " -t " in stripped or stripped.startswith("-")


def parse_mosquitto_command(line: str) -> dict[str, Any]:
    """Parse a mosquitto_sub command line or a bare topic string."""
    raw = (line or "").strip()
    if not raw:
        return {}
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()
    if not tokens:
        return {}

    if tokens[0] in {"mosquitto_sub", "mosquitto_sub.exe"}:
        tokens = tokens[1:]

    if len(tokens) == 1 and not tokens[0].startswith("-"):
        return {"topic": strip_leading_slash(tokens[0])}

    result: dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"-v", "--verbose"}:
            result["verbose"] = True
            i += 1
            continue
        if token in {"--insecure"}:
            result["tls_insecure"] = True
            i += 1
            continue
        if token in {"--cafile"} and i + 1 < len(tokens):
            result["cafile"] = tokens[i + 1]
            i += 2
            continue
        if token in MOSQUITTO_FLAG_MAP and i + 1 < len(tokens):
            key = MOSQUITTO_FLAG_MAP[token]
            value: Any = tokens[i + 1]
            if key == "port":
                try:
                    value = int(value)
                except ValueError:
                    pass
            elif key == "topic":
                value = strip_leading_slash(str(value))
            result[key] = value
            i += 2
            continue
        i += 1
    return result


def apply_parsed_command(config: SessionConfig, parsed: dict[str, Any], channel: str | None = None) -> None:
    if "host" in parsed:
        config.host = str(parsed["host"])
    if "port" in parsed:
        config.port = int(parsed["port"])
    if "username" in parsed:
        config.username = str(parsed["username"])
    if "password" in parsed:
        config.password = str(parsed["password"])
    if parsed.get("verbose"):
        config.verbose = True
    if parsed.get("tls_insecure"):
        config.tls_insecure = True
    if parsed.get("cafile"):
        config.cafile = str(parsed["cafile"])
    topic = parsed.get("topic")
    if topic and channel:
        setattr(config, f"{channel}_topic", strip_leading_slash(str(topic)))
        centre = centre_from_topic(str(topic))
        if centre and not config.centre_id:
            config.centre_id = centre


def centre_from_topic(topic: str) -> str:
    parts = strip_leading_slash(topic).split("/")
    if len(parts) >= 4 and parts[1] == "a" and parts[2] == "wis2":
        return parts[3]
    return ""


def normalize_centre_id(value: str) -> str:
    """Accept a centre-id, or extract it from a pasted WIS2 topic/command."""
    raw = (value or "").strip()
    if not raw:
        return ""
    if looks_like_mosquitto_command(raw):
        parsed = parse_mosquitto_command(raw)
        raw = str(parsed.get("topic") or raw)
    extracted = centre_from_topic(raw)
    if extracted:
        return extracted
    return raw.strip("/").lower()


def topics_for_centre(centre_id: str) -> dict[str, str]:
    centre = normalize_centre_id(centre_id)
    return {
        "origin": f"origin/a/wis2/{centre}/#",
        "cache": f"cache/a/wis2/{centre}/#",
        "monitor": f"monitor/a/wis2/{centre}",
    }


def load_saved_config() -> dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def save_config(config: SessionConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "centre_id": config.centre_id,
        "origin_topic": config.origin_topic,
        "cache_topic": config.cache_topic,
        "monitor_topic": config.monitor_topic,
        "verbose": config.verbose,
        "use_tls": config.use_tls,
        "tls_insecure": config.tls_insecure,
        "cafile": config.cafile,
        "gisc": config.gisc,
        "gdc_url": config.gdc_url,
        "output_dir": config.output_dir,
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def default_ca_bundle() -> str:
    candidates = [
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
        "/etc/ssl/certs/ca-certificates.crt",
    ]
    for path in candidates:
        if Path(path).is_file():
            return path
    return ""
