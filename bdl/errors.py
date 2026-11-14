"""Typed errors. Callers see a normalised structure, never a traceback."""

from __future__ import annotations

from typing import Any, Dict


class DeliveryError(Exception):
    code = "DELIVERY_ERROR"
    exit_code = 1

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "detail": self.detail,
            }
        }


class ConfigError(DeliveryError):
    code = "CONFIG_ERROR"


class ContractError(DeliveryError):
    code = "CONTRACT_ERROR"


class SnapshotError(ContractError):
    code = "SNAPSHOT_ERROR"


class ProseError(DeliveryError):
    code = "PROSE_ERROR"


class WriteError(DeliveryError):
    code = "WRITE_ERROR"


class PolicyError(DeliveryError):
    code = "POLICY_ERROR"
