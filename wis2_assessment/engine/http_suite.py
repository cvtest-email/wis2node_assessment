"""HTTP / data-server checks on canonical URLs from captured notifications."""

from __future__ import annotations

import hashlib
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..messages import ParsedMessage
from .results import (
    CONFORMANCE,
    FAIL,
    INTEROPERABILITY,
    NOT_OBSERVED,
    PASS,
    PERFORMANCE,
    QUALITY,
    SKIP,
    WARNING,
    TestResult,
    make_result,
)

DEFAULT_TIMEOUT = 15
DEFAULT_MAX_URLS = 8
HASH_LIMIT = 10 * 1024 * 1024
DOWNLOAD_LIMIT = 2 * 1024 * 1024
SLOW_SECONDS = 5.0
USER_AGENT = "wis2-node-assessment/1.2"


@dataclass
class Probe:
    url: str
    topic: str
    message_id: str
    integrity_method: str = ""
    integrity_value: str = ""
    ok_dns: bool = False
    dns_error: str = ""
    scheme: str = ""
    host: str = ""
    tls_ok: bool | None = None
    tls_error: str = ""
    status: int | None = None
    content_type: str = ""
    content_length: str = ""
    redirected: bool = False
    redirect_chain: list[str] = field(default_factory=list)
    final_url: str = ""
    downloaded: bool = False
    body: bytes = b""
    latency_s: float | None = None
    http_error: str = ""
    hash_hex: str = ""
    hash_match: bool | None = None


def _unique_targets(messages: list[ParsedMessage], limit: int) -> list[Probe]:
    seen: set[str] = set()
    probes: list[Probe] = []
    for msg in messages:
        href = msg.canonical_href()
        if not href or href in seen:
            continue
        seen.add(href)
        integrity = msg.properties().get("integrity")
        method = ""
        value = ""
        if isinstance(integrity, dict):
            method = str(integrity.get("method") or "").lower()
            value = str(integrity.get("value") or "")
        probes.append(
            Probe(
                url=href,
                topic=msg.topic,
                message_id=msg.message_id(),
                integrity_method=method,
                integrity_value=value,
            )
        )
        if len(probes) >= limit:
            break
    return probes


def _dns(probe: Probe) -> None:
    parsed = urlparse(probe.url)
    probe.scheme = (parsed.scheme or "").lower()
    probe.host = parsed.hostname or ""
    if not probe.host:
        probe.dns_error = "URL has no host"
        return
    try:
        socket.getaddrinfo(probe.host, parsed.port or None)
        probe.ok_dns = True
    except OSError as exc:
        probe.dns_error = str(exc)


def _tls(probe: Probe, timeout: int) -> None:
    if probe.scheme != "https":
        probe.tls_ok = None
        return
    if not probe.host:
        probe.tls_ok = False
        probe.tls_error = "no host"
        return
    parsed = urlparse(probe.url)
    port = parsed.port or 443
    try:
        context = ssl.create_default_context()
        with socket.create_connection((probe.host, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=probe.host) as ssock:
                cert = ssock.getpeercert()
        probe.tls_ok = bool(cert)
    except ssl.SSLError as exc:
        probe.tls_ok = False
        probe.tls_error = str(exc)
    except OSError as exc:
        probe.tls_ok = False
        probe.tls_error = str(exc)


def _http(probe: Probe, timeout: int) -> None:
    request = Request(
        probe.url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        method="GET",
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            probe.latency_s = time.monotonic() - started
            probe.status = int(getattr(response, "status", 0) or response.getcode() or 0)
            probe.final_url = str(getattr(response, "url", "") or probe.url)
            headers = response.headers
            probe.content_type = headers.get("Content-Type", "")
            probe.content_length = headers.get("Content-Length", "")
            probe.redirected = probe.final_url.rstrip("/") != probe.url.rstrip("/")
            if probe.redirected:
                probe.redirect_chain = [probe.url, probe.final_url]
            limit = HASH_LIMIT if probe.integrity_value else DOWNLOAD_LIMIT
            probe.body = response.read(limit + 1)
            probe.downloaded = True
    except HTTPError as exc:
        probe.latency_s = time.monotonic() - started
        probe.status = int(exc.code)
        probe.http_error = f"HTTP {exc.code}"
        probe.final_url = str(getattr(exc, "url", "") or probe.url)
    except (URLError, TimeoutError, OSError) as exc:
        probe.latency_s = time.monotonic() - started
        probe.http_error = str(getattr(exc, "reason", exc))
    if probe.body:
        if probe.integrity_method.startswith("sha") and probe.integrity_value:
            digest = hashlib.new(
                probe.integrity_method.replace("sha3-", "sha3_")
                if probe.integrity_method.startswith("sha3-")
                else probe.integrity_method,
                probe.body[:HASH_LIMIT],
            )
            # WNM integrity value is base64 of the raw digest.
            import base64

            probe.hash_hex = digest.hexdigest()
            try:
                expected = base64.b64decode(probe.integrity_value)
                probe.hash_match = expected == digest.digest()
            except Exception:
                probe.hash_match = False


def probe_urls(probes: list[Probe], timeout: int = DEFAULT_TIMEOUT) -> list[Probe]:
    for probe in probes:
        _dns(probe)
        if probe.ok_dns:
            _tls(probe, timeout)
            _http(probe, timeout)
    return probes


def _summarize(probes: list[Probe], attr: str, ok_pred) -> tuple[str, str, dict[str, Any]]:
    failures = [item.url for item in probes if not ok_pred(item)]
    received = f"{len(probes) - len(failures)}/{len(probes)} URLs ok"
    evidence = {
        "urls": [
            {
                "url": item.url,
                "topic": item.topic,
                "message_id": item.message_id,
                attr: getattr(item, attr),
                "error": item.http_error or item.dns_error or item.tls_error,
                "status": item.status,
                "latency_s": item.latency_s,
            }
            for item in probes
        ],
        "failed": failures[:8],
    }
    return received, (failures[0] if failures else received), evidence


def run_http_tests(
    origin_messages: list[ParsedMessage],
    cache_messages: list[ParsedMessage],
    *,
    enabled: bool = True,
    max_urls: int = DEFAULT_MAX_URLS,
    timeout: int = DEFAULT_TIMEOUT,
    prober=None,
) -> list[TestResult]:
    if not enabled:
        return [
            make_result(
                test_id,
                "WIS2 data server",
                title,
                INTEROPERABILITY,
                SKIP,
                received="HTTP probing disabled",
            )
            for test_id, title in (
                ("HTTP-001", "URL resolves"),
                ("HTTP-002", "HTTPS certificate valid"),
                ("HTTP-003", "HTTP response status"),
                ("HTTP-004", "Content-Type"),
                ("HTTP-005", "Content-Length"),
                ("HTTP-006", "Resource downloadable"),
                ("HTTP-007", "Redirect behaviour"),
                ("HTTP-008", "Canonical URL"),
                ("HTTP-009", "Integrity/hash"),
                ("HTTP-010", "Response latency"),
            )
        ]

    targets = _unique_targets(origin_messages, max_urls)
    if not targets:
        empty = [
            make_result(
                test_id,
                "WIS2 data server",
                title,
                INTEROPERABILITY,
                NOT_OBSERVED,
                expected="canonical href on origin notifications",
                received="no canonical URLs captured yet",
                recommendation="Wait for origin metadata/data notifications that include a canonical link.",
            )
            for test_id, title in (
                ("HTTP-001", "URL resolves"),
                ("HTTP-002", "HTTPS certificate valid"),
                ("HTTP-003", "HTTP response status"),
                ("HTTP-004", "Content-Type"),
                ("HTTP-005", "Content-Length"),
                ("HTTP-006", "Resource downloadable"),
                ("HTTP-007", "Redirect behaviour"),
                ("HTTP-008", "Canonical URL"),
                ("HTTP-009", "Integrity/hash"),
                ("HTTP-010", "Response latency"),
            )
        ]
        return empty

    probes = (prober or probe_urls)(targets, timeout)

    def result(
        test_id: str,
        title: str,
        classification: str,
        expected: str,
        pred,
        *,
        fail_when_empty: bool = True,
        warning_pred=None,
        recommendation: str = "",
        skip_pred=None,
    ) -> TestResult:
        if skip_pred and all(skip_pred(item) for item in probes):
            return make_result(
                test_id,
                "WIS2 data server",
                title,
                classification,
                SKIP,
                expected=expected,
                received="not applicable for these URLs",
            )
        bad = [item for item in probes if not pred(item)]
        warn = [item for item in probes if warning_pred and warning_pred(item)] if warning_pred else []
        received, first, evidence = _summarize(probes, "url", pred)
        if bad and fail_when_empty:
            return make_result(
                test_id,
                "WIS2 data server",
                title,
                classification,
                FAIL,
                expected=expected,
                received=first,
                evidence=evidence,
                recommendation=recommendation or "Correct the data-server URL or TLS/HTTP configuration.",
            )
        if warn:
            return make_result(
                test_id,
                "WIS2 data server",
                title,
                QUALITY if classification == CONFORMANCE else classification,
                WARNING,
                expected=expected,
                received=f"{len(warn)} URL(s) have warnings",
                evidence=evidence,
                recommendation=recommendation,
            )
        return make_result(
            test_id,
            "WIS2 data server",
            title,
            classification,
            PASS,
            expected=expected,
            received=received,
            evidence=evidence,
        )

    https_probes = [item for item in probes if item.scheme == "https"]
    results = [
        result(
            "HTTP-001",
            "URL resolves",
            CONFORMANCE,
            "canonical host resolves in DNS",
            lambda p: p.ok_dns,
            recommendation="The canonical host must resolve. Check DNS and the href.",
        ),
        make_result(
            "HTTP-002",
            "WIS2 data server / TLS",
            "HTTPS certificate valid",
            CONFORMANCE,
            FAIL
            if any(item.scheme == "https" and item.tls_ok is False for item in probes)
            else (
                WARNING
                if any(item.scheme == "http" for item in probes)
                else (PASS if https_probes else SKIP)
            ),
            expected="HTTPS with a valid certificate",
            received=(
                "HTTP (not HTTPS) used"
                if any(item.scheme == "http" for item in probes)
                else f"{sum(1 for item in https_probes if item.tls_ok)}/{len(https_probes)} certificates ok"
            ),
            evidence={"tls_errors": [item.tls_error for item in probes if item.tls_error][:6]},
            recommendation="WIS2 prefers HTTPS. Fix the certificate or publish an https:// canonical link.",
        ),
        result(
            "HTTP-003",
            "HTTP response status",
            CONFORMANCE,
            "HTTP 2xx",
            lambda p: p.status is not None and 200 <= p.status < 300,
            recommendation="The canonical URL must return HTTP 200–299 for a GISC download check.",
        ),
        result(
            "HTTP-004",
            "Content-Type",
            QUALITY,
            "Content-Type response header present",
            lambda p: True,
            warning_pred=lambda p: p.status and 200 <= p.status < 300 and not p.content_type,
            fail_when_empty=False,
            recommendation="Set a correct Content-Type on the data object.",
        ),
        result(
            "HTTP-005",
            "Content-Length",
            QUALITY,
            "Content-Length response header present",
            lambda p: True,
            warning_pred=lambda p: p.status and 200 <= p.status < 300 and not p.content_length,
            fail_when_empty=False,
            recommendation="Content-Length helps consumers and matches WNM link length when present.",
        ),
        result(
            "HTTP-006",
            "Resource downloadable",
            INTEROPERABILITY,
            "GET returns a body",
            lambda p: p.downloaded and bool(p.body),
            recommendation="The data object must be downloadable from the canonical URL without extra steps.",
        ),
        result(
            "HTTP-007",
            "Redirect behaviour",
            INTEROPERABILITY,
            "stable canonical URL; short HTTPS redirect chain if any",
            lambda p: p.status is not None and p.status < 400,
            warning_pred=lambda p: p.redirected,
            fail_when_empty=False,
            recommendation="Prefer a stable canonical URL. Note any redirects in the evidence.",
        ),
        result(
            "HTTP-008",
            "Canonical URL",
            CONFORMANCE,
            "absolute http(s) or ftp(s) URL",
            lambda p: p.scheme in {"http", "https", "ftp", "sftp"} and bool(p.host),
            recommendation="canonical href must be an absolute URL with a supported scheme.",
        ),
        make_result(
            "HTTP-009",
            "WIS2 data server / integrity",
            "Integrity/hash",
            QUALITY,
            (
                FAIL
                if any(item.hash_match is False for item in probes)
                else (
                    PASS
                    if any(item.hash_match is True for item in probes)
                    else WARNING
                )
            ),
            expected="WNM integrity matches the downloaded object when provided",
            received=(
                "no integrity block on sampled notifications"
                if all(not item.integrity_value for item in probes)
                else f"matched={sum(1 for item in probes if item.hash_match)} "
                f"failed={sum(1 for item in probes if item.hash_match is False)}"
            ),
            recommendation="Add properties.integrity or correct the hash if it does not match the file.",
        ),
        make_result(
            "HTTP-010",
            "WIS2 data server / latency",
            "Response latency",
            PERFORMANCE,
            WARNING
            if any((item.latency_s or 0) > SLOW_SECONDS for item in probes)
            else (PASS if any(item.latency_s is not None for item in probes) else NOT_OBSERVED),
            expected=f"response faster than {SLOW_SECONDS:.0f}s (warning only)",
            received=", ".join(
                f"{item.latency_s:.2f}s" for item in probes if item.latency_s is not None
            )
            or "no timings",
            recommendation="Slow downloads are recorded as performance warnings, not conformance failures.",
        ),
    ]

    return results
