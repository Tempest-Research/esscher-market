"""Append-only, cutoff-safe episodic context for PAPER-only Esscher.

This module records already-made decision and observed execution facts.  It is
strictly offline and deterministic: it neither retrains nor changes a policy,
prompt, model configuration, routing decision, or broker authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import NoReturn

from esscher.risk.ledger import ImmutableEpisodeConflict, RiskLedger

DECISION_EPISODE_SCHEMA = "esscher.episodic_decision"
OUTCOME_EPISODE_SCHEMA = "esscher.episodic_outcome"
BROKER_TRUTH_SNAPSHOT_SCHEMA = "esscher.episodic_broker_truth_snapshot"
EPISODIC_SUMMARY_SCHEMA = "esscher.episodic_summary"
EPISODIC_SCHEMA_VERSION = 1
GENESIS_SUMMARY_SHA256 = "0" * 64

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9./-]{0,31}$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_DECIMAL_TEXT = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")

_DECISION_DISPOSITIONS = frozenset({"ACCEPTED", "ABSTAINED", "REJECTED"})
_DIRECTIONS = frozenset({"UP", "DOWN", "UNCERTAIN"})
_LIFECYCLE_OUTCOMES = frozenset(
    {
        "CLOSED",
        "OPEN",
        "PARTIAL",
        "MANUAL_REQUIRED",
        "ROUTE_FAILED",
        "RECONCILIATION_FAILED",
        "CANCELED",
    }
)
_PNL_CLASSIFICATIONS = frozenset({"REALIZED", "NOT_APPLICABLE", "UNAVAILABLE"})
_SUMMARY_COMPATIBILITIES = frozenset(
    {"COMPATIBLE", "POLICY_MISMATCH", "MODEL_CONFIG_MISMATCH", "POLICY_AND_MODEL_MISMATCH"}
)
_SUMMARY_UNAVAILABLE_REASONS = frozenset(
    {
        "NO_OUTCOME",
        "OUTCOME_AFTER_CUTOFF",
        "OUTCOME_OBSERVED_AFTER_CUTOFF",
        "OUTCOME_CREATED_AFTER_CUTOFF",
        "OUTCOME_OPEN",
        "OUTCOME_PARTIAL",
        "OUTCOME_MANUAL_REQUIRED",
        "ROUTE_FAILURE",
        "RECONCILIATION_FAILURE",
        "OUTCOME_CANCELED_UNVERIFIED",
        "OUTCOME_BROKER_NOT_FLAT",
        "OUTCOME_LINK_MISMATCH",
        "POLICY_MISMATCH",
        "MODEL_CONFIG_MISMATCH",
        "POLICY_AND_MODEL_MISMATCH",
    }
)

_DECISION_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "episode_id",
        "event_id",
        "candidate_id",
        "symbol",
        "occurred_at",
        "decision_cutoff_at",
        "source_policy_sha256",
        "source_evidence_sha256",
        "source_feature_sha256",
        "source_snapshot_sha256",
        "prior_summary_sha256",
        "route_sha256",
        "prompt_sha256",
        "model_config_sha256",
        "exchange_sha256",
        "decision_sha256",
        "disposition",
        "direction",
        "created_at",
        "supersedes_episode_id",
        "supersedes_episode_sha256",
    }
)
_OUTCOME_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "outcome_id",
        "decision_episode_id",
        "event_id",
        "open_permit_id",
        "close_permit_id",
        "open_order_id",
        "close_order_id",
        "terminal_at",
        "observed_at",
        "lifecycle_outcome",
        "pnl_classification",
        "gross_pnl",
        "net_pnl",
        "reconciliation_sha256",
        "final_flat",
        "supersedes_outcome_id",
        "supersedes_outcome_sha256",
        "created_at",
    }
)
_SNAPSHOT_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "snapshot_id",
        "observed_at",
        "account_sha256",
        "orders_sha256",
        "positions_sha256",
        "equity",
        "open_exposure",
        "is_flat",
        "created_at",
        "supersedes_snapshot_id",
        "supersedes_snapshot_sha256",
    }
)
_SUMMARY_ROW_FIELDS = frozenset(
    {
        "episode_id",
        "episode_sha256",
        "event_id",
        "candidate_id",
        "symbol",
        "occurred_at",
        "decision_cutoff_at",
        "source_policy_sha256",
        "model_config_sha256",
        "decision_sha256",
        "disposition",
        "direction",
        "compatibility",
        "outcome_id",
        "lifecycle_outcome",
        "pnl_classification",
        "gross_pnl",
        "net_pnl",
        "outcome_unavailable_reason",
    }
)
_SUMMARY_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "as_of",
        "policy_sha256",
        "model_config_sha256",
        "candidate_ids",
        "limit",
        "rows",
        "completed_count",
        "realized_count",
        "net_pnl",
        "route_failure_count",
        "reconciliation_failure_count",
        "candidate_filter_excluded_count",
        "latest_broker_truth_snapshot_id",
        "latest_broker_truth_snapshot_sha256",
        "summary_sha256",
    }
)


class EpisodicReason(StrEnum):
    """Stable deterministic reasons for rejecting episodic records."""

    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    NON_CANONICAL_DOCUMENT = "NON_CANONICAL_DOCUMENT"
    INVALID_ID = "INVALID_ID"
    INVALID_HASH = "INVALID_HASH"
    INVALID_DECIMAL = "INVALID_DECIMAL"
    INVALID_CLOCK = "INVALID_CLOCK"
    INVALID_STATE = "INVALID_STATE"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    DECISION_LINK_MISMATCH = "DECISION_LINK_MISMATCH"
    CORRECTION_MISMATCH = "CORRECTION_MISMATCH"
    STORED_RECORD_INVALID = "STORED_RECORD_INVALID"
    HASH_MISMATCH = "HASH_MISMATCH"
    SUMMARY_SEMANTIC_MISMATCH = "SUMMARY_SEMANTIC_MISMATCH"


class EpisodeRejected(ValueError):
    """A typed, deterministic, fail-closed episodic-memory rejection."""

    def __init__(self, reason: EpisodicReason, path: str, detail: str) -> None:
        self.reason = reason
        self.path = path
        self.detail = detail
        super().__init__(f"{reason.value} at {path}: {detail}")


class _DuplicateFieldError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecisionEpisode:
    """One immutable, source-bound strategy decision fact."""

    episode_id: str
    event_id: str
    candidate_id: str
    symbol: str
    occurred_at: datetime
    decision_cutoff_at: datetime
    source_policy_sha256: str
    source_evidence_sha256: str
    source_feature_sha256: str
    source_snapshot_sha256: str
    prior_summary_sha256: str
    route_sha256: str
    prompt_sha256: str
    model_config_sha256: str
    exchange_sha256: str
    decision_sha256: str
    disposition: str
    direction: str
    created_at: datetime
    supersedes_episode_id: str | None
    supersedes_episode_sha256: str | None


@dataclass(frozen=True, slots=True)
class OutcomeEpisode:
    """One immutable observation of a decision lifecycle or realized PnL."""

    outcome_id: str
    decision_episode_id: str
    event_id: str
    open_permit_id: str | None
    close_permit_id: str | None
    open_order_id: str | None
    close_order_id: str | None
    terminal_at: datetime
    observed_at: datetime
    lifecycle_outcome: str
    pnl_classification: str
    gross_pnl: str | None
    net_pnl: str | None
    reconciliation_sha256: str
    final_flat: bool
    supersedes_outcome_id: str | None
    supersedes_outcome_sha256: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class BrokerTruthSnapshot:
    """One immutable broker-observed account/orders/positions truth fact."""

    snapshot_id: str
    observed_at: datetime
    account_sha256: str
    orders_sha256: str
    positions_sha256: str
    equity: str
    open_exposure: str
    is_flat: bool
    created_at: datetime
    supersedes_snapshot_id: str | None
    supersedes_snapshot_sha256: str | None


@dataclass(frozen=True, slots=True)
class EpisodicSummaryRow:
    """A bounded, context-only view of one prior decision and its outcome fact."""

    episode_id: str
    episode_sha256: str
    event_id: str
    candidate_id: str
    symbol: str
    occurred_at: datetime
    decision_cutoff_at: datetime
    source_policy_sha256: str
    model_config_sha256: str
    decision_sha256: str
    disposition: str
    direction: str
    compatibility: str
    outcome_id: str | None
    lifecycle_outcome: str | None
    pnl_classification: str | None
    gross_pnl: str | None
    net_pnl: str | None
    outcome_unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class EpisodicSummary:
    """A deterministic cutoff-safe context artifact with its own identity hash."""

    as_of: datetime
    policy_sha256: str
    model_config_sha256: str
    candidate_ids: tuple[str, ...]
    limit: int
    rows: tuple[EpisodicSummaryRow, ...]
    completed_count: int
    realized_count: int
    net_pnl: str
    route_failure_count: int
    reconciliation_failure_count: int
    candidate_filter_excluded_count: int
    latest_broker_truth_snapshot_id: str | None
    latest_broker_truth_snapshot_sha256: str | None
    summary_sha256: str


def _reject(reason: EpisodicReason, path: str, detail: str) -> NoReturn:
    raise EpisodeRejected(reason, path, detail)


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateFieldError(key)
        value[key] = item
    return value


def _invalid_float(value: str) -> NoReturn:
    raise ValueError(f"JSON numeric literal {value} is forbidden")


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")


def _decode(raw: object, *, path: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(EpisodicReason.INVALID_DOCUMENT, path, "must be immutable UTF-8 bytes")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_invalid_float,
            parse_constant=_invalid_constant,
        )
    except _DuplicateFieldError as error:
        _reject(EpisodicReason.DUPLICATE_FIELD, path, f"duplicate field {error}")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(EpisodicReason.INVALID_DOCUMENT, path, str(error))
    if not isinstance(payload, Mapping):
        _reject(EpisodicReason.INVALID_DOCUMENT, path, "root must be an object")
    return payload


def _canonical_bytes(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        _reject(EpisodicReason.INVALID_DOCUMENT, "canonical", str(error))


def _strict_object(
    payload: Mapping[str, object], *, path: str, fields: frozenset[str]
) -> Mapping[str, object]:
    actual = set(payload)
    unknown = actual - fields
    if unknown:
        _reject(EpisodicReason.UNKNOWN_FIELD, path, f"unknown fields: {', '.join(sorted(unknown))}")
    missing = fields - actual
    if missing:
        _reject(EpisodicReason.MISSING_FIELD, path, f"missing fields: {', '.join(sorted(missing))}")
    return payload


def _schema(payload: Mapping[str, object], *, schema: str, path: str) -> None:
    if payload["schema"] != schema or type(payload["schema_version"]) is not int:
        _reject(EpisodicReason.UNSUPPORTED_SCHEMA, path, "schema or schema_version is invalid")
    if payload["schema_version"] != EPISODIC_SCHEMA_VERSION:
        _reject(EpisodicReason.UNSUPPORTED_SCHEMA, path, "unsupported schema version")


def _identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _reject(EpisodicReason.INVALID_ID, path, "must be a normalized identifier")
    return value


def _nullable_identifier(value: object, *, path: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, path=path)


def _symbol(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        _reject(EpisodicReason.INVALID_ID, path, "must be an uppercase normalized symbol")
    return value


def _sha256(value: object, *, path: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _reject(EpisodicReason.INVALID_HASH, path, "must be a lowercase SHA-256 digest")
    return value


def _nullable_sha256(value: object, *, path: str) -> str | None:
    if value is None:
        return None
    return _sha256(value, path=path)


def _timestamp(value: object, *, path: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is not UTC:
        _reject(EpisodicReason.INVALID_CLOCK, path, "must be an aware UTC datetime")
    return value.isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP.fullmatch(value):
        _reject(EpisodicReason.INVALID_CLOCK, path, "must be a canonical UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as error:
        _reject(EpisodicReason.INVALID_CLOCK, path, str(error))
    if _timestamp(parsed, path=path) != value:
        _reject(EpisodicReason.NON_CANONICAL_DOCUMENT, path, "timestamp is not canonical")
    return parsed


def _choice(value: object, *, path: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        _reject(EpisodicReason.INVALID_STATE, path, "unknown bounded state")
    return value


def _boolean(value: object, *, path: str) -> bool:
    if type(value) is not bool:
        _reject(EpisodicReason.INVALID_STATE, path, "must be a JSON boolean")
    return value


def _decimal_text(value: object, *, path: str, nonnegative: bool = False) -> str:
    if not isinstance(value, str) or not _DECIMAL_TEXT.fullmatch(value):
        _reject(EpisodicReason.INVALID_DECIMAL, path, "must be canonical decimal text")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        _reject(EpisodicReason.INVALID_DECIMAL, path, "cannot be parsed as Decimal")
    if not decimal.is_finite() or value == "-0" or ("." in value and value.endswith("0")):
        _reject(EpisodicReason.INVALID_DECIMAL, path, "must have exactly one finite canonical form")
    if nonnegative and decimal < 0:
        _reject(EpisodicReason.INVALID_DECIMAL, path, "must be non-negative")
    return value


def _nullable_decimal(value: object, *, path: str) -> str | None:
    if value is None:
        return None
    return _decimal_text(value, path=path)


def _correction_pair(
    record_id: str | None,
    record_sha256: str | None,
    *,
    path: str,
) -> None:
    if (record_id is None) != (record_sha256 is None):
        _reject(EpisodicReason.INVALID_STATE, path, "correction id and exact prior hash are paired")


def _decision_payload(value: object) -> dict[str, object]:
    if not isinstance(value, DecisionEpisode):
        _reject(EpisodicReason.INVALID_DOCUMENT, "decision", "must be a DecisionEpisode")
    episode = DecisionEpisode(
        episode_id=_identifier(value.episode_id, path="decision.episode_id"),
        event_id=_identifier(value.event_id, path="decision.event_id"),
        candidate_id=_identifier(value.candidate_id, path="decision.candidate_id"),
        symbol=_symbol(value.symbol, path="decision.symbol"),
        occurred_at=value.occurred_at,
        decision_cutoff_at=value.decision_cutoff_at,
        source_policy_sha256=_sha256(
            value.source_policy_sha256, path="decision.source_policy_sha256"
        ),
        source_evidence_sha256=_sha256(
            value.source_evidence_sha256, path="decision.source_evidence_sha256"
        ),
        source_feature_sha256=_sha256(
            value.source_feature_sha256, path="decision.source_feature_sha256"
        ),
        source_snapshot_sha256=_sha256(
            value.source_snapshot_sha256, path="decision.source_snapshot_sha256"
        ),
        prior_summary_sha256=_sha256(
            value.prior_summary_sha256, path="decision.prior_summary_sha256"
        ),
        route_sha256=_sha256(value.route_sha256, path="decision.route_sha256"),
        prompt_sha256=_sha256(value.prompt_sha256, path="decision.prompt_sha256"),
        model_config_sha256=_sha256(value.model_config_sha256, path="decision.model_config_sha256"),
        exchange_sha256=_sha256(value.exchange_sha256, path="decision.exchange_sha256"),
        decision_sha256=_sha256(value.decision_sha256, path="decision.decision_sha256"),
        disposition=_choice(
            value.disposition, path="decision.disposition", allowed=_DECISION_DISPOSITIONS
        ),
        direction=_choice(value.direction, path="decision.direction", allowed=_DIRECTIONS),
        created_at=value.created_at,
        supersedes_episode_id=_nullable_identifier(
            value.supersedes_episode_id, path="decision.supersedes_episode_id"
        ),
        supersedes_episode_sha256=_nullable_sha256(
            value.supersedes_episode_sha256, path="decision.supersedes_episode_sha256"
        ),
    )
    occurred_at = _timestamp(episode.occurred_at, path="decision.occurred_at")
    cutoff_at = _timestamp(episode.decision_cutoff_at, path="decision.decision_cutoff_at")
    created_at = _timestamp(episode.created_at, path="decision.created_at")
    if episode.occurred_at > episode.decision_cutoff_at or episode.created_at < episode.occurred_at:
        _reject(EpisodicReason.INVALID_STATE, "decision", "decision clocks are inconsistent")
    if episode.disposition == "ACCEPTED" and episode.direction not in {"UP", "DOWN"}:
        _reject(
            EpisodicReason.INVALID_STATE,
            "decision.direction",
            "accepted decisions require UP or DOWN",
        )
    if episode.disposition != "ACCEPTED" and episode.direction != "UNCERTAIN":
        _reject(
            EpisodicReason.INVALID_STATE,
            "decision.direction",
            "abstained/rejected decisions require UNCERTAIN",
        )
    _correction_pair(
        episode.supersedes_episode_id,
        episode.supersedes_episode_sha256,
        path="decision.supersedes",
    )
    if episode.supersedes_episode_id == episode.episode_id:
        _reject(
            EpisodicReason.INVALID_STATE,
            "decision.supersedes_episode_id",
            "cannot supersede itself",
        )
    return {
        "schema": DECISION_EPISODE_SCHEMA,
        "schema_version": EPISODIC_SCHEMA_VERSION,
        "episode_id": episode.episode_id,
        "event_id": episode.event_id,
        "candidate_id": episode.candidate_id,
        "symbol": episode.symbol,
        "occurred_at": occurred_at,
        "decision_cutoff_at": cutoff_at,
        "source_policy_sha256": episode.source_policy_sha256,
        "source_evidence_sha256": episode.source_evidence_sha256,
        "source_feature_sha256": episode.source_feature_sha256,
        "source_snapshot_sha256": episode.source_snapshot_sha256,
        "prior_summary_sha256": episode.prior_summary_sha256,
        "route_sha256": episode.route_sha256,
        "prompt_sha256": episode.prompt_sha256,
        "model_config_sha256": episode.model_config_sha256,
        "exchange_sha256": episode.exchange_sha256,
        "decision_sha256": episode.decision_sha256,
        "disposition": episode.disposition,
        "direction": episode.direction,
        "created_at": created_at,
        "supersedes_episode_id": episode.supersedes_episode_id,
        "supersedes_episode_sha256": episode.supersedes_episode_sha256,
    }


def _outcome_payload(value: object) -> dict[str, object]:
    if not isinstance(value, OutcomeEpisode):
        _reject(EpisodicReason.INVALID_DOCUMENT, "outcome", "must be an OutcomeEpisode")
    outcome = OutcomeEpisode(
        outcome_id=_identifier(value.outcome_id, path="outcome.outcome_id"),
        decision_episode_id=_identifier(
            value.decision_episode_id, path="outcome.decision_episode_id"
        ),
        event_id=_identifier(value.event_id, path="outcome.event_id"),
        open_permit_id=_nullable_identifier(value.open_permit_id, path="outcome.open_permit_id"),
        close_permit_id=_nullable_identifier(value.close_permit_id, path="outcome.close_permit_id"),
        open_order_id=_nullable_identifier(value.open_order_id, path="outcome.open_order_id"),
        close_order_id=_nullable_identifier(value.close_order_id, path="outcome.close_order_id"),
        terminal_at=value.terminal_at,
        observed_at=value.observed_at,
        lifecycle_outcome=_choice(
            value.lifecycle_outcome, path="outcome.lifecycle_outcome", allowed=_LIFECYCLE_OUTCOMES
        ),
        pnl_classification=_choice(
            value.pnl_classification,
            path="outcome.pnl_classification",
            allowed=_PNL_CLASSIFICATIONS,
        ),
        gross_pnl=_nullable_decimal(value.gross_pnl, path="outcome.gross_pnl"),
        net_pnl=_nullable_decimal(value.net_pnl, path="outcome.net_pnl"),
        reconciliation_sha256=_sha256(
            value.reconciliation_sha256, path="outcome.reconciliation_sha256"
        ),
        final_flat=_boolean(value.final_flat, path="outcome.final_flat"),
        supersedes_outcome_id=_nullable_identifier(
            value.supersedes_outcome_id, path="outcome.supersedes_outcome_id"
        ),
        supersedes_outcome_sha256=_nullable_sha256(
            value.supersedes_outcome_sha256, path="outcome.supersedes_outcome_sha256"
        ),
        created_at=value.created_at,
    )
    terminal_at = _timestamp(outcome.terminal_at, path="outcome.terminal_at")
    observed_at = _timestamp(outcome.observed_at, path="outcome.observed_at")
    created_at = _timestamp(outcome.created_at, path="outcome.created_at")
    if outcome.observed_at < outcome.terminal_at or outcome.created_at < outcome.observed_at:
        _reject(EpisodicReason.INVALID_STATE, "outcome", "outcome clocks are inconsistent")
    if outcome.lifecycle_outcome == "CLOSED":
        if (
            not outcome.final_flat
            or outcome.pnl_classification != "REALIZED"
            or outcome.gross_pnl is None
            or outcome.net_pnl is None
        ):
            _reject(
                EpisodicReason.INVALID_STATE, "outcome", "closed outcomes require flat realized PnL"
            )
    elif outcome.lifecycle_outcome == "CANCELED":
        if (
            not outcome.final_flat
            or outcome.pnl_classification != "NOT_APPLICABLE"
            or outcome.gross_pnl is not None
            or outcome.net_pnl is not None
        ):
            _reject(
                EpisodicReason.INVALID_STATE,
                "outcome",
                "canceled outcomes require flat NOT_APPLICABLE PnL",
            )
    elif (
        outcome.final_flat
        or outcome.pnl_classification != "UNAVAILABLE"
        or outcome.gross_pnl is not None
        or outcome.net_pnl is not None
    ):
        _reject(
            EpisodicReason.INVALID_STATE,
            "outcome",
            "open, partial, manual, or failed outcomes cannot imply PnL or flatness",
        )
    _correction_pair(
        outcome.supersedes_outcome_id,
        outcome.supersedes_outcome_sha256,
        path="outcome.supersedes",
    )
    if outcome.supersedes_outcome_id == outcome.outcome_id:
        _reject(
            EpisodicReason.INVALID_STATE, "outcome.supersedes_outcome_id", "cannot supersede itself"
        )
    return {
        "schema": OUTCOME_EPISODE_SCHEMA,
        "schema_version": EPISODIC_SCHEMA_VERSION,
        "outcome_id": outcome.outcome_id,
        "decision_episode_id": outcome.decision_episode_id,
        "event_id": outcome.event_id,
        "open_permit_id": outcome.open_permit_id,
        "close_permit_id": outcome.close_permit_id,
        "open_order_id": outcome.open_order_id,
        "close_order_id": outcome.close_order_id,
        "terminal_at": terminal_at,
        "observed_at": observed_at,
        "lifecycle_outcome": outcome.lifecycle_outcome,
        "pnl_classification": outcome.pnl_classification,
        "gross_pnl": outcome.gross_pnl,
        "net_pnl": outcome.net_pnl,
        "reconciliation_sha256": outcome.reconciliation_sha256,
        "final_flat": outcome.final_flat,
        "supersedes_outcome_id": outcome.supersedes_outcome_id,
        "supersedes_outcome_sha256": outcome.supersedes_outcome_sha256,
        "created_at": created_at,
    }


def _snapshot_payload(value: object) -> dict[str, object]:
    if not isinstance(value, BrokerTruthSnapshot):
        _reject(EpisodicReason.INVALID_DOCUMENT, "snapshot", "must be a BrokerTruthSnapshot")
    snapshot = BrokerTruthSnapshot(
        snapshot_id=_identifier(value.snapshot_id, path="snapshot.snapshot_id"),
        observed_at=value.observed_at,
        account_sha256=_sha256(value.account_sha256, path="snapshot.account_sha256"),
        orders_sha256=_sha256(value.orders_sha256, path="snapshot.orders_sha256"),
        positions_sha256=_sha256(value.positions_sha256, path="snapshot.positions_sha256"),
        equity=_decimal_text(value.equity, path="snapshot.equity"),
        open_exposure=_decimal_text(
            value.open_exposure, path="snapshot.open_exposure", nonnegative=True
        ),
        is_flat=_boolean(value.is_flat, path="snapshot.is_flat"),
        created_at=value.created_at,
        supersedes_snapshot_id=_nullable_identifier(
            value.supersedes_snapshot_id, path="snapshot.supersedes_snapshot_id"
        ),
        supersedes_snapshot_sha256=_nullable_sha256(
            value.supersedes_snapshot_sha256, path="snapshot.supersedes_snapshot_sha256"
        ),
    )
    observed_at = _timestamp(snapshot.observed_at, path="snapshot.observed_at")
    created_at = _timestamp(snapshot.created_at, path="snapshot.created_at")
    if snapshot.created_at < snapshot.observed_at:
        _reject(EpisodicReason.INVALID_STATE, "snapshot", "created_at precedes observed_at")
    if snapshot.is_flat != (snapshot.open_exposure == "0"):
        _reject(
            EpisodicReason.INVALID_STATE,
            "snapshot.open_exposure",
            "flatness must exactly agree with open exposure",
        )
    _correction_pair(
        snapshot.supersedes_snapshot_id,
        snapshot.supersedes_snapshot_sha256,
        path="snapshot.supersedes",
    )
    if snapshot.supersedes_snapshot_id == snapshot.snapshot_id:
        _reject(
            EpisodicReason.INVALID_STATE,
            "snapshot.supersedes_snapshot_id",
            "cannot supersede itself",
        )
    return {
        "schema": BROKER_TRUTH_SNAPSHOT_SCHEMA,
        "schema_version": EPISODIC_SCHEMA_VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "observed_at": observed_at,
        "account_sha256": snapshot.account_sha256,
        "orders_sha256": snapshot.orders_sha256,
        "positions_sha256": snapshot.positions_sha256,
        "equity": snapshot.equity,
        "open_exposure": snapshot.open_exposure,
        "is_flat": snapshot.is_flat,
        "created_at": created_at,
        "supersedes_snapshot_id": snapshot.supersedes_snapshot_id,
        "supersedes_snapshot_sha256": snapshot.supersedes_snapshot_sha256,
    }


def decision_episode_bytes(value: object) -> bytes:
    """Return the one canonical immutable byte form of a decision episode."""

    return _canonical_bytes(_decision_payload(value))


def decision_episode_sha256(value: object) -> str:
    """Return the SHA-256 identity of strict canonical decision bytes."""

    return hashlib.sha256(decision_episode_bytes(value)).hexdigest()


def outcome_episode_bytes(value: object) -> bytes:
    """Return the one canonical immutable byte form of an outcome episode."""

    return _canonical_bytes(_outcome_payload(value))


def outcome_episode_sha256(value: object) -> str:
    """Return the SHA-256 identity of strict canonical outcome bytes."""

    return hashlib.sha256(outcome_episode_bytes(value)).hexdigest()


def broker_truth_snapshot_bytes(value: object) -> bytes:
    """Return the one canonical immutable byte form of a broker snapshot."""

    return _canonical_bytes(_snapshot_payload(value))


def broker_truth_snapshot_sha256(value: object) -> str:
    """Return the SHA-256 identity of strict canonical broker snapshot bytes."""

    return hashlib.sha256(broker_truth_snapshot_bytes(value)).hexdigest()


def _parse_decision_payload(payload: Mapping[str, object]) -> DecisionEpisode:
    _strict_object(payload, path="decision", fields=_DECISION_FIELDS)
    _schema(payload, schema=DECISION_EPISODE_SCHEMA, path="decision")
    return DecisionEpisode(
        episode_id=_identifier(payload["episode_id"], path="decision.episode_id"),
        event_id=_identifier(payload["event_id"], path="decision.event_id"),
        candidate_id=_identifier(payload["candidate_id"], path="decision.candidate_id"),
        symbol=_symbol(payload["symbol"], path="decision.symbol"),
        occurred_at=_parse_timestamp(payload["occurred_at"], path="decision.occurred_at"),
        decision_cutoff_at=_parse_timestamp(
            payload["decision_cutoff_at"], path="decision.decision_cutoff_at"
        ),
        source_policy_sha256=_sha256(
            payload["source_policy_sha256"], path="decision.source_policy_sha256"
        ),
        source_evidence_sha256=_sha256(
            payload["source_evidence_sha256"], path="decision.source_evidence_sha256"
        ),
        source_feature_sha256=_sha256(
            payload["source_feature_sha256"], path="decision.source_feature_sha256"
        ),
        source_snapshot_sha256=_sha256(
            payload["source_snapshot_sha256"], path="decision.source_snapshot_sha256"
        ),
        prior_summary_sha256=_sha256(
            payload["prior_summary_sha256"], path="decision.prior_summary_sha256"
        ),
        route_sha256=_sha256(payload["route_sha256"], path="decision.route_sha256"),
        prompt_sha256=_sha256(payload["prompt_sha256"], path="decision.prompt_sha256"),
        model_config_sha256=_sha256(
            payload["model_config_sha256"], path="decision.model_config_sha256"
        ),
        exchange_sha256=_sha256(payload["exchange_sha256"], path="decision.exchange_sha256"),
        decision_sha256=_sha256(payload["decision_sha256"], path="decision.decision_sha256"),
        disposition=_choice(
            payload["disposition"], path="decision.disposition", allowed=_DECISION_DISPOSITIONS
        ),
        direction=_choice(payload["direction"], path="decision.direction", allowed=_DIRECTIONS),
        created_at=_parse_timestamp(payload["created_at"], path="decision.created_at"),
        supersedes_episode_id=_nullable_identifier(
            payload["supersedes_episode_id"], path="decision.supersedes_episode_id"
        ),
        supersedes_episode_sha256=_nullable_sha256(
            payload["supersedes_episode_sha256"], path="decision.supersedes_episode_sha256"
        ),
    )


def _parse_outcome_payload(payload: Mapping[str, object]) -> OutcomeEpisode:
    _strict_object(payload, path="outcome", fields=_OUTCOME_FIELDS)
    _schema(payload, schema=OUTCOME_EPISODE_SCHEMA, path="outcome")
    return OutcomeEpisode(
        outcome_id=_identifier(payload["outcome_id"], path="outcome.outcome_id"),
        decision_episode_id=_identifier(
            payload["decision_episode_id"], path="outcome.decision_episode_id"
        ),
        event_id=_identifier(payload["event_id"], path="outcome.event_id"),
        open_permit_id=_nullable_identifier(
            payload["open_permit_id"], path="outcome.open_permit_id"
        ),
        close_permit_id=_nullable_identifier(
            payload["close_permit_id"], path="outcome.close_permit_id"
        ),
        open_order_id=_nullable_identifier(payload["open_order_id"], path="outcome.open_order_id"),
        close_order_id=_nullable_identifier(
            payload["close_order_id"], path="outcome.close_order_id"
        ),
        terminal_at=_parse_timestamp(payload["terminal_at"], path="outcome.terminal_at"),
        observed_at=_parse_timestamp(payload["observed_at"], path="outcome.observed_at"),
        lifecycle_outcome=_choice(
            payload["lifecycle_outcome"],
            path="outcome.lifecycle_outcome",
            allowed=_LIFECYCLE_OUTCOMES,
        ),
        pnl_classification=_choice(
            payload["pnl_classification"],
            path="outcome.pnl_classification",
            allowed=_PNL_CLASSIFICATIONS,
        ),
        gross_pnl=_nullable_decimal(payload["gross_pnl"], path="outcome.gross_pnl"),
        net_pnl=_nullable_decimal(payload["net_pnl"], path="outcome.net_pnl"),
        reconciliation_sha256=_sha256(
            payload["reconciliation_sha256"], path="outcome.reconciliation_sha256"
        ),
        final_flat=_boolean(payload["final_flat"], path="outcome.final_flat"),
        supersedes_outcome_id=_nullable_identifier(
            payload["supersedes_outcome_id"], path="outcome.supersedes_outcome_id"
        ),
        supersedes_outcome_sha256=_nullable_sha256(
            payload["supersedes_outcome_sha256"], path="outcome.supersedes_outcome_sha256"
        ),
        created_at=_parse_timestamp(payload["created_at"], path="outcome.created_at"),
    )


def _parse_snapshot_payload(payload: Mapping[str, object]) -> BrokerTruthSnapshot:
    _strict_object(payload, path="snapshot", fields=_SNAPSHOT_FIELDS)
    _schema(payload, schema=BROKER_TRUTH_SNAPSHOT_SCHEMA, path="snapshot")
    return BrokerTruthSnapshot(
        snapshot_id=_identifier(payload["snapshot_id"], path="snapshot.snapshot_id"),
        observed_at=_parse_timestamp(payload["observed_at"], path="snapshot.observed_at"),
        account_sha256=_sha256(payload["account_sha256"], path="snapshot.account_sha256"),
        orders_sha256=_sha256(payload["orders_sha256"], path="snapshot.orders_sha256"),
        positions_sha256=_sha256(payload["positions_sha256"], path="snapshot.positions_sha256"),
        equity=_decimal_text(payload["equity"], path="snapshot.equity"),
        open_exposure=_decimal_text(
            payload["open_exposure"], path="snapshot.open_exposure", nonnegative=True
        ),
        is_flat=_boolean(payload["is_flat"], path="snapshot.is_flat"),
        created_at=_parse_timestamp(payload["created_at"], path="snapshot.created_at"),
        supersedes_snapshot_id=_nullable_identifier(
            payload["supersedes_snapshot_id"], path="snapshot.supersedes_snapshot_id"
        ),
        supersedes_snapshot_sha256=_nullable_sha256(
            payload["supersedes_snapshot_sha256"], path="snapshot.supersedes_snapshot_sha256"
        ),
    )


def _parse_canonical(
    raw: object,
    *,
    path: str,
    parser: Callable[[Mapping[str, object]], object],
    serializer: Callable[[object], bytes],
) -> object:
    payload = _decode(raw, path=path)
    value = parser(payload)
    if raw != serializer(value):
        _reject(EpisodicReason.NON_CANONICAL_DOCUMENT, path, "bytes are not canonical")
    return value


def parse_decision_episode(raw: object) -> DecisionEpisode:
    """Parse and verify exact canonical decision bytes."""

    value = _parse_canonical(
        raw,
        path="decision",
        parser=_parse_decision_payload,
        serializer=decision_episode_bytes,
    )
    if not isinstance(value, DecisionEpisode):
        raise AssertionError("decision parser returned the wrong type")
    return value


def parse_outcome_episode(raw: object) -> OutcomeEpisode:
    """Parse and verify exact canonical outcome bytes."""

    value = _parse_canonical(
        raw,
        path="outcome",
        parser=_parse_outcome_payload,
        serializer=outcome_episode_bytes,
    )
    if not isinstance(value, OutcomeEpisode):
        raise AssertionError("outcome parser returned the wrong type")
    return value


def parse_broker_truth_snapshot(raw: object) -> BrokerTruthSnapshot:
    """Parse and verify exact canonical broker-truth snapshot bytes."""

    value = _parse_canonical(
        raw,
        path="snapshot",
        parser=_parse_snapshot_payload,
        serializer=broker_truth_snapshot_bytes,
    )
    if not isinstance(value, BrokerTruthSnapshot):
        raise AssertionError("snapshot parser returned the wrong type")
    return value


def _decision_values(value: DecisionEpisode) -> dict[str, object]:
    payload = _decision_payload(value)
    return {key: payload[key] for key in _DECISION_FIELDS - {"schema", "schema_version"}}


def _outcome_values(value: OutcomeEpisode) -> dict[str, object]:
    payload = _outcome_payload(value)
    result = {key: payload[key] for key in _OUTCOME_FIELDS - {"schema", "schema_version"}}
    result["final_flat"] = int(bool(result["final_flat"]))
    return result


def _snapshot_values(value: BrokerTruthSnapshot) -> dict[str, object]:
    payload = _snapshot_payload(value)
    result = {key: payload[key] for key in _SNAPSHOT_FIELDS - {"schema", "schema_version"}}
    result["is_flat"] = int(bool(result["is_flat"]))
    return result


def _validated_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    path: str,
    parser: Callable[[object], object],
    values: Callable[[object], Mapping[str, object]],
) -> list[object]:
    records: list[object] = []
    for index, row in enumerate(rows):
        raw = row.get("payload")
        stored_sha256 = row.get("payload_sha256")
        if type(raw) is not bytes or not isinstance(stored_sha256, str):
            _reject(
                EpisodicReason.STORED_RECORD_INVALID, f"{path}[{index}]", "missing BLOB or hash"
            )
        if hashlib.sha256(raw).hexdigest() != stored_sha256:
            _reject(
                EpisodicReason.STORED_RECORD_INVALID, f"{path}[{index}]", "payload hash mismatch"
            )
        try:
            record = parser(raw)
        except EpisodeRejected as error:
            _reject(EpisodicReason.STORED_RECORD_INVALID, f"{path}[{index}]", error.detail)
        expected = values(record)
        for field, expected_value in expected.items():
            if row.get(field) != expected_value:
                _reject(
                    EpisodicReason.STORED_RECORD_INVALID,
                    f"{path}[{index}].{field}",
                    "normalized column does not match canonical payload",
                )
        records.append(record)
    return records


def _stored_decisions(ledger: RiskLedger) -> list[DecisionEpisode]:
    records = _validated_rows(
        ledger.decision_episode_rows(),
        path="stored.decisions",
        parser=parse_decision_episode,
        values=lambda value: _decision_values(value),
    )
    return [record for record in records if isinstance(record, DecisionEpisode)]


def _stored_outcomes(ledger: RiskLedger) -> list[OutcomeEpisode]:
    records = _validated_rows(
        ledger.outcome_episode_rows(),
        path="stored.outcomes",
        parser=parse_outcome_episode,
        values=lambda value: _outcome_values(value),
    )
    return [record for record in records if isinstance(record, OutcomeEpisode)]


def _stored_snapshots(ledger: RiskLedger) -> list[BrokerTruthSnapshot]:
    records = _validated_rows(
        ledger.broker_truth_snapshot_rows(),
        path="stored.snapshots",
        parser=parse_broker_truth_snapshot,
        values=lambda value: _snapshot_values(value),
    )
    return [record for record in records if isinstance(record, BrokerTruthSnapshot)]


def _find_by_id(records: Sequence[object], *, field: str, identity: str) -> object | None:
    for record in records:
        if getattr(record, field) == identity:
            return record
    return None


def _verify_exact_correction(
    *,
    current_id: str,
    supersedes_id: str | None,
    supersedes_sha256: str | None,
    records: Sequence[object],
    id_field: str,
    identity: Callable[[object], str],
    path: str,
) -> object | None:
    if supersedes_id is None:
        return None
    prior = _find_by_id(records, field=id_field, identity=supersedes_id)
    if prior is None or supersedes_sha256 is None or identity(prior) != supersedes_sha256:
        _reject(
            EpisodicReason.CORRECTION_MISMATCH,
            path,
            "prior id does not bind exact canonical identity",
        )
    if supersedes_id == current_id:
        _reject(EpisodicReason.INVALID_STATE, path, "cannot supersede itself")
    return prior


def append_decision_episode(ledger: RiskLedger, value: DecisionEpisode) -> bool:
    """Append a strict decision episode; exact replay alone is idempotent."""

    raw = decision_episode_bytes(value)
    if value.supersedes_episode_id is not None:
        prior = _verify_exact_correction(
            current_id=value.episode_id,
            supersedes_id=value.supersedes_episode_id,
            supersedes_sha256=value.supersedes_episode_sha256,
            records=_stored_decisions(ledger),
            id_field="episode_id",
            identity=lambda record: decision_episode_sha256(record),
            path="decision.supersedes",
        )
        if not isinstance(prior, DecisionEpisode) or (
            prior.event_id != value.event_id or prior.candidate_id != value.candidate_id
        ):
            _reject(
                EpisodicReason.CORRECTION_MISMATCH, "decision.supersedes", "prior linkage changed"
            )
    try:
        return ledger.append_decision_episode(
            values=_decision_values(value),
            payload=raw,
            payload_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except ImmutableEpisodeConflict as error:
        _reject(EpisodicReason.IDENTITY_CONFLICT, "decision.episode_id", error.identity)


def append_outcome_episode(ledger: RiskLedger, value: OutcomeEpisode) -> bool:
    """Append a strict outcome bound to an exact stored decision episode."""

    raw = outcome_episode_bytes(value)
    decision = _find_by_id(
        _stored_decisions(ledger), field="episode_id", identity=value.decision_episode_id
    )
    if not isinstance(decision, DecisionEpisode) or decision.event_id != value.event_id:
        _reject(
            EpisodicReason.DECISION_LINK_MISMATCH,
            "outcome.decision_episode_id",
            "outcome must bind an existing same-event decision",
        )
    if value.terminal_at < decision.occurred_at:
        _reject(EpisodicReason.INVALID_STATE, "outcome.terminal_at", "precedes its decision")
    if value.supersedes_outcome_id is not None:
        prior = _verify_exact_correction(
            current_id=value.outcome_id,
            supersedes_id=value.supersedes_outcome_id,
            supersedes_sha256=value.supersedes_outcome_sha256,
            records=_stored_outcomes(ledger),
            id_field="outcome_id",
            identity=lambda record: outcome_episode_sha256(record),
            path="outcome.supersedes",
        )
        if not isinstance(prior, OutcomeEpisode) or (
            prior.decision_episode_id != value.decision_episode_id
            or prior.event_id != value.event_id
        ):
            _reject(
                EpisodicReason.CORRECTION_MISMATCH, "outcome.supersedes", "prior linkage changed"
            )
    try:
        return ledger.append_outcome_episode(
            values=_outcome_values(value),
            payload=raw,
            payload_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except ImmutableEpisodeConflict as error:
        _reject(EpisodicReason.IDENTITY_CONFLICT, "outcome.outcome_id", error.identity)


def append_broker_truth_snapshot(ledger: RiskLedger, value: BrokerTruthSnapshot) -> bool:
    """Append a strict broker-truth snapshot; no provider or broker call occurs."""

    raw = broker_truth_snapshot_bytes(value)
    if value.supersedes_snapshot_id is not None:
        _verify_exact_correction(
            current_id=value.snapshot_id,
            supersedes_id=value.supersedes_snapshot_id,
            supersedes_sha256=value.supersedes_snapshot_sha256,
            records=_stored_snapshots(ledger),
            id_field="snapshot_id",
            identity=lambda record: broker_truth_snapshot_sha256(record),
            path="snapshot.supersedes",
        )
    try:
        return ledger.append_broker_truth_snapshot(
            values=_snapshot_values(value),
            payload=raw,
            payload_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except ImmutableEpisodeConflict as error:
        _reject(EpisodicReason.IDENTITY_CONFLICT, "snapshot.snapshot_id", error.identity)


def _nonnegative_int(value: object, *, path: str) -> int:
    if type(value) is not int or value < 0:
        _reject(EpisodicReason.INVALID_STATE, path, "must be a non-negative integer")
    return value


def _summary_limit(value: object, *, path: str) -> int:
    if type(value) is not int or not 1 <= value <= 64:
        _reject(EpisodicReason.INVALID_STATE, path, "must be an integer from 1 through 64")
    return value


def _candidate_id_tuple(
    value: object,
    *,
    path: str,
    require_canonical_order: bool,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _reject(EpisodicReason.INVALID_DOCUMENT, path, "must be an identifier list")
    candidates = tuple(
        _identifier(item, path=f"{path}[{index}]") for index, item in enumerate(value)
    )
    if len(set(candidates)) != len(candidates):
        _reject(EpisodicReason.INVALID_STATE, path, "candidate ids must be unique")
    canonical = tuple(sorted(candidates))
    if require_canonical_order and candidates != canonical:
        _reject(EpisodicReason.NON_CANONICAL_DOCUMENT, path, "candidate ids must be sorted")
    return canonical


def _canonical_decimal(value: Decimal) -> str:
    if not value.is_finite():
        _reject(EpisodicReason.INVALID_DECIMAL, "summary.net_pnl", "must be finite")
    result = format(value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result == "-0" else result


def _nullable_choice(value: object, *, path: str, allowed: frozenset[str]) -> str | None:
    if value is None:
        return None
    return _choice(value, path=path, allowed=allowed)


def _nullable_summary_reason(value: object, *, path: str) -> str | None:
    if value is None:
        return None
    return _choice(value, path=path, allowed=_SUMMARY_UNAVAILABLE_REASONS)


def _summary_row_payload(value: object) -> dict[str, object]:
    if not isinstance(value, EpisodicSummaryRow):
        _reject(
            EpisodicReason.INVALID_DOCUMENT,
            "summary.rows",
            "must contain EpisodicSummaryRow values",
        )
    row = EpisodicSummaryRow(
        episode_id=_identifier(value.episode_id, path="summary.row.episode_id"),
        episode_sha256=_sha256(value.episode_sha256, path="summary.row.episode_sha256"),
        event_id=_identifier(value.event_id, path="summary.row.event_id"),
        candidate_id=_identifier(value.candidate_id, path="summary.row.candidate_id"),
        symbol=_symbol(value.symbol, path="summary.row.symbol"),
        occurred_at=value.occurred_at,
        decision_cutoff_at=value.decision_cutoff_at,
        source_policy_sha256=_sha256(
            value.source_policy_sha256, path="summary.row.source_policy_sha256"
        ),
        model_config_sha256=_sha256(
            value.model_config_sha256, path="summary.row.model_config_sha256"
        ),
        decision_sha256=_sha256(value.decision_sha256, path="summary.row.decision_sha256"),
        disposition=_choice(
            value.disposition, path="summary.row.disposition", allowed=_DECISION_DISPOSITIONS
        ),
        direction=_choice(value.direction, path="summary.row.direction", allowed=_DIRECTIONS),
        compatibility=_choice(
            value.compatibility, path="summary.row.compatibility", allowed=_SUMMARY_COMPATIBILITIES
        ),
        outcome_id=_nullable_identifier(value.outcome_id, path="summary.row.outcome_id"),
        lifecycle_outcome=_nullable_choice(
            value.lifecycle_outcome,
            path="summary.row.lifecycle_outcome",
            allowed=_LIFECYCLE_OUTCOMES,
        ),
        pnl_classification=_nullable_choice(
            value.pnl_classification,
            path="summary.row.pnl_classification",
            allowed=_PNL_CLASSIFICATIONS,
        ),
        gross_pnl=_nullable_decimal(value.gross_pnl, path="summary.row.gross_pnl"),
        net_pnl=_nullable_decimal(value.net_pnl, path="summary.row.net_pnl"),
        outcome_unavailable_reason=_nullable_summary_reason(
            value.outcome_unavailable_reason,
            path="summary.row.outcome_unavailable_reason",
        ),
    )
    occurred_at = _timestamp(row.occurred_at, path="summary.row.occurred_at")
    cutoff_at = _timestamp(row.decision_cutoff_at, path="summary.row.decision_cutoff_at")
    if row.occurred_at > row.decision_cutoff_at:
        _reject(EpisodicReason.INVALID_STATE, "summary.row", "decision clocks are inconsistent")
    if row.disposition == "ACCEPTED" and row.direction not in {"UP", "DOWN"}:
        _reject(
            EpisodicReason.INVALID_STATE, "summary.row.direction", "accepted requires UP or DOWN"
        )
    if row.disposition != "ACCEPTED" and row.direction != "UNCERTAIN":
        _reject(
            EpisodicReason.INVALID_STATE, "summary.row.direction", "non-accepted requires UNCERTAIN"
        )
    if row.compatibility != "COMPATIBLE":
        if (
            row.outcome_unavailable_reason != row.compatibility
            or row.outcome_id is not None
            or row.lifecycle_outcome is not None
            or row.pnl_classification is not None
            or row.gross_pnl is not None
            or row.net_pnl is not None
        ):
            _reject(
                EpisodicReason.INVALID_STATE,
                "summary.row",
                "incompatible rows must be labelled without outcome values",
            )
    elif row.outcome_unavailable_reason is None:
        if row.outcome_id is None or row.lifecycle_outcome not in {"CLOSED", "CANCELED"}:
            _reject(
                EpisodicReason.INVALID_STATE,
                "summary.row",
                "available rows require a terminal outcome identity",
            )
        if row.lifecycle_outcome == "CLOSED" and (
            row.pnl_classification != "REALIZED" or row.gross_pnl is None or row.net_pnl is None
        ):
            _reject(EpisodicReason.INVALID_STATE, "summary.row", "closed rows require realized PnL")
        if row.lifecycle_outcome == "CANCELED" and (
            row.pnl_classification != "NOT_APPLICABLE"
            or row.gross_pnl is not None
            or row.net_pnl is not None
        ):
            _reject(EpisodicReason.INVALID_STATE, "summary.row", "canceled rows require no PnL")
    elif row.gross_pnl is not None or row.net_pnl is not None:
        _reject(
            EpisodicReason.INVALID_STATE,
            "summary.row",
            "unavailable outcomes cannot expose inferred PnL",
        )
    return {
        "episode_id": row.episode_id,
        "episode_sha256": row.episode_sha256,
        "event_id": row.event_id,
        "candidate_id": row.candidate_id,
        "symbol": row.symbol,
        "occurred_at": occurred_at,
        "decision_cutoff_at": cutoff_at,
        "source_policy_sha256": row.source_policy_sha256,
        "model_config_sha256": row.model_config_sha256,
        "decision_sha256": row.decision_sha256,
        "disposition": row.disposition,
        "direction": row.direction,
        "compatibility": row.compatibility,
        "outcome_id": row.outcome_id,
        "lifecycle_outcome": row.lifecycle_outcome,
        "pnl_classification": row.pnl_classification,
        "gross_pnl": row.gross_pnl,
        "net_pnl": row.net_pnl,
        "outcome_unavailable_reason": row.outcome_unavailable_reason,
    }


def _summary_totals(rows: Sequence[EpisodicSummaryRow]) -> tuple[int, int, str, int, int]:
    completed = sum(1 for row in rows if row.outcome_unavailable_reason is None)
    realized_rows = [
        row
        for row in rows
        if row.outcome_unavailable_reason is None and row.lifecycle_outcome == "CLOSED"
    ]
    net_pnl = sum(
        (Decimal(row.net_pnl) for row in realized_rows if row.net_pnl is not None), Decimal()
    )
    route_failures = sum(row.outcome_unavailable_reason == "ROUTE_FAILURE" for row in rows)
    reconciliation_failures = sum(
        row.outcome_unavailable_reason == "RECONCILIATION_FAILURE" for row in rows
    )
    return (
        completed,
        len(realized_rows),
        _canonical_decimal(net_pnl),
        route_failures,
        reconciliation_failures,
    )


def _summary_unsigned_payload(value: object) -> dict[str, object]:
    if not isinstance(value, EpisodicSummary):
        _reject(EpisodicReason.INVALID_DOCUMENT, "summary", "must be an EpisodicSummary")
    as_of = _timestamp(value.as_of, path="summary.as_of")
    policy_sha256 = _sha256(value.policy_sha256, path="summary.policy_sha256")
    model_config_sha256 = _sha256(value.model_config_sha256, path="summary.model_config_sha256")
    if not isinstance(value.candidate_ids, tuple):
        _reject(EpisodicReason.INVALID_DOCUMENT, "summary.candidate_ids", "must be a tuple")
    candidate_ids = _candidate_id_tuple(
        value.candidate_ids, path="summary.candidate_ids", require_canonical_order=True
    )
    limit = _summary_limit(value.limit, path="summary.limit")
    if not isinstance(value.rows, tuple):
        _reject(EpisodicReason.INVALID_DOCUMENT, "summary.rows", "must be a tuple")
    if len(value.rows) > limit:
        _reject(EpisodicReason.INVALID_STATE, "summary.rows", "cannot exceed the declared limit")
    rows = tuple(value.rows)
    if len({row.episode_id for row in rows}) != len(rows):
        _reject(EpisodicReason.INVALID_STATE, "summary.rows", "episode ids must be unique")
    ordered_rows = tuple(
        sorted(rows, key=lambda row: (row.occurred_at, row.episode_id), reverse=True)
    )
    if rows != ordered_rows:
        _reject(EpisodicReason.NON_CANONICAL_DOCUMENT, "summary.rows", "rows must be newest-first")
    row_payloads = [_summary_row_payload(row) for row in rows]
    row_completed, row_realized, _, row_route_failures, row_reconciliation_failures = (
        _summary_totals(rows)
    )
    completed = _nonnegative_int(value.completed_count, path="summary.completed_count")
    realized = _nonnegative_int(value.realized_count, path="summary.realized_count")
    net_pnl = _decimal_text(value.net_pnl, path="summary.net_pnl")
    route_failures = _nonnegative_int(value.route_failure_count, path="summary.route_failure_count")
    reconciliation_failures = _nonnegative_int(
        value.reconciliation_failure_count,
        path="summary.reconciliation_failure_count",
    )
    if (
        completed < row_completed
        or realized < row_realized
        or realized > completed
        or route_failures < row_route_failures
        or reconciliation_failures < row_reconciliation_failures
    ):
        _reject(
            EpisodicReason.SUMMARY_SEMANTIC_MISMATCH,
            "summary",
            "all-history aggregate fields cannot be smaller than displayed-row totals",
        )
    candidate_filter_excluded_count = _nonnegative_int(
        value.candidate_filter_excluded_count,
        path="summary.candidate_filter_excluded_count",
    )
    snapshot_id = _nullable_identifier(
        value.latest_broker_truth_snapshot_id,
        path="summary.latest_broker_truth_snapshot_id",
    )
    snapshot_sha256 = _nullable_sha256(
        value.latest_broker_truth_snapshot_sha256,
        path="summary.latest_broker_truth_snapshot_sha256",
    )
    _correction_pair(snapshot_id, snapshot_sha256, path="summary.latest_broker_truth_snapshot")
    return {
        "schema": EPISODIC_SUMMARY_SCHEMA,
        "schema_version": EPISODIC_SCHEMA_VERSION,
        "as_of": as_of,
        "policy_sha256": policy_sha256,
        "model_config_sha256": model_config_sha256,
        "candidate_ids": list(candidate_ids),
        "limit": limit,
        "rows": row_payloads,
        "completed_count": completed,
        "realized_count": realized,
        "net_pnl": net_pnl,
        "route_failure_count": route_failures,
        "reconciliation_failure_count": reconciliation_failures,
        "candidate_filter_excluded_count": candidate_filter_excluded_count,
        "latest_broker_truth_snapshot_id": snapshot_id,
        "latest_broker_truth_snapshot_sha256": snapshot_sha256,
    }


def _summary_payload(value: object) -> dict[str, object]:
    unsigned = _summary_unsigned_payload(value)
    if not isinstance(value, EpisodicSummary):
        raise AssertionError("summary validation returned the wrong type")
    supplied = _sha256(value.summary_sha256, path="summary.summary_sha256")
    expected = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    if supplied != expected:
        _reject(
            EpisodicReason.HASH_MISMATCH, "summary.summary_sha256", "does not bind summary bytes"
        )
    return {**unsigned, "summary_sha256": supplied}


def episodic_summary_bytes(value: object) -> bytes:
    """Return strict canonical bytes for one self-identifying summary."""

    return _canonical_bytes(_summary_payload(value))


def episodic_summary_sha256(value: object) -> str:
    """Return the strict identity carried by a valid episodic summary."""

    _summary_payload(value)
    if not isinstance(value, EpisodicSummary):
        raise AssertionError("summary validation returned the wrong type")
    return value.summary_sha256


def _parse_summary_row(value: object, *, path: str) -> EpisodicSummaryRow:
    if not isinstance(value, Mapping):
        _reject(EpisodicReason.INVALID_DOCUMENT, path, "must be an object")
    _strict_object(value, path=path, fields=_SUMMARY_ROW_FIELDS)
    row = EpisodicSummaryRow(
        episode_id=_identifier(value["episode_id"], path=f"{path}.episode_id"),
        episode_sha256=_sha256(value["episode_sha256"], path=f"{path}.episode_sha256"),
        event_id=_identifier(value["event_id"], path=f"{path}.event_id"),
        candidate_id=_identifier(value["candidate_id"], path=f"{path}.candidate_id"),
        symbol=_symbol(value["symbol"], path=f"{path}.symbol"),
        occurred_at=_parse_timestamp(value["occurred_at"], path=f"{path}.occurred_at"),
        decision_cutoff_at=_parse_timestamp(
            value["decision_cutoff_at"], path=f"{path}.decision_cutoff_at"
        ),
        source_policy_sha256=_sha256(
            value["source_policy_sha256"], path=f"{path}.source_policy_sha256"
        ),
        model_config_sha256=_sha256(
            value["model_config_sha256"], path=f"{path}.model_config_sha256"
        ),
        decision_sha256=_sha256(value["decision_sha256"], path=f"{path}.decision_sha256"),
        disposition=_choice(
            value["disposition"], path=f"{path}.disposition", allowed=_DECISION_DISPOSITIONS
        ),
        direction=_choice(value["direction"], path=f"{path}.direction", allowed=_DIRECTIONS),
        compatibility=_choice(
            value["compatibility"], path=f"{path}.compatibility", allowed=_SUMMARY_COMPATIBILITIES
        ),
        outcome_id=_nullable_identifier(value["outcome_id"], path=f"{path}.outcome_id"),
        lifecycle_outcome=_nullable_choice(
            value["lifecycle_outcome"],
            path=f"{path}.lifecycle_outcome",
            allowed=_LIFECYCLE_OUTCOMES,
        ),
        pnl_classification=_nullable_choice(
            value["pnl_classification"],
            path=f"{path}.pnl_classification",
            allowed=_PNL_CLASSIFICATIONS,
        ),
        gross_pnl=_nullable_decimal(value["gross_pnl"], path=f"{path}.gross_pnl"),
        net_pnl=_nullable_decimal(value["net_pnl"], path=f"{path}.net_pnl"),
        outcome_unavailable_reason=_nullable_summary_reason(
            value["outcome_unavailable_reason"], path=f"{path}.outcome_unavailable_reason"
        ),
    )
    _summary_row_payload(row)
    return row


def parse_episodic_summary(raw: object) -> EpisodicSummary:
    """Parse one strict summary and verify both its semantic fields and own hash."""

    payload = _decode(raw, path="summary")
    _strict_object(payload, path="summary", fields=_SUMMARY_FIELDS)
    _schema(payload, schema=EPISODIC_SUMMARY_SCHEMA, path="summary")
    raw_candidate_ids = payload["candidate_ids"]
    if not isinstance(raw_candidate_ids, list):
        _reject(EpisodicReason.INVALID_DOCUMENT, "summary.candidate_ids", "must be a JSON list")
    raw_rows = payload["rows"]
    if not isinstance(raw_rows, list):
        _reject(EpisodicReason.INVALID_DOCUMENT, "summary.rows", "must be a JSON list")
    summary = EpisodicSummary(
        as_of=_parse_timestamp(payload["as_of"], path="summary.as_of"),
        policy_sha256=_sha256(payload["policy_sha256"], path="summary.policy_sha256"),
        model_config_sha256=_sha256(
            payload["model_config_sha256"], path="summary.model_config_sha256"
        ),
        candidate_ids=_candidate_id_tuple(
            raw_candidate_ids,
            path="summary.candidate_ids",
            require_canonical_order=True,
        ),
        limit=_summary_limit(payload["limit"], path="summary.limit"),
        rows=tuple(
            _parse_summary_row(row, path=f"summary.rows[{index}]")
            for index, row in enumerate(raw_rows)
        ),
        completed_count=_nonnegative_int(
            payload["completed_count"], path="summary.completed_count"
        ),
        realized_count=_nonnegative_int(payload["realized_count"], path="summary.realized_count"),
        net_pnl=_decimal_text(payload["net_pnl"], path="summary.net_pnl"),
        route_failure_count=_nonnegative_int(
            payload["route_failure_count"], path="summary.route_failure_count"
        ),
        reconciliation_failure_count=_nonnegative_int(
            payload["reconciliation_failure_count"], path="summary.reconciliation_failure_count"
        ),
        candidate_filter_excluded_count=_nonnegative_int(
            payload["candidate_filter_excluded_count"],
            path="summary.candidate_filter_excluded_count",
        ),
        latest_broker_truth_snapshot_id=_nullable_identifier(
            payload["latest_broker_truth_snapshot_id"],
            path="summary.latest_broker_truth_snapshot_id",
        ),
        latest_broker_truth_snapshot_sha256=_nullable_sha256(
            payload["latest_broker_truth_snapshot_sha256"],
            path="summary.latest_broker_truth_snapshot_sha256",
        ),
        summary_sha256=_sha256(payload["summary_sha256"], path="summary.summary_sha256"),
    )
    if raw != episodic_summary_bytes(summary):
        _reject(EpisodicReason.NON_CANONICAL_DOCUMENT, "summary", "bytes are not canonical")
    return summary


def _effective_records(
    records: Sequence[object],
    *,
    identity_field: str,
    supersedes_field: str,
) -> tuple[object, ...]:
    superseded = {
        getattr(record, supersedes_field)
        for record in records
        if getattr(record, supersedes_field) is not None
    }
    return tuple(record for record in records if getattr(record, identity_field) not in superseded)


def _compatibility(
    decision: DecisionEpisode,
    *,
    policy_sha256: str,
    model_config_sha256: str,
) -> str:
    policy_mismatch = decision.source_policy_sha256 != policy_sha256
    model_mismatch = decision.model_config_sha256 != model_config_sha256
    if policy_mismatch and model_mismatch:
        return "POLICY_AND_MODEL_MISMATCH"
    if policy_mismatch:
        return "POLICY_MISMATCH"
    if model_mismatch:
        return "MODEL_CONFIG_MISMATCH"
    return "COMPATIBLE"


def _latest_outcome_for_decision(
    outcomes: Sequence[OutcomeEpisode],
    *,
    decision: DecisionEpisode,
) -> OutcomeEpisode | None:
    linked = [outcome for outcome in outcomes if outcome.decision_episode_id == decision.episode_id]
    if not linked:
        return None
    return max(
        linked,
        key=lambda outcome: (
            outcome.terminal_at,
            outcome.observed_at,
            outcome.created_at,
            outcome.outcome_id,
        ),
    )


def _flat_snapshot_after(
    snapshots: Sequence[BrokerTruthSnapshot],
    *,
    terminal_at: datetime,
) -> bool:
    return any(snapshot.is_flat and snapshot.observed_at >= terminal_at for snapshot in snapshots)


def _unavailable_summary_row(
    decision: DecisionEpisode,
    *,
    compatibility: str,
    reason: str,
    outcome: OutcomeEpisode | None = None,
) -> EpisodicSummaryRow:
    return EpisodicSummaryRow(
        episode_id=decision.episode_id,
        episode_sha256=decision_episode_sha256(decision),
        event_id=decision.event_id,
        candidate_id=decision.candidate_id,
        symbol=decision.symbol,
        occurred_at=decision.occurred_at,
        decision_cutoff_at=decision.decision_cutoff_at,
        source_policy_sha256=decision.source_policy_sha256,
        model_config_sha256=decision.model_config_sha256,
        decision_sha256=decision.decision_sha256,
        disposition=decision.disposition,
        direction=decision.direction,
        compatibility=compatibility,
        outcome_id=None if outcome is None else outcome.outcome_id,
        lifecycle_outcome=None if outcome is None else outcome.lifecycle_outcome,
        pnl_classification=None if outcome is None else outcome.pnl_classification,
        gross_pnl=None,
        net_pnl=None,
        outcome_unavailable_reason=reason,
    )


def _summary_row_for_decision(
    decision: DecisionEpisode,
    *,
    as_of: datetime,
    policy_sha256: str,
    model_config_sha256: str,
    outcomes: Sequence[OutcomeEpisode],
    snapshots: Sequence[BrokerTruthSnapshot],
) -> EpisodicSummaryRow:
    compatibility = _compatibility(
        decision,
        policy_sha256=policy_sha256,
        model_config_sha256=model_config_sha256,
    )
    if compatibility != "COMPATIBLE":
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason=compatibility,
        )
    outcome = _latest_outcome_for_decision(outcomes, decision=decision)
    if outcome is None:
        return _unavailable_summary_row(decision, compatibility=compatibility, reason="NO_OUTCOME")
    if outcome.event_id != decision.event_id:
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason="OUTCOME_LINK_MISMATCH",
            outcome=outcome,
        )
    if outcome.lifecycle_outcome == "ROUTE_FAILED":
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason="ROUTE_FAILURE",
            outcome=outcome,
        )
    if outcome.lifecycle_outcome == "RECONCILIATION_FAILED":
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason="RECONCILIATION_FAILURE",
            outcome=outcome,
        )
    lifecycle_reasons = {
        "OPEN": "OUTCOME_OPEN",
        "PARTIAL": "OUTCOME_PARTIAL",
        "MANUAL_REQUIRED": "OUTCOME_MANUAL_REQUIRED",
    }
    if outcome.lifecycle_outcome in lifecycle_reasons:
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason=lifecycle_reasons[outcome.lifecycle_outcome],
            outcome=outcome,
        )
    if outcome.terminal_at >= as_of:
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason="OUTCOME_AFTER_CUTOFF",
            outcome=outcome,
        )
    if outcome.observed_at > as_of:
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason="OUTCOME_OBSERVED_AFTER_CUTOFF",
            outcome=outcome,
        )
    if outcome.created_at > as_of:
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason="OUTCOME_CREATED_AFTER_CUTOFF",
            outcome=outcome,
        )
    if not _flat_snapshot_after(snapshots, terminal_at=outcome.terminal_at):
        reason = (
            "OUTCOME_CANCELED_UNVERIFIED"
            if outcome.lifecycle_outcome == "CANCELED"
            else "OUTCOME_BROKER_NOT_FLAT"
        )
        return _unavailable_summary_row(
            decision,
            compatibility=compatibility,
            reason=reason,
            outcome=outcome,
        )
    return EpisodicSummaryRow(
        episode_id=decision.episode_id,
        episode_sha256=decision_episode_sha256(decision),
        event_id=decision.event_id,
        candidate_id=decision.candidate_id,
        symbol=decision.symbol,
        occurred_at=decision.occurred_at,
        decision_cutoff_at=decision.decision_cutoff_at,
        source_policy_sha256=decision.source_policy_sha256,
        model_config_sha256=decision.model_config_sha256,
        decision_sha256=decision.decision_sha256,
        disposition=decision.disposition,
        direction=decision.direction,
        compatibility=compatibility,
        outcome_id=outcome.outcome_id,
        lifecycle_outcome=outcome.lifecycle_outcome,
        pnl_classification=outcome.pnl_classification,
        gross_pnl=outcome.gross_pnl,
        net_pnl=outcome.net_pnl,
        outcome_unavailable_reason=None,
    )


def build_episodic_summary(
    ledger: RiskLedger,
    *,
    as_of: object,
    policy_sha256: object,
    model_config_sha256: object,
    candidate_ids: object = (),
    limit: int = 16,
) -> EpisodicSummary:
    """Build one pure deterministic, cutoff-safe episodic context artifact."""

    if not isinstance(ledger, RiskLedger):
        _reject(EpisodicReason.INVALID_DOCUMENT, "ledger", "must be a RiskLedger")
    if not isinstance(as_of, datetime):
        _reject(EpisodicReason.INVALID_CLOCK, "as_of", "must be an aware UTC datetime")
    _timestamp(as_of, path="as_of")
    normalized_policy_sha256 = _sha256(policy_sha256, path="policy_sha256")
    normalized_model_config_sha256 = _sha256(model_config_sha256, path="model_config_sha256")
    normalized_candidate_ids = _candidate_id_tuple(
        candidate_ids,
        path="candidate_ids",
        require_canonical_order=False,
    )
    bounded_limit = _summary_limit(limit, path="limit")

    available_decisions = [
        decision
        for decision in _stored_decisions(ledger)
        if isinstance(decision, DecisionEpisode)
        and decision.occurred_at < as_of
        and decision.created_at <= as_of
    ]
    decisions = [
        decision
        for decision in _effective_records(
            available_decisions,
            identity_field="episode_id",
            supersedes_field="supersedes_episode_id",
        )
        if isinstance(decision, DecisionEpisode)
    ]
    if normalized_candidate_ids:
        candidate_set = frozenset(normalized_candidate_ids)
        candidate_filter_excluded_count = sum(
            decision.candidate_id not in candidate_set for decision in decisions
        )
        decisions = [decision for decision in decisions if decision.candidate_id in candidate_set]
    else:
        candidate_filter_excluded_count = 0
    ordered_decisions = tuple(
        sorted(
            decisions,
            key=lambda decision: (decision.occurred_at, decision.episode_id),
            reverse=True,
        )
    )
    available_outcomes = [
        outcome
        for outcome in _stored_outcomes(ledger)
        if isinstance(outcome, OutcomeEpisode)
        and outcome.terminal_at < as_of
        and outcome.observed_at <= as_of
        and outcome.created_at <= as_of
    ]
    outcomes = tuple(
        outcome
        for outcome in _effective_records(
            available_outcomes,
            identity_field="outcome_id",
            supersedes_field="supersedes_outcome_id",
        )
        if isinstance(outcome, OutcomeEpisode)
    )
    available_snapshots = [
        snapshot
        for snapshot in _stored_snapshots(ledger)
        if isinstance(snapshot, BrokerTruthSnapshot)
        and snapshot.observed_at <= as_of
        and snapshot.created_at <= as_of
    ]
    snapshots = tuple(
        snapshot
        for snapshot in _effective_records(
            available_snapshots,
            identity_field="snapshot_id",
            supersedes_field="supersedes_snapshot_id",
        )
        if isinstance(snapshot, BrokerTruthSnapshot)
    )
    all_rows = tuple(
        _summary_row_for_decision(
            decision,
            as_of=as_of,
            policy_sha256=normalized_policy_sha256,
            model_config_sha256=normalized_model_config_sha256,
            outcomes=outcomes,
            snapshots=snapshots,
        )
        for decision in ordered_decisions
    )
    rows = all_rows[:bounded_limit]
    completed, realized, net_pnl, route_failures, reconciliation_failures = _summary_totals(
        all_rows
    )
    latest_snapshot = (
        max(snapshots, key=lambda snapshot: (snapshot.observed_at, snapshot.snapshot_id))
        if snapshots
        else None
    )
    draft = EpisodicSummary(
        as_of=as_of,
        policy_sha256=normalized_policy_sha256,
        model_config_sha256=normalized_model_config_sha256,
        candidate_ids=normalized_candidate_ids,
        limit=bounded_limit,
        rows=rows,
        completed_count=completed,
        realized_count=realized,
        net_pnl=net_pnl,
        route_failure_count=route_failures,
        reconciliation_failure_count=reconciliation_failures,
        candidate_filter_excluded_count=candidate_filter_excluded_count,
        latest_broker_truth_snapshot_id=None
        if latest_snapshot is None
        else latest_snapshot.snapshot_id,
        latest_broker_truth_snapshot_sha256=(
            None if latest_snapshot is None else broker_truth_snapshot_sha256(latest_snapshot)
        ),
        summary_sha256=GENESIS_SUMMARY_SHA256,
    )
    identity = hashlib.sha256(_canonical_bytes(_summary_unsigned_payload(draft))).hexdigest()
    return EpisodicSummary(
        as_of=draft.as_of,
        policy_sha256=draft.policy_sha256,
        model_config_sha256=draft.model_config_sha256,
        candidate_ids=draft.candidate_ids,
        limit=draft.limit,
        rows=draft.rows,
        completed_count=draft.completed_count,
        realized_count=draft.realized_count,
        net_pnl=draft.net_pnl,
        route_failure_count=draft.route_failure_count,
        reconciliation_failure_count=draft.reconciliation_failure_count,
        candidate_filter_excluded_count=draft.candidate_filter_excluded_count,
        latest_broker_truth_snapshot_id=draft.latest_broker_truth_snapshot_id,
        latest_broker_truth_snapshot_sha256=draft.latest_broker_truth_snapshot_sha256,
        summary_sha256=identity,
    )


def validate_episodic_summary(ledger: RiskLedger, value: EpisodicSummary) -> EpisodicSummary:
    """Validate that a summary's semantics still exactly match durable ledger facts."""

    expected = build_episodic_summary(
        ledger,
        as_of=value.as_of,
        policy_sha256=value.policy_sha256,
        model_config_sha256=value.model_config_sha256,
        candidate_ids=value.candidate_ids,
        limit=value.limit,
    )
    if episodic_summary_bytes(value) != episodic_summary_bytes(expected):
        _reject(
            EpisodicReason.SUMMARY_SEMANTIC_MISMATCH,
            "summary",
            "does not match the ledger-derived summary",
        )
    return value
