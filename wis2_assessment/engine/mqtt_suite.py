"""MQTT broker checks for the broker this session actually connected to."""

from __future__ import annotations

from .results import (
    CONFORMANCE,
    FAIL,
    INTEROPERABILITY,
    NOT_OBSERVED,
    PASS,
    SKIP,
    WARNING,
    TestResult,
    make_result,
)


def _status_blob(connection: dict[str, str], subscriptions: dict[str, str]) -> str:
    parts = [f"broker={connection.get('broker', '') or 'n/a'}"]
    for channel in ("origin", "cache", "monitor"):
        parts.append(f"{channel}={subscriptions.get(channel, '') or 'n/a'}")
    return "; ".join(parts)


def run_mqtt_tests(
    *,
    connection: dict[str, str],
    subscriptions: dict[str, str],
    use_tls: bool,
    tls_insecure: bool,
    username: str,
    live_session: bool,
) -> list[TestResult]:
    blob = _status_blob(connection, subscriptions)
    broker = (connection.get("broker") or "").lower()

    if not live_session and not broker:
        skip = [
            make_result(
                test_id,
                "WIS2 Message Broker",
                title,
                INTEROPERABILITY,
                SKIP,
                received="replay / no live MQTT session",
                recommendation="Connect to a broker to assess MQTT-001–004.",
            )
            for test_id, title in (
                ("MQTT-001", "Broker reachable"),
                ("MQTT-002", "TLS available"),
                ("MQTT-003", "Authentication"),
                ("MQTT-004", "Subscription succeeds"),
            )
        ]
        return skip

    if "fail" in broker or "error" in broker:
        reach = FAIL
        rec_reach = "The session did not connect. Check host, port, TLS, and credentials."
    elif "connected" in broker:
        reach = PASS
        rec_reach = ""
    elif "disconnect" in broker:
        reach = WARNING
        rec_reach = "The broker was reached but later disconnected."
    else:
        reach = NOT_OBSERVED
        rec_reach = "Still connecting, or no connection status recorded."

    results = [
        make_result(
            "MQTT-001",
            "WIS2 Message Broker / reachability",
            "Broker reachable",
            INTEROPERABILITY,
            reach,
            expected="MQTT(S) CONNECT succeeds",
            received=connection.get("broker") or "no status",
            recommendation=rec_reach,
        )
    ]

    if reach == NOT_OBSERVED:
        tls_result = NOT_OBSERVED
        tls_rec = "TLS is assessed after a successful connection."
    elif not use_tls:
        tls_result = WARNING
        tls_rec = "WIS2 Global Brokers use MQTTS on 8883. This session did not use TLS."
    elif tls_insecure:
        tls_result = WARNING
        tls_rec = "TLS was used but certificate verification was disabled (--insecure)."
    elif reach == FAIL:
        tls_result = FAIL
        tls_rec = "TLS was requested but the broker was not reached."
    else:
        tls_result = PASS
        tls_rec = ""
    results.append(
        make_result(
            "MQTT-002",
            "WIS2 Message Broker / TLS",
            "TLS available",
            CONFORMANCE if use_tls else INTEROPERABILITY,
            tls_result,
            expected="mqtts / TLS 1.2+ with a verified certificate",
            received=f"use_tls={use_tls} insecure={tls_insecure}",
            recommendation=tls_rec,
        )
    )

    if reach == FAIL:
        auth = FAIL
        auth_rec = "Authentication could not be completed because CONNECT failed."
    elif reach == NOT_OBSERVED:
        auth = NOT_OBSERVED
        auth_rec = ""
    elif username:
        auth = PASS
        auth_rec = ""
    else:
        auth = WARNING
        auth_rec = "No username was supplied. WIS2 brokers normally require everyone/everyone or node credentials."
    results.append(
        make_result(
            "MQTT-003",
            "WIS2 Message Broker / authentication",
            "Authentication",
            INTEROPERABILITY,
            auth,
            expected="username/password accepted",
            received=f"username={username or '(none)'}; {connection.get('broker') or ''}",
            recommendation=auth_rec,
        )
    )

    channels = ("origin", "cache", "monitor")
    denied = []
    subscribed = []
    pending = []
    for channel in channels:
        text = (subscriptions.get(channel) or "").lower()
        if "denied" in text or "fail" in text:
            denied.append(channel)
        elif "subscribed" in text:
            subscribed.append(channel)
        else:
            pending.append(channel)
    if denied:
        sub = FAIL
        rec = f"Subscription failed for: {', '.join(denied)}."
    elif len(subscribed) == 3:
        sub = PASS
        rec = ""
    elif subscribed and not denied:
        sub = WARNING
        rec = f"Not all topics confirmed: pending {', '.join(pending)}."
    else:
        sub = NOT_OBSERVED
        rec = "No successful subscriptions recorded yet."
    results.append(
        make_result(
            "MQTT-004",
            "WIS2 Message Broker / subscribe",
            "Subscription succeeds",
            INTEROPERABILITY,
            sub,
            expected="QoS 1 subscribe to origin, cache, and monitor",
            received=blob,
            evidence={"subscribed": subscribed, "denied": denied, "pending": pending},
            recommendation=rec,
        )
    )
    return results
