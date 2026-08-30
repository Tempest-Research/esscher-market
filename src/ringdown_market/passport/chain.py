"""Append-only hash-linked Trade Passport chain.

The passport is the single readable trace required by issue #33: every stage
from permitted source bytes to final-flat reconciliation is one entry, each
entry binds its parent entries by hash, and the whole chain is verifiable
deterministically without trusting any single artifact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

PASSPORT_SCHEMA = "esscher.trade_passport"
PASSPORT_SCHEMA_VERSION = 1
GENESIS_PREV_SHA256 = "0" * 64


class PassportStage(StrEnum):
    """Frozen trace stages in causal order."""

    SOURCE_EVIDENCE = "SOURCE_EVIDENCE"
    SNAPSHOT = "SNAPSHOT"
    DECISION = "DECISION"
    PACKAGE = "PACKAGE"
    RISK_RESERVATION = "RISK_RESERVATION"
    PERMIT = "PERMIT"
    OPEN_SUBMISSION = "OPEN_SUBMISSION"
    OPEN_FILL = "OPEN_FILL"
    HOLD = "HOLD"
    CLOSE_SUBMISSION = "CLOSE_SUBMISSION"
    CLOSE_FILL = "CLOSE_FILL"
    FINAL_FLAT_RECONCILIATION = "FINAL_FLAT_RECONCILIATION"
    RESULT = "RESULT"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("passport timestamps must be timezone-aware")
    if value.microsecond != 0:
        raise ValueError("passport timestamps must use second precision")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class PassportEntry:
    """One immutable trace entry bound to its predecessor."""

    sequence: int
    stage: PassportStage
    at: datetime
    payload: Mapping[str, object]
    prev_sha256: str
    entry_sha256: str


def entry_content(
    *,
    sequence: int,
    stage: PassportStage,
    at: datetime,
    payload: Mapping[str, object],
    prev_sha256: str,
) -> dict[str, object]:
    """Return the canonical content that is hashed for one entry."""

    return {
        "schema": PASSPORT_SCHEMA,
        "schema_version": PASSPORT_SCHEMA_VERSION,
        "sequence": sequence,
        "stage": stage.value,
        "at": _utc_text(at),
        "payload": dict(payload),
        "prev_sha256": prev_sha256,
    }


def compute_entry_sha256(
    *,
    sequence: int,
    stage: PassportStage,
    at: datetime,
    payload: Mapping[str, object],
    prev_sha256: str,
) -> str:
    """Compute the deterministic SHA-256 of one entry's canonical content."""

    content = entry_content(
        sequence=sequence, stage=stage, at=at, payload=payload, prev_sha256=prev_sha256
    )
    return hashlib.sha256(_canonical_json_bytes(content)).hexdigest()


class PassportChainError(ValueError):
    """Raised when an append violates the append-only chain contract."""


class TradePassport:
    """Append-only builder; entries can never be rewritten or reordered."""

    def __init__(self) -> None:
        self._entries: list[PassportEntry] = []

    @property
    def entries(self) -> tuple[PassportEntry, ...]:
        return tuple(self._entries)

    @property
    def head_sha256(self) -> str:
        if not self._entries:
            return GENESIS_PREV_SHA256
        return self._entries[-1].entry_sha256

    def append(
        self, *, stage: PassportStage, at: datetime, payload: Mapping[str, object]
    ) -> PassportEntry:
        sequence = len(self._entries)
        entry = PassportEntry(
            sequence=sequence,
            stage=stage,
            at=at,
            payload=dict(payload),
            prev_sha256=self.head_sha256,
            entry_sha256=compute_entry_sha256(
                sequence=sequence,
                stage=stage,
                at=at,
                payload=payload,
                prev_sha256=self.head_sha256,
            ),
        )
        self._entries.append(entry)
        return entry

    def payload_bytes(self) -> bytes:
        """Serialize the whole chain to deterministic canonical bytes."""

        return _canonical_json_bytes(
            {
                "schema": PASSPORT_SCHEMA,
                "schema_version": PASSPORT_SCHEMA_VERSION,
                "entries": [
                    entry_content(
                        sequence=entry.sequence,
                        stage=entry.stage,
                        at=entry.at,
                        payload=entry.payload,
                        prev_sha256=entry.prev_sha256,
                    )
                    for entry in self._entries
                ],
                "head_sha256": self.head_sha256,
            }
        )

    def passport_sha256(self) -> str:
        return hashlib.sha256(self.payload_bytes()).hexdigest()


def parse_passport_bytes(raw: bytes) -> tuple[PassportEntry, ...]:
    """Parse canonical passport bytes into entries; structural drift raises."""

    try:
        payload = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PassportChainError(f"passport bytes are not valid JSON: {error}") from None
    if not isinstance(payload, Mapping):
        raise PassportChainError("passport must be an object")
    if payload.get("schema") != PASSPORT_SCHEMA:
        raise PassportChainError("unsupported passport schema")
    if payload.get("schema_version") != PASSPORT_SCHEMA_VERSION:
        raise PassportChainError("unsupported passport schema version")
    entries_value = payload.get("entries")
    if not isinstance(entries_value, Sequence) or isinstance(entries_value, (str, bytes)):
        raise PassportChainError("passport entries must be a list")
    entries: list[PassportEntry] = []
    for index, item in enumerate(entries_value):
        if not isinstance(item, Mapping):
            raise PassportChainError(f"entry {index} must be an object")
        for field in (
            "schema",
            "schema_version",
            "sequence",
            "stage",
            "at",
            "payload",
            "prev_sha256",
        ):
            if field not in item:
                raise PassportChainError(f"entry {index} is missing {field}")
        try:
            stage = PassportStage(item["stage"])
        except ValueError:
            raise PassportChainError(f"entry {index} has an unknown stage") from None
        try:
            at = datetime.fromisoformat(str(item["at"])).astimezone(UTC)
        except ValueError:
            raise PassportChainError(f"entry {index} has an invalid timestamp") from None
        payload_value = item["payload"]
        if not isinstance(payload_value, Mapping):
            raise PassportChainError(f"entry {index} payload must be an object")
        entries.append(
            PassportEntry(
                sequence=int(item["sequence"]),
                stage=stage,
                at=at,
                payload=dict(payload_value),
                prev_sha256=str(item["prev_sha256"]),
                entry_sha256=compute_entry_sha256(
                    sequence=int(item["sequence"]),
                    stage=stage,
                    at=at,
                    payload=payload_value,
                    prev_sha256=str(item["prev_sha256"]),
                ),
            )
        )
    return tuple(entries)
