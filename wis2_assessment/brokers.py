"""WIS2Dev vs operational Global Brokers."""

from __future__ import annotations

from .config import DEFAULT_HOST, SessionConfig

WIS2DEV_HOST = DEFAULT_HOST
WIS2DEV_LABEL = "WIS2Dev"

STAGE_WIS2DEV = "wis2dev"
STAGE_PRODUCTION = "production"
STAGE_CUSTOM = "custom"

# Same public everyone/everyone MQTTS:8883 access as gb.wis2dev.io.
# France is shown on the top of each pane; Brazil on the bottom.
PRODUCTION_GLOBAL_BROKERS: tuple[tuple[str, str], ...] = (
    ("France", "globalbroker.meteo.fr"),
    ("Brazil", "globalbroker.inmet.gov.br"),
)


def broker_label(host: str) -> str:
    host = (host or "").lower()
    if host == WIS2DEV_HOST:
        return WIS2DEV_LABEL
    for name, production in PRODUCTION_GLOBAL_BROKERS:
        if host == production:
            return f"Production ({name})"
    return host or "broker"


def is_wis2dev_host(host: str) -> bool:
    return (host or "").lower() == WIS2DEV_HOST


def production_hosts() -> list[tuple[str, str]]:
    return [(name, host) for name, host in PRODUCTION_GLOBAL_BROKERS]


def slot_for_host(host: str) -> str:
    """top = France / WIS2Dev; bottom = Brazil."""
    host = (host or "").lower()
    if host == PRODUCTION_GLOBAL_BROKERS[1][1]:
        return "bottom"
    return "top"


def failover_stages(config: SessionConfig) -> list[str]:
    """Available broker views. Switching between WIS2Dev and production is manual."""
    stages = [STAGE_WIS2DEV, STAGE_PRODUCTION]
    if config.broker_profile == "production":
        return stages
    if config.host and not is_wis2dev_host(config.host):
        return [STAGE_CUSTOM, *stages]
    return stages


def initial_stage_index(config: SessionConfig) -> int:
    stages = failover_stages(config)
    if config.broker_profile == "production" and STAGE_PRODUCTION in stages:
        return stages.index(STAGE_PRODUCTION)
    return 0


def apply_broker(config: SessionConfig, host: str) -> None:
    config.host = host
    config.port = config.port or 8883
    if config.port == 8883:
        config.use_tls = True
    config.client_id = ""
