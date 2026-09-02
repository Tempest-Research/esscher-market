"""Immutable, canonical PAPER strategy release and release-log contracts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

STRATEGY_RELEASE_SCHEMA: Final = "esscher.strategy_release"
ARM_RECORD_SCHEMA: Final = "esscher.arm_record"
SCHEMA_VERSION: Final = 1
PAPER_MODE: Final = "PAPER"
EXPECTED_LANE_BINDINGS: Final = (
    ("EARNINGS_RESIDUAL_CONTINUATION_V1", "EARNINGS_RESIDUAL_CONTINUATION_V2"),
    ("SPY_QQQ_INTRADAY_REGIME_SPREAD_V1", "MARKET_ANCHOR_INTRADAY_CONTINUATION_V1"),
    ("LIQUID_STOCK_CATALYST_SPREAD_V1", "LIQUID_STOCK_CATALYST_CONTINUATION_V1"),
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_RELEASE_FIELDS = frozenset(
    {
        "autonomy_policy_id",
        "build_artifact_sha256",
        "code_revision",
        "created_at",
        "evidence_qualified",
        "evidence_report_sha256",
        "lane_bindings",
        "latency_profile_id",
        "lifecycle_policy_id",
        "mode",
        "reasoner_model",
        "reasoner_route_id",
        "reasoner_schema_id",
        "release_id",
        "release_sha256",
        "release_version",
        "risk_policy_id",
        "schema",
        "schema_version",
        "security_passed",
        "security_report_sha256",
        "source_matrix_id",
        "strategy_policy_id",
        "supersedes_release_sha256",
    }
)
_ARM_FIELDS = frozenset(
    {
        "account_capability_id",
        "arm_id",
        "arm_sha256",
        "expires_at",
        "flatten_authority",
        "ledger_id",
        "process_id",
        "recovery_authority",
        "release_sha256",
        "schema",
        "schema_version",
        "source_ids",
        "starts_at",
    }
)


class StrategyReleaseRejected(ValueError):
    """Raised when a release or arm fails its closed contract."""


class PromotionStatus(StrEnum):
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class PromotionReason(StrEnum):
    INVALID_REFERENCE = "INVALID_REFERENCE"
    PAPER_MODE_REQUIRED = "PAPER_MODE_REQUIRED"
    SEMANTIC_ID_MISMATCH = "SEMANTIC_ID_MISMATCH"
    LANE_BINDINGS_MISMATCH = "LANE_BINDINGS_MISMATCH"
    REASONER_ROUTE_INELIGIBLE = "REASONER_ROUTE_INELIGIBLE"
    LATENCY_PROFILE_INELIGIBLE = "LATENCY_PROFILE_INELIGIBLE"
    EVIDENCE_UNQUALIFIED = "EVIDENCE_UNQUALIFIED"
    SECURITY_FAILED = "SECURITY_FAILED"


class ReleaseLogReason(StrEnum):
    INVALID_HASH = "INVALID_HASH"
    NOT_FOUND = "NOT_FOUND"
    PROMOTION_REJECTED = "PROMOTION_REJECTED"
    DECISION_MISMATCH = "DECISION_MISMATCH"
    PREDECESSOR_MISMATCH = "PREDECESSOR_MISMATCH"
    HASH_COLLISION = "HASH_COLLISION"
    SUPERSEDED = "SUPERSEDED"
    REVOKED = "REVOKED"
    REVOCATION_CONFLICT = "REVOCATION_CONFLICT"
    STORED_RELEASE_INVALID = "STORED_RELEASE_INVALID"


class RevocationReason(StrEnum):
    SECURITY = "SECURITY"
    EVIDENCE_INVALIDATED = "EVIDENCE_INVALIDATED"
    OPERATOR = "OPERATOR"


@dataclass(frozen=True, slots=True)
class StrategyRelease:
    release_id: str
    release_version: int
    created_at: datetime
    mode: str
    code_revision: str
    build_artifact_sha256: str
    evidence_report_sha256: str
    security_report_sha256: str
    evidence_qualified: bool
    security_passed: bool
    autonomy_policy_id: str
    strategy_policy_id: str
    reasoner_route_id: str
    reasoner_model: str
    reasoner_schema_id: str
    latency_profile_id: str
    source_matrix_id: str
    risk_policy_id: str
    lifecycle_policy_id: str
    lane_bindings: tuple[tuple[str, str], ...]
    supersedes_release_sha256: str | None = None

    @property
    def release_sha256(self) -> str:
        return _sha256(_canonical_json(_release_payload(self)))


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    release_sha256: str | None
    status: PromotionStatus
    reason: PromotionReason | None


@dataclass(frozen=True, slots=True)
class ArmRecord:
    arm_id: str
    release_sha256: str
    account_capability_id: str
    source_ids: tuple[str, ...]
    starts_at: datetime
    expires_at: datetime
    ledger_id: str
    process_id: str
    flatten_authority: bool
    recovery_authority: bool

    @property
    def arm_sha256(self) -> str:
        return _sha256(_canonical_json(_arm_payload(self)))


class ReleaseLogRejected(RuntimeError):
    """Raised when an append-only release-log operation is refused."""

    def __init__(self, reason: ReleaseLogReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _reject(detail: str) -> None:
    raise StrategyReleaseRejected(detail)


def _identifier(value: object, path: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _reject(f"{path} must be a normalized identifier")
    return value


def _digest(value: object, path: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _reject(f"{path} must be a lowercase SHA-256 digest")
    return value


def _timestamp_text(value: object, path: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _reject(f"{path} must be a timezone-aware datetime")
    normalized = value.astimezone(UTC)
    if normalized.microsecond:
        _reject(f"{path} must have whole-second precision")
    return normalized.strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp(value: object, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _reject(f"{path} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as error:
        raise StrategyReleaseRejected(f"{path} must be a canonical UTC timestamp") from error
    if _timestamp_text(parsed, path) != value:
        _reject(f"{path} must be a canonical UTC timestamp")
    return parsed


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "ascii"
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant {value}")


def _object(raw: bytes, fields: frozenset[str], path: str) -> dict[str, object]:
    if type(raw) is not bytes:
        _reject(f"{path} must be immutable bytes")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=_pairs, parse_constant=_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise StrategyReleaseRejected(f"{path} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict) or set(value) != fields:
        _reject(f"{path} must contain exactly the closed schema fields")
    return value


def _positive(value: object, path: str) -> int:
    if type(value) is not int or value < 1:
        _reject(f"{path} must be a positive integer")
    return value


def _bindings(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, tuple) or len(value) != 3:
        _reject("lane_bindings must be an exact immutable three-item tuple")
    result: list[tuple[str, str]] = []
    for index, binding in enumerate(value):
        if not isinstance(binding, tuple) or len(binding) != 2:
            _reject(f"lane_bindings[{index}] must be a pair")
        result.append(
            (
                _identifier(binding[0], f"lane_bindings[{index}][0]"),
                _identifier(binding[1], f"lane_bindings[{index}][1]"),
            )
        )
    return tuple(result)


def _release_payload(value: StrategyRelease) -> dict[str, object]:
    if not isinstance(value, StrategyRelease):
        _reject("release must be a StrategyRelease")
    if value.mode != PAPER_MODE:
        _reject("mode must be permanently PAPER")
    if not isinstance(value.code_revision, str) or _REVISION.fullmatch(value.code_revision) is None:
        _reject("code_revision must be a lowercase Git SHA-1 or SHA-256")
    if type(value.evidence_qualified) is not bool or type(value.security_passed) is not bool:
        _reject("report qualifications must be booleans")
    if value.supersedes_release_sha256 is not None:
        _digest(value.supersedes_release_sha256, "supersedes_release_sha256")
    ids = {
        name: _identifier(getattr(value, name), name)
        for name in (
            "autonomy_policy_id",
            "strategy_policy_id",
            "reasoner_route_id",
            "reasoner_model",
            "reasoner_schema_id",
            "latency_profile_id",
            "source_matrix_id",
            "risk_policy_id",
            "lifecycle_policy_id",
        )
    }
    return {
        **ids,
        "schema": STRATEGY_RELEASE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "release_id": _identifier(value.release_id, "release_id"),
        "release_version": _positive(value.release_version, "release_version"),
        "created_at": _timestamp_text(value.created_at, "created_at"),
        "mode": value.mode,
        "code_revision": value.code_revision,
        "build_artifact_sha256": _digest(value.build_artifact_sha256, "build_artifact_sha256"),
        "evidence_report_sha256": _digest(value.evidence_report_sha256, "evidence_report_sha256"),
        "security_report_sha256": _digest(value.security_report_sha256, "security_report_sha256"),
        "evidence_qualified": value.evidence_qualified,
        "security_passed": value.security_passed,
        "lane_bindings": [
            {"owner_lane_id": lane, "candidate_id": candidate}
            for lane, candidate in _bindings(value.lane_bindings)
        ],
        "supersedes_release_sha256": value.supersedes_release_sha256,
    }


def strategy_release_bytes(value: StrategyRelease) -> bytes:
    """Serialize a validated release in its unique canonical representation."""
    payload = _release_payload(value)
    return _canonical_json({**payload, "release_sha256": _sha256(_canonical_json(payload))})


def _text(data: dict[str, object], key: str) -> str:
    return data[key] if isinstance(data[key], str) else ""


def parse_strategy_release(raw: bytes) -> StrategyRelease:
    """Parse only immutable, exact canonical StrategyRelease bytes."""
    data = _object(raw, _RELEASE_FIELDS, "release")
    if data["schema"] != STRATEGY_RELEASE_SCHEMA or data["schema_version"] != SCHEMA_VERSION:
        _reject("release schema mismatch")
    raw_bindings = data["lane_bindings"]
    if not isinstance(raw_bindings, list):
        _reject("lane_bindings must be an array")
    bindings: list[tuple[str, str]] = []
    for index, binding in enumerate(raw_bindings):
        if not isinstance(binding, dict) or set(binding) != {"owner_lane_id", "candidate_id"}:
            _reject(f"lane_bindings[{index}] must be a closed mapping")
        bindings.append(
            (
                _identifier(binding["owner_lane_id"], "owner_lane_id"),
                _identifier(binding["candidate_id"], "candidate_id"),
            )
        )
    value = StrategyRelease(
        release_id=_text(data, "release_id"),
        release_version=_positive(data["release_version"], "release_version"),
        created_at=_timestamp(data["created_at"], "created_at"),
        mode=_text(data, "mode"),
        code_revision=_text(data, "code_revision"),
        build_artifact_sha256=_text(data, "build_artifact_sha256"),
        evidence_report_sha256=_text(data, "evidence_report_sha256"),
        security_report_sha256=_text(data, "security_report_sha256"),
        evidence_qualified=data["evidence_qualified"],
        security_passed=data["security_passed"],
        autonomy_policy_id=_text(data, "autonomy_policy_id"),
        strategy_policy_id=_text(data, "strategy_policy_id"),
        reasoner_route_id=_text(data, "reasoner_route_id"),
        reasoner_model=_text(data, "reasoner_model"),
        reasoner_schema_id=_text(data, "reasoner_schema_id"),
        latency_profile_id=_text(data, "latency_profile_id"),
        source_matrix_id=_text(data, "source_matrix_id"),
        risk_policy_id=_text(data, "risk_policy_id"),
        lifecycle_policy_id=_text(data, "lifecycle_policy_id"),
        lane_bindings=tuple(bindings),
        supersedes_release_sha256=data["supersedes_release_sha256"],
    )
    if value.release_sha256 != _digest(data["release_sha256"], "release_sha256"):
        _reject("release self-hash mismatch")
    if strategy_release_bytes(value) != raw:
        _reject("release bytes are not canonical")
    return value


def current_semantic_ids() -> dict[str, str]:
    """Return identifiers from current packaged, validated policy objects."""
    from ringdown_market.autonomy.policy import load_autonomous_policy
    from ringdown_market.contracts.execution_policy import PAPER_PERMIT_POLICY_VERSION
    from ringdown_market.contracts.latency_profile import load_latency_profile
    from ringdown_market.contracts.reasoner_route import load_approved_reasoner_route_v2
    from ringdown_market.contracts.source_matrix import load_source_matrix
    from ringdown_market.risk.policy import load_risk_policy_v2
    from ringdown_market.strategy.policy import load_strategy_policy_v2

    route = load_approved_reasoner_route_v2()
    return {
        "autonomy_policy_id": load_autonomous_policy().policy_id,
        "strategy_policy_id": load_strategy_policy_v2().policy_id,
        "reasoner_route_id": route.route_id,
        "reasoner_model": route.model,
        "reasoner_schema_id": route.provider_request_policy.output_schema_name,
        "latency_profile_id": load_latency_profile().profile_id,
        "source_matrix_id": load_source_matrix().matrix_id,
        "risk_policy_id": load_risk_policy_v2().policy_id,
        "lifecycle_policy_id": PAPER_PERMIT_POLICY_VERSION,
    }


def evaluate_release(value: StrategyRelease) -> PromotionDecision:
    """Deterministically decide whether a release satisfies current package gates."""
    try:
        release_sha256 = value.release_sha256
        _release_payload(value)
    except (AttributeError, StrategyReleaseRejected):
        return PromotionDecision(None, PromotionStatus.REJECTED, PromotionReason.INVALID_REFERENCE)
    expected = current_semantic_ids()
    if any(getattr(value, key) != expected_value for key, expected_value in expected.items()):
        return PromotionDecision(
            release_sha256, PromotionStatus.REJECTED, PromotionReason.SEMANTIC_ID_MISMATCH
        )
    if value.lane_bindings != EXPECTED_LANE_BINDINGS:
        return PromotionDecision(
            release_sha256, PromotionStatus.REJECTED, PromotionReason.LANE_BINDINGS_MISMATCH
        )
    from ringdown_market.contracts.latency_profile import load_latency_profile
    from ringdown_market.contracts.reasoner_route import (
        ApprovalState,
        load_approved_reasoner_route_v2,
    )

    route = load_approved_reasoner_route_v2()
    if route.approval_state is not ApprovalState.APPROVED or not route.evaluation_eligible:
        return PromotionDecision(
            release_sha256, PromotionStatus.REJECTED, PromotionReason.REASONER_ROUTE_INELIGIBLE
        )
    if not load_latency_profile().promotion_eligible:
        return PromotionDecision(
            release_sha256, PromotionStatus.REJECTED, PromotionReason.LATENCY_PROFILE_INELIGIBLE
        )
    if not value.evidence_qualified:
        return PromotionDecision(
            release_sha256, PromotionStatus.REJECTED, PromotionReason.EVIDENCE_UNQUALIFIED
        )
    if not value.security_passed:
        return PromotionDecision(
            release_sha256, PromotionStatus.REJECTED, PromotionReason.SECURITY_FAILED
        )
    return PromotionDecision(release_sha256, PromotionStatus.PROMOTED, None)


def _arm_payload(value: ArmRecord) -> dict[str, object]:
    if not isinstance(value, ArmRecord) or not isinstance(value.source_ids, tuple):
        _reject("arm and source_ids must be immutable records")
    if type(value.flatten_authority) is not bool or type(value.recovery_authority) is not bool:
        _reject("arm authorities must be booleans")
    if not value.flatten_authority or not value.recovery_authority:
        _reject("arm must retain flatten and recovery authority")
    source_ids = tuple(
        _identifier(item, f"source_ids[{index}]") for index, item in enumerate(value.source_ids)
    )
    if not source_ids or source_ids != tuple(sorted(set(source_ids))):
        _reject("source_ids must be non-empty, unique, and sorted")
    starts_at, expires_at = (
        _timestamp_text(value.starts_at, "starts_at"),
        _timestamp_text(value.expires_at, "expires_at"),
    )
    if value.expires_at.astimezone(UTC) <= value.starts_at.astimezone(UTC):
        _reject("arm expiry must be after its start")
    return {
        "schema": ARM_RECORD_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "arm_id": _identifier(value.arm_id, "arm_id"),
        "release_sha256": _digest(value.release_sha256, "release_sha256"),
        "account_capability_id": _identifier(value.account_capability_id, "account_capability_id"),
        "source_ids": list(source_ids),
        "starts_at": starts_at,
        "expires_at": expires_at,
        "ledger_id": _identifier(value.ledger_id, "ledger_id"),
        "process_id": _identifier(value.process_id, "process_id"),
        "flatten_authority": value.flatten_authority,
        "recovery_authority": value.recovery_authority,
    }


def arm_record_bytes(value: ArmRecord) -> bytes:
    """Serialize one validated arm record canonically."""
    payload = _arm_payload(value)
    return _canonical_json({**payload, "arm_sha256": _sha256(_canonical_json(payload))})


def parse_arm_record(raw: bytes) -> ArmRecord:
    """Parse only immutable, exact canonical ArmRecord bytes."""
    data = _object(raw, _ARM_FIELDS, "arm")
    if data["schema"] != ARM_RECORD_SCHEMA or data["schema_version"] != SCHEMA_VERSION:
        _reject("arm schema mismatch")
    source_ids = data["source_ids"]
    if not isinstance(source_ids, list):
        _reject("source_ids must be an array")
    value = ArmRecord(
        arm_id=_text(data, "arm_id"),
        release_sha256=_text(data, "release_sha256"),
        account_capability_id=_text(data, "account_capability_id"),
        source_ids=tuple(source_ids),
        starts_at=_timestamp(data["starts_at"], "starts_at"),
        expires_at=_timestamp(data["expires_at"], "expires_at"),
        ledger_id=_text(data, "ledger_id"),
        process_id=_text(data, "process_id"),
        flatten_authority=data["flatten_authority"],
        recovery_authority=data["recovery_authority"],
    )
    if value.arm_sha256 != _digest(data["arm_sha256"], "arm_sha256"):
        _reject("arm self-hash mismatch")
    if arm_record_bytes(value) != raw:
        _reject("arm bytes are not canonical")
    return value


class ReleaseLog:
    """Minimal append-only SQLite release log with exact-hash loading only."""

    def __init__(self, path: str | Path) -> None:
        self._connection: sqlite3.Connection | None = sqlite3.connect(path)
        self._require_connection().executescript("""
            CREATE TABLE IF NOT EXISTS strategy_releases (
                sequence INTEGER PRIMARY KEY, release_sha256 TEXT UNIQUE NOT NULL,
                release_json BLOB NOT NULL, supersedes_release_sha256 TEXT
            );
            CREATE TABLE IF NOT EXISTS strategy_release_revocations (
                release_sha256 TEXT PRIMARY KEY, reason TEXT NOT NULL,
                operator_id TEXT NOT NULL, revoked_at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS releases_no_update
            BEFORE UPDATE ON strategy_releases BEGIN SELECT RAISE(ABORT, 'append only'); END;
            CREATE TRIGGER IF NOT EXISTS releases_no_delete
            BEFORE DELETE ON strategy_releases BEGIN SELECT RAISE(ABORT, 'append only'); END;
            CREATE TRIGGER IF NOT EXISTS revocations_no_update
            BEFORE UPDATE ON strategy_release_revocations
            BEGIN SELECT RAISE(ABORT, 'append only'); END;
            CREATE TRIGGER IF NOT EXISTS revocations_no_delete
            BEFORE DELETE ON strategy_release_revocations
            BEGIN SELECT RAISE(ABORT, 'append only'); END;
        """)

    def __enter__(self) -> ReleaseLog:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the SQLite connection; repeated closes are harmless."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def promote(self, value: StrategyRelease, decision: PromotionDecision) -> StrategyRelease:
        """Append a recomputed promoted release, or replay an exact append."""
        actual = evaluate_release(value)
        if decision != actual:
            raise ReleaseLogRejected(ReleaseLogReason.DECISION_MISMATCH)
        if actual.status is not PromotionStatus.PROMOTED:
            raise ReleaseLogRejected(ReleaseLogReason.PROMOTION_REJECTED)
        raw, connection = strategy_release_bytes(value), self._require_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT release_json FROM strategy_releases WHERE release_sha256 = ?",
                (value.release_sha256,),
            ).fetchone()
            if existing is not None:
                stored = existing[0]
                if type(stored) is bytes:
                    stored_raw = stored
                elif isinstance(stored, (bytearray, memoryview)):
                    stored_raw = bytes(stored)
                else:
                    raise ReleaseLogRejected(ReleaseLogReason.STORED_RELEASE_INVALID)
                if stored_raw == raw:
                    connection.commit()
                    return value
                raise ReleaseLogRejected(ReleaseLogReason.HASH_COLLISION)
            prior = connection.execute(
                "SELECT release_sha256 FROM strategy_releases ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if value.supersedes_release_sha256 != (prior[0] if prior is not None else None):
                raise ReleaseLogRejected(ReleaseLogReason.PREDECESSOR_MISMATCH)
            connection.execute(
                "INSERT INTO strategy_releases "
                "(release_sha256, release_json, supersedes_release_sha256) VALUES (?, ?, ?)",
                (value.release_sha256, raw, value.supersedes_release_sha256),
            )
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return value

    def load_exact(self, release_sha256: str) -> StrategyRelease:
        """Load one current, unrevoked release by exact canonical hash."""
        self._log_digest(release_sha256)
        connection = self._require_connection()
        row = connection.execute(
            "SELECT release_json FROM strategy_releases WHERE release_sha256 = ?", (release_sha256,)
        ).fetchone()
        if row is None:
            raise ReleaseLogRejected(ReleaseLogReason.NOT_FOUND)
        if (
            connection.execute(
                "SELECT 1 FROM strategy_release_revocations WHERE release_sha256 = ?",
                (release_sha256,),
            ).fetchone()
            is not None
        ):
            raise ReleaseLogRejected(ReleaseLogReason.REVOKED)
        if (
            connection.execute(
                "SELECT 1 FROM strategy_releases WHERE supersedes_release_sha256 = ?",
                (release_sha256,),
            ).fetchone()
            is not None
        ):
            raise ReleaseLogRejected(ReleaseLogReason.SUPERSEDED)
        try:
            stored = row[0]
            if type(stored) is bytes:
                raw = stored
            elif isinstance(stored, (bytearray, memoryview)):
                raw = bytes(stored)
            else:
                raise StrategyReleaseRejected("stored release must be bytes")
            value = parse_strategy_release(raw)
        except (StrategyReleaseRejected, TypeError, ValueError) as error:
            raise ReleaseLogRejected(ReleaseLogReason.STORED_RELEASE_INVALID) from error
        if value.release_sha256 != release_sha256:
            raise ReleaseLogRejected(ReleaseLogReason.STORED_RELEASE_INVALID)
        return value

    def revoke(
        self,
        release_sha256: str,
        *,
        reason: RevocationReason,
        operator_id: str,
        revoked_at: datetime,
    ) -> None:
        """Append an idempotent closed-reason revocation for an existing release."""
        self._log_digest(release_sha256)
        if not isinstance(reason, RevocationReason):
            raise ReleaseLogRejected(ReleaseLogReason.REVOCATION_CONFLICT)
        try:
            operator_id, revoked_text = (
                _identifier(operator_id, "operator_id"),
                _timestamp_text(revoked_at, "revoked_at"),
            )
        except StrategyReleaseRejected as error:
            raise ReleaseLogRejected(ReleaseLogReason.REVOCATION_CONFLICT) from error
        connection = self._require_connection()
        with connection:
            if (
                connection.execute(
                    "SELECT 1 FROM strategy_releases WHERE release_sha256 = ?", (release_sha256,)
                ).fetchone()
                is None
            ):
                raise ReleaseLogRejected(ReleaseLogReason.NOT_FOUND)
            existing = connection.execute(
                "SELECT reason, operator_id, revoked_at FROM strategy_release_revocations "
                "WHERE release_sha256 = ?",
                (release_sha256,),
            ).fetchone()
            expected = (reason.value, operator_id, revoked_text)
            if existing is not None:
                if tuple(existing) == expected:
                    return
                raise ReleaseLogRejected(ReleaseLogReason.REVOCATION_CONFLICT)
            connection.execute(
                "INSERT INTO strategy_release_revocations "
                "(release_sha256, reason, operator_id, revoked_at) VALUES (?, ?, ?, ?)",
                (release_sha256, *expected),
            )

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("release log is closed")
        return self._connection

    @staticmethod
    def _log_digest(value: str) -> None:
        try:
            _digest(value, "release_sha256")
        except StrategyReleaseRejected as error:
            raise ReleaseLogRejected(ReleaseLogReason.INVALID_HASH) from error
