"""MQTT client for simultaneous origin, cache, and monitor subscriptions."""

from __future__ import annotations

import ssl
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .config import SessionConfig, default_ca_bundle
from .messages import ParsedMessage, parse_mqtt_message

StatusCallback = Callable[[str, str], None]
MessageCallback = Callable[[ParsedMessage], None]


class WIS2MqttClient:
    def __init__(
        self,
        config: SessionConfig,
        on_message: MessageCallback,
        on_status: StatusCallback | None = None,
    ) -> None:
        self.config = config
        self.on_message = on_message
        self.on_status = on_status or (lambda _kind, _text: None)
        self._client: Any = None
        self._lock = threading.Lock()
        self._subscribe_mids: dict[int, tuple[str, str]] = {}
        self._stopped = threading.Event()

    def _emit(self, kind: str, text: str) -> None:
        if self._stopped.is_set():
            return
        self.on_status(kind, text)

    def start(self) -> None:
        import paho.mqtt.client as mqtt

        client_id = self.config.client_id or f"wis2-assess-{uuid.uuid4().hex[:10]}"
        kwargs: dict[str, Any] = {
            "client_id": client_id,
            "protocol": mqtt.MQTTv311,
            "clean_session": True,
        }
        callback_api = getattr(mqtt, "CallbackAPIVersion", None)
        if callback_api is not None:
            kwargs["callback_api_version"] = callback_api.VERSION2
            self._api_v2 = True
        else:
            self._api_v2 = False

        client = mqtt.Client(**kwargs)
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password)

        if self.config.use_tls:
            if self.config.tls_insecure:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                client.tls_set_context(context)
            else:
                cafile = self.config.cafile or default_ca_bundle() or None
                tls_kwargs: dict[str, Any] = {
                    "tls_version": ssl.PROTOCOL_TLS_CLIENT,
                    "cert_reqs": ssl.CERT_REQUIRED,
                }
                if cafile:
                    tls_kwargs["ca_certs"] = cafile
                client.tls_set(**tls_kwargs)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        self._client = client
        self._stopped.clear()
        self._emit("connection", f"connecting to {self.config.broker_url()}")
        client.connect_async(self.config.host, int(self.config.port), keepalive=60)
        client.loop_start()

    def stop(self) -> None:
        self._stopped.set()
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass

    def _channel_for_topic(self, topic: str) -> str:
        first = topic.strip("/").split("/", 1)[0]
        if first in {"origin", "cache", "monitor"}:
            return first
        for channel, subscribed in self.config.topics().items():
            prefix = subscribed.rstrip("#").rstrip("/")
            if topic == subscribed or topic.startswith(prefix):
                return channel
        return first or "other"

    def _on_connect(self, client: Any, _userdata: Any, flags: Any, *args: Any) -> None:
        reason = args[0] if args else flags
        failed = False
        text = str(reason)
        if hasattr(reason, "is_failure"):
            failed = bool(reason.is_failure)
            text = str(reason)
        elif isinstance(reason, int):
            failed = reason != 0
            text = f"rc={reason}"
        if failed:
            self._emit("connection", f"connect failed: {text}")
            return
        self._emit("connection", "connected")
        for channel, topic in self.config.topics().items():
            if not topic:
                continue
            result, mid = client.subscribe(topic, qos=1)
            if result != 0:
                self._emit("subscription", f"{channel} subscribe failed ({topic})")
            else:
                with self._lock:
                    self._subscribe_mids[mid] = (channel, topic)
                self._emit("subscription", f"{channel} subscribing {topic}")

    def _on_disconnect(self, _client: Any, _userdata: Any, *args: Any) -> None:
        if self._stopped.is_set():
            return
        extra = args[0] if args else ""
        self._emit("connection", f"disconnected ({extra}); reconnecting")

    def _on_subscribe(self, _client: Any, _userdata: Any, mid: int, *args: Any) -> None:
        with self._lock:
            channel, topic = self._subscribe_mids.get(mid, ("", ""))
        reason_codes = args[0] if args else []
        denied = False
        if isinstance(reason_codes, list):
            for code in reason_codes:
                if hasattr(code, "is_failure") and code.is_failure:
                    denied = True
                elif isinstance(code, int) and code >= 128:
                    denied = True
        elif isinstance(reason_codes, int) and reason_codes >= 128:
            denied = True
        if denied:
            self._emit("subscription", f"{channel or topic}: subscription denied ({topic})")
        else:
            self._emit("subscription", f"{channel}: subscribed {topic}")

    def _on_message(self, _client: Any, _userdata: Any, msg: Any) -> None:
        try:
            topic = getattr(msg, "topic", "")
            channel = self._channel_for_topic(topic)
            parsed = parse_mqtt_message(
                channel=channel,
                topic=topic,
                payload=msg.payload,
                retained=bool(getattr(msg, "retain", False)),
                qos=int(getattr(msg, "qos", 0) or 0),
                received_at=datetime.now(timezone.utc),
            )
            self.on_message(parsed)
        except Exception as exc:
            self._emit("error", f"failed to parse MQTT payload: {exc}")
