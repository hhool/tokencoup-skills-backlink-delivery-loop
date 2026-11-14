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
    center_domain: str
    own_site_base: str
    tier: str
    auto_transmit: bool
    log_level: str
    repeat_window_days: int = 90
    daily_ceiling: int = 100

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

        level = source.get("BDL_LOG_LEVEL", "INFO").strip().upper()
        if level not in _LEVELS:
            raise ConfigError("Unsupported log level", env="BDL_LOG_LEVEL", value=level)

        center = source.get("BDL_CENTER_DOMAIN", "").strip().lower()
        base = source.get("BDL_OWN_SITE_BASE", "").strip().rstrip("/")
        if not base and center:
            base = "https://" + center

        return cls(
            home=home,
            snapshot_dir=_path(home, "BDL_SNAPSHOT_DIR", "data/snapshots", source),
            prose_dir=_path(home, "BDL_PROSE_DIR", "data/prose", source),
            out_dir=_path(home, "BDL_OUT_DIR", "data/out", source),
            center_domain=center,
            own_site_base=base,
            tier=tier,
            auto_transmit=auto,
            log_level=level,
        )
