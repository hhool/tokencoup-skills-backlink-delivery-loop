"""Central configuration. Every value comes from the environment and is validated once."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .errors import ConfigError

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}
_LEVELS = {"DEBUG", "INFO", "WARN", "ERROR"}


def _as_bool(raw: str, name: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError("Value must be a boolean", env=name, value=raw)


def _path(home: Path, key: str, default: str, env: Mapping[str, str]) -> Path:
    raw = Path(env.get(key, default)).expanduser()
    return raw.resolve() if raw.is_absolute() else (home / raw).resolve()


@dataclass(frozen=True)
class Config:
    home: Path
    snapshot_dir: Path
    prose_dir: Path
    out_dir: Path
    history_dir: Path
    templates_dir: Path
    observation_dir: Path
    records_dir: Path
    forms_dir: Path
    center_domain: str
    own_site_base: str
    tier: str
    auto_transmit: bool
    auto_confirm: bool
    log_level: str
    deferral_window_days: int = 90
    daily_ceiling: int = 100
    receipt_window_days: int = 30
    contact_lost_days: int = 180
    provider: str = ""
    provider_base_url: str = ""
    provider_key_present: bool = False
    own_landing_url: str = ""
    from_email: str = ""
    from_name: str = ""

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        source = env if env is not None else os.environ
        home = Path(source.get("BDL_HOME", ".")).expanduser().resolve()

        tier = source.get("BDL_TIER", "B").strip().upper()
        if tier not in {"A", "B"}:
            raise ConfigError("Tier must be A or B", env="BDL_TIER", value=tier)

        auto = _as_bool(source.get("BDL_AUTO_TRANSMIT", "false"), "BDL_AUTO_TRANSMIT")
        if auto:
            raise ConfigError(
                "Automatic transmission is disabled: class U targets require human review",
                env="BDL_AUTO_TRANSMIT",
                rule="R-520/R-530",
            )

        auto_confirm = _as_bool(source.get("BDL_AUTO_CONFIRM", "false"), "BDL_AUTO_CONFIRM")

        level = source.get("BDL_LOG_LEVEL", "INFO").strip().upper()
        if level not in _LEVELS:
            raise ConfigError("Unsupported log level", env="BDL_LOG_LEVEL", value=level)

        deferral = int(source.get("BDL_DEFERRAL_WINDOW_DAYS", "90")) if source.get("BDL_DEFERRAL_WINDOW_DAYS") else 90
        if deferral < 0:
            raise ConfigError("Deferral window must be non-negative", env="BDL_DEFERRAL_WINDOW_DAYS", value=str(deferral))
        ceiling = int(source.get("BDL_DAILY_CEILING", "100")) if source.get("BDL_DAILY_CEILING") else 100
        if ceiling < 1:
            raise ConfigError("Daily ceiling must be positive", env="BDL_DAILY_CEILING", value=str(ceiling))

        receipt = int(source.get("BDL_RECEIPT_WINDOW_DAYS", "30")) if source.get("BDL_RECEIPT_WINDOW_DAYS") else 30
        if receipt < 1:
            raise ConfigError(
                "Receipt window must be at least one day",
                env="BDL_RECEIPT_WINDOW_DAYS",
                value=str(receipt),
            )
        lost = int(source.get("BDL_CONTACT_LOST_DAYS", "180")) if source.get("BDL_CONTACT_LOST_DAYS") else 180
        if lost < 1:
            raise ConfigError(
                "Contact-lost cooldown must be at least one day",
                env="BDL_CONTACT_LOST_DAYS",
                value=str(lost),
            )

        center = source.get("BDL_CENTER_DOMAIN", "").strip().lower()
        base = source.get("BDL_OWN_SITE_BASE", "").strip().rstrip("/")
        if not base and center:
            base = "https://" + center

        provider = source.get("BDL_PROVIDER", "").strip().lower()
        provider_base = source.get("BDL_PROVIDER_BASE_URL", "").strip().rstrip("/")
        # One page the whole pool should link to. Without it every row stalls on a
        # value that is identical across the pool.
        from_email = source.get("BDL_FROM_EMAIL", "").strip()
        from_name = source.get("BDL_FROM_NAME", "").strip()
        own_landing = source.get("BDL_OWN_LANDING_URL", "").strip()
        if not own_landing and base:
            own_landing = base
        if own_landing and not own_landing.startswith(("http://", "https://")):
            raise ConfigError(
                "Own landing URL must be absolute", env="BDL_OWN_LANDING_URL", value=own_landing
            )
        # The key itself never enters the config object: a frozen dataclass ends up
        # in logs and JSON dumps, and a receipt poll only needs to know it exists.
        key_present = bool(source.get("BDL_PROVIDER_API_KEY", "").strip())

        return cls(
            home=home,
            snapshot_dir=_path(home, "BDL_SNAPSHOT_DIR", "data/snapshots", source),
            prose_dir=_path(home, "BDL_PROSE_DIR", "data/prose", source),
            out_dir=_path(home, "BDL_OUT_DIR", "data/out", source),
            history_dir=_path(home, "BDL_HISTORY_DIR", "data/history", source),
            templates_dir=_path(home, "BDL_TEMPLATES_DIR", "data/templates", source),
            observation_dir=_path(home, "BDL_OBSERVATION_DIR", "data/observations", source),
            records_dir=_path(home, "BDL_RECORDS_DIR", "data/records", source),
            forms_dir=_path(home, "BDL_FORMS_DIR", "data/forms", source),
            center_domain=center,
            own_site_base=base,
            tier=tier,
            auto_transmit=auto,
            auto_confirm=auto_confirm,
            log_level=level,
            deferral_window_days=deferral,
            daily_ceiling=ceiling,
            receipt_window_days=receipt,
            contact_lost_days=lost,
            provider=provider,
            provider_base_url=provider_base,
            provider_key_present=key_present,
            own_landing_url=own_landing,
            from_email=from_email,
            from_name=from_name,
        )
