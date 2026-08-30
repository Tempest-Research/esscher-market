"""Shared immutable-value checks for the snapshot collector."""

from __future__ import annotations

import re
from datetime import UTC, datetime

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def require_utc(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo != UTC:
        raise ValueError(f"{field} must be a UTC datetime")


def require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty trimmed identifier")
