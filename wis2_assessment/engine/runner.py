"""Run Phase 1 test suites against a live or replayed assessment session."""

from __future__ import annotations

from ..config import SessionConfig
from ..store import Assessment, MessageStore
from .gdc_suite import run_gdc_tests
from .http_suite import DEFAULT_MAX_URLS, DEFAULT_TIMEOUT, run_http_tests
from .mqtt_suite import run_mqtt_tests
from .results import EngineReport
from .wnm import run_wnm_tests
from .wth import run_wth_tests


def run_engine(
    store: MessageStore,
    config: SessionConfig,
    *,
    probe_http: bool = False,
    live_session: bool | None = None,
) -> EngineReport:
    assessment = store.evaluate(config.centre_id)
    return run_engine_from_assessment(
        assessment,
        config,
        store=store,
        probe_http=probe_http,
        live_session=live_session,
    )


def run_engine_from_assessment(
    assessment: Assessment,
    config: SessionConfig,
    *,
    store: MessageStore | None = None,
    probe_http: bool = False,
    live_session: bool | None = None,
) -> EngineReport:
    connection = dict(store.connection_status) if store else {}
    subscriptions = dict(store.subscription_status) if store else {}
    if live_session is None:
        live_session = bool(connection.get("broker"))

    if store:
        monitor_messages = [msg for msg in store.snapshot() if msg.channel == "monitor"]
    else:
        monitor_messages = assessment.monitor_errors + assessment.monitor_other

    gdc_ids = [record.identifier for record in assessment.gdc.records] if assessment.gdc else []

    results = []
    results.extend(
        run_mqtt_tests(
            connection=connection,
            subscriptions=subscriptions,
            use_tls=config.use_tls,
            tls_insecure=config.tls_insecure,
            username=config.username,
            live_session=live_session,
        )
    )
    results.extend(
        run_wth_tests(
            centre_id=config.centre_id or assessment.centre_id,
            origin_topic=config.origin_topic,
            cache_topic=config.cache_topic,
            monitor_topic=config.monitor_topic,
            origin_metadata=assessment.origin_metadata,
            origin_data=assessment.origin_data,
            cache_metadata=assessment.cache_metadata,
            cache_data=assessment.cache_data,
            cache_data_core=assessment.cache_data_core,
            monitor_messages=monitor_messages,
            gdc_identifiers=gdc_ids,
        )
    )
    wnm_messages = (
        assessment.origin_metadata
        + assessment.origin_data
        + assessment.cache_metadata
        + assessment.cache_data
    )
    results.extend(run_wnm_tests(wnm_messages))
    results.extend(
        run_gdc_tests(
            centre_id=config.centre_id or assessment.centre_id,
            snapshot=assessment.gdc,
            skip_gdc=config.skip_gdc,
            ets_ok=assessment.ets_ok,
            ets_count=len(assessment.ets_reports),
            monitor_errors=len(assessment.monitor_errors),
        )
    )
    skip_http = probe_http is False or getattr(config, "skip_http", False)
    results.extend(
        run_http_tests(
            assessment.origin_metadata + assessment.origin_data,
            assessment.cache_metadata + assessment.cache_data_core,
            enabled=not skip_http,
            max_urls=getattr(config, "http_max_urls", DEFAULT_MAX_URLS),
            timeout=getattr(config, "http_timeout", DEFAULT_TIMEOUT),
        )
    )
    return EngineReport(results=results)
