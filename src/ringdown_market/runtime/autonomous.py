"""Durable, fail-closed autonomous PAPER-session contracts.

This module deliberately contains coordination and persistence boundaries only.
It has no network, model, broker, account, or order implementation: those are
injected as typed ports by the coordinator added below.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final, NoReturn, Protocol
from zoneinfo import ZoneInfo

from ringdown_market.autonomy import autonomous_policy_sha256, load_autonomous_policy
from ringdown_market.contracts.execution_policy import ALPACA_MCP_CURRENT_PROTOCOL_SHA256
from ringdown_market.contracts.reasoner_route import load_current_approved_reasoner_route
from ringdown_market.risk import risk_policy_v2_sha256
from ringdown_market.strategy import load_strategy_policy_v2, strategy_policy_v2_sha256

AUTONOMOUS_SESSION_ARM_SCHEMA: Final = "esscher.autonomous_session_arm"
AUTONOMOUS_SESSION_ARM_SCHEMA_VERSION: Final = 1
AUTONOMOUS_WINDOW_SCHEMA: Final = "esscher.autonomous_session_window"
AUTONOMOUS_WINDOW_SCHEMA_VERSION: Final = 1
AUTONOMOUS_ACTIVE_LIFECYCLE_SCHEMA: Final = "esscher.autonomous_active_lifecycle"
AUTONOMOUS_ACTIVE_LIFECYCLE_SCHEMA_VERSION: Final = 1
AUTONOMOUS_SESSION_SUMMARY_SCHEMA: Final = "esscher.autonomous_session_summary"
AUTONOMOUS_SESSION_SUMMARY_SCHEMA_VERSION: Final = 1

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EASTERN = ZoneInfo("America/New_York")


class AutonomousArmRejected(ValueError):
    """Raised when an autonomous-session arm is malformed or semantically forged."""


class _DuplicateFieldError(ValueError):
    """Raised by the JSON object hook before duplicate fields are erased."""


def _reject(message: str) -> NoReturn:
    raise AutonomousArmRejected(message)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        _reject(f"canonical JSON encoding failed: {type(error).__name__}")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _unique_object(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateFieldError(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    _reject(f"non-standard JSON constant is forbidden: {value}")


def _strict_object(value: object, *, fields: frozenset[str], path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _reject(f"{path} must be an object")
    actual = set(value)
    missing = sorted(fields - actual)
    unknown = sorted(actual - fields)
    if missing or unknown:
        _reject(f"{path} field mismatch; missing={missing} unknown={unknown}")
    return value


def _identifier(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _reject(f"{path} must be a bounded identifier")
    return value


def _digest(value: object, *, path: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(f"{path} must be a lowercase SHA-256 digest")
    return value


def _utc_datetime(value: datetime, *, path: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        _reject(f"{path} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _timestamp_text(value: datetime, *, path: str) -> str:
    value = _utc_datetime(value, path=path)
    rendered = value.replace(tzinfo=None).isoformat(timespec="microseconds")
    if value.microsecond == 0:
        rendered = rendered[:19]
    return f"{rendered}Z"


def _timestamp(value: object, *, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _reject(f"{path} must be an explicit canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        _reject(f"{path} must be an explicit canonical UTC timestamp")
    parsed = _utc_datetime(parsed, path=path)
    if _timestamp_text(parsed, path=path) != value:
        _reject(f"{path} must be an explicit canonical UTC timestamp")
    return parsed


def _current_contract_hashes() -> tuple[str, str, str, str, str, str]:
    """Load the immutable package identities required by every valid arm."""

    try:
        owner_sha = autonomous_policy_sha256()
        strategy_sha = strategy_policy_v2_sha256()
        risk_sha = risk_policy_v2_sha256()
        execution_sha = ALPACA_MCP_CURRENT_PROTOCOL_SHA256
        route = load_current_approved_reasoner_route()
        if not route.evaluation_eligible:
            _reject("the packaged K3 V2 route is not evaluation eligible")
        return (
            owner_sha,
            strategy_sha,
            risk_sha,
            execution_sha,
            route.route_sha256,
            route.model_config_sha256,
        )
    except AutonomousArmRejected:
        raise
    except (ImportError, OSError, TypeError, ValueError) as error:
        _reject(f"frozen autonomous contracts are unavailable: {type(error).__name__}")


@dataclass(frozen=True, slots=True)
class AutonomousWindow:
    """One exact due window within an owner-approved autonomous session."""

    window_id: str
    opens_at: datetime
    closes_at: datetime
    candidate_ids: tuple[str, ...]

    @property
    def window_sha256(self) -> str:
        return _sha256(_canonical_json(_window_payload(self)))


@dataclass(frozen=True, slots=True)
class AutonomousSessionArm:
    """A closed, self-hashing PAPER-only session authorization.

    ``release_code_sha256`` and ``account_fingerprint_sha256`` are supplied as
    already-sanitized hashes.  Raw account, broker, provider, credential, and
    article values are intentionally absent from the schema.
    """

    session_id: str
    release_code_sha256: str
    account_fingerprint_sha256: str
    owner_policy_sha256: str
    strategy_policy_sha256: str
    risk_policy_sha256: str
    execution_protocol_sha256: str
    reasoner_route_sha256: str
    reasoner_model_config_sha256: str
    mode: str
    starts_at: datetime
    ends_at: datetime
    hard_flat_at: datetime
    windows: tuple[AutonomousWindow, ...]

    @classmethod
    def for_trading_date(
        cls,
        *,
        session_id: str,
        session_date: date,
        release_code_sha256: str,
        account_fingerprint_sha256: str,
    ) -> AutonomousSessionArm:
        """Build the exact 10:00--15:00 ET scan cadence for one PAPER date."""

        if not isinstance(session_date, date) or isinstance(session_date, datetime):
            _reject("session_date must be a calendar date")
        try:
            owner = load_autonomous_policy()
            policy = load_strategy_policy_v2()
        except (ImportError, OSError, TypeError, ValueError) as error:
            _reject(f"frozen autonomous contracts are unavailable: {type(error).__name__}")
        try:
            decision_times = tuple(
                time.fromisoformat(item) for item in owner.intraday_decision_times_et
            )
            hard_flat_time = time.fromisoformat(owner.hard_flat_time_et)
        except (TypeError, ValueError) as error:
            _reject(f"owner policy clocks are invalid: {type(error).__name__}")
        if not decision_times:
            _reject("owner policy contains no decision times")
        hard_flat_local = datetime.combine(session_date, hard_flat_time, tzinfo=_EASTERN)
        opening_times = tuple(
            datetime.combine(session_date, item, tzinfo=_EASTERN) for item in decision_times
        )
        windows = tuple(
            AutonomousWindow(
                window_id=f"SCAN_{opening.strftime('%H%M')}_ET",
                opens_at=opening.astimezone(UTC),
                closes_at=(
                    opening_times[index + 1] if index + 1 < len(opening_times) else hard_flat_local
                ).astimezone(UTC),
                candidate_ids=policy.candidate_ids,
            )
            for index, opening in enumerate(opening_times)
        )
        (
            owner_sha,
            strategy_sha,
            risk_sha,
            execution_sha,
            route_sha,
            model_sha,
        ) = _current_contract_hashes()
        return cls(
            session_id=session_id,
            release_code_sha256=release_code_sha256,
            account_fingerprint_sha256=account_fingerprint_sha256,
            owner_policy_sha256=owner_sha,
            strategy_policy_sha256=strategy_sha,
            risk_policy_sha256=risk_sha,
            execution_protocol_sha256=execution_sha,
            reasoner_route_sha256=route_sha,
            reasoner_model_config_sha256=model_sha,
            mode="PAPER",
            starts_at=opening_times[0].astimezone(UTC),
            ends_at=hard_flat_local.astimezone(UTC),
            hard_flat_at=hard_flat_local.astimezone(UTC),
            windows=windows,
        )

    @property
    def arm_sha256(self) -> str:
        return autonomous_session_arm_sha256(self)

    def to_json_bytes(self) -> bytes:
        return autonomous_session_arm_bytes(self)


def _window_payload(window: AutonomousWindow) -> dict[str, object]:
    _validate_window_shape(window)
    return {
        "candidate_ids": list(window.candidate_ids),
        "closes_at": _timestamp_text(window.closes_at, path="window.closes_at"),
        "opens_at": _timestamp_text(window.opens_at, path="window.opens_at"),
        "schema": AUTONOMOUS_WINDOW_SCHEMA,
        "schema_version": AUTONOMOUS_WINDOW_SCHEMA_VERSION,
        "window_id": window.window_id,
    }


def _validate_window_shape(window: AutonomousWindow) -> None:
    if not isinstance(window, AutonomousWindow):
        _reject("window must be an AutonomousWindow")
    _identifier(window.window_id, path="window.window_id")
    opens_at = _utc_datetime(window.opens_at, path="window.opens_at")
    closes_at = _utc_datetime(window.closes_at, path="window.closes_at")
    if closes_at <= opens_at:
        _reject("window must close after it opens")
    if not isinstance(window.candidate_ids, tuple) or not window.candidate_ids:
        _reject("window candidate_ids must be a non-empty tuple")
    for candidate_id in window.candidate_ids:
        _identifier(candidate_id, path="window.candidate_ids")


def _arm_unsigned_payload(arm: AutonomousSessionArm) -> dict[str, object]:
    _validate_arm(arm)
    return {
        "account_fingerprint_sha256": arm.account_fingerprint_sha256,
        "ends_at": _timestamp_text(arm.ends_at, path="ends_at"),
        "execution_protocol_sha256": arm.execution_protocol_sha256,
        "hard_flat_at": _timestamp_text(arm.hard_flat_at, path="hard_flat_at"),
        "mode": arm.mode,
        "owner_policy_sha256": arm.owner_policy_sha256,
        "reasoner_model_config_sha256": arm.reasoner_model_config_sha256,
        "reasoner_route_sha256": arm.reasoner_route_sha256,
        "release_code_sha256": arm.release_code_sha256,
        "risk_policy_sha256": arm.risk_policy_sha256,
        "schema": AUTONOMOUS_SESSION_ARM_SCHEMA,
        "schema_version": AUTONOMOUS_SESSION_ARM_SCHEMA_VERSION,
        "session_id": arm.session_id,
        "starts_at": _timestamp_text(arm.starts_at, path="starts_at"),
        "strategy_policy_sha256": arm.strategy_policy_sha256,
        "windows": [_window_payload(window) for window in arm.windows],
    }


def autonomous_session_arm_sha256(arm: AutonomousSessionArm) -> str:
    """Return the semantic SHA-256 identity of one closed autonomous arm."""

    return _sha256(_canonical_json(_arm_unsigned_payload(arm)))


def autonomous_session_arm_bytes(arm: AutonomousSessionArm) -> bytes:
    """Serialize one arm as exact canonical self-authenticating JSON bytes."""

    payload = _arm_unsigned_payload(arm)
    payload["arm_sha256"] = _sha256(_canonical_json(payload))
    return _canonical_json(payload)


def _validate_arm(arm: AutonomousSessionArm) -> None:
    if not isinstance(arm, AutonomousSessionArm):
        _reject("arm must be an AutonomousSessionArm")
    _identifier(arm.session_id, path="session_id")
    for field in (
        "release_code_sha256",
        "account_fingerprint_sha256",
        "owner_policy_sha256",
        "strategy_policy_sha256",
        "risk_policy_sha256",
        "execution_protocol_sha256",
        "reasoner_route_sha256",
        "reasoner_model_config_sha256",
    ):
        _digest(getattr(arm, field), path=field)
    if arm.mode != "PAPER":
        _reject("autonomous sessions are permanently PAPER-only")
    starts_at = _utc_datetime(arm.starts_at, path="starts_at")
    ends_at = _utc_datetime(arm.ends_at, path="ends_at")
    hard_flat_at = _utc_datetime(arm.hard_flat_at, path="hard_flat_at")
    if starts_at > hard_flat_at or ends_at < hard_flat_at:
        _reject("session clocks must include the hard-flat deadline")
    if not isinstance(arm.windows, tuple) or not arm.windows:
        _reject("windows must be a non-empty tuple")

    (
        owner_sha,
        strategy_sha,
        risk_sha,
        execution_sha,
        route_sha,
        model_sha,
    ) = _current_contract_hashes()
    if (
        arm.owner_policy_sha256,
        arm.strategy_policy_sha256,
        arm.risk_policy_sha256,
        arm.execution_protocol_sha256,
        arm.reasoner_route_sha256,
        arm.reasoner_model_config_sha256,
    ) != (owner_sha, strategy_sha, risk_sha, execution_sha, route_sha, model_sha):
        _reject("arm hashes do not bind the current frozen package contracts")

    try:
        owner = load_autonomous_policy()
        strategy = load_strategy_policy_v2()
    except (ImportError, OSError, TypeError, ValueError) as error:
        _reject(f"frozen autonomous contracts are unavailable: {type(error).__name__}")
    if (
        owner.run_mode != "PAPER"
        or owner.trade_count_cap_per_day is not None
        or owner.hard_flat_time_et != "15:30:00"
        or owner.intraday_decision_times_et
        != (
            "10:00:00",
            "11:00:00",
            "12:00:00",
            "13:00:00",
            "14:00:00",
            "15:00:00",
        )
    ):
        _reject("owner autonomy policy no longer matches the frozen PAPER session envelope")

    ordered_windows = tuple(
        sorted(
            arm.windows,
            key=lambda item: (_utc_datetime(item.opens_at, path="window.opens_at"), item.window_id),
        )
    )
    if arm.windows != ordered_windows:
        _reject("windows must be in deterministic opening-time order")
    if len({window.window_id for window in arm.windows}) != len(arm.windows):
        _reject("window IDs must be unique")
    if len(arm.windows) != len(owner.intraday_decision_times_et):
        _reject("arm must include every owner-approved hourly scan window")

    session_date: date | None = None
    local_openings: list[str] = []
    for window in arm.windows:
        _validate_window_shape(window)
        opens_at = _utc_datetime(window.opens_at, path="window.opens_at")
        closes_at = _utc_datetime(window.closes_at, path="window.closes_at")
        if opens_at < starts_at or closes_at > hard_flat_at:
            _reject("window must fall inside the active pre-hard-flat session")
        local_open = opens_at.astimezone(_EASTERN)
        local_close = closes_at.astimezone(_EASTERN)
        if session_date is None:
            session_date = local_open.date()
        if local_open.date() != session_date or local_close.date() != session_date:
            _reject("all windows must fall on one New York trading date")
        local_openings.append(local_open.strftime("%H:%M:%S"))
        if window.candidate_ids != strategy.candidate_ids:
            _reject("window candidate IDs must be the exact three V2 lanes")
    if tuple(local_openings) != owner.intraday_decision_times_et:
        _reject("window openings must follow the exact 10:00--15:00 ET hourly cadence")
    if hard_flat_at.astimezone(_EASTERN).strftime("%H:%M:%S") != owner.hard_flat_time_et:
        _reject("hard-flat clock must be the exact owner-approved 15:30 ET deadline")


def _parse_window(value: object, *, path: str) -> AutonomousWindow:
    payload = _strict_object(
        value,
        fields=frozenset(
            {
                "schema",
                "schema_version",
                "window_id",
                "opens_at",
                "closes_at",
                "candidate_ids",
            }
        ),
        path=path,
    )
    if (
        payload["schema"] != AUTONOMOUS_WINDOW_SCHEMA
        or payload["schema_version"] != AUTONOMOUS_WINDOW_SCHEMA_VERSION
    ):
        _reject(f"{path} has an unsupported window schema")
    candidate_ids = payload["candidate_ids"]
    if not isinstance(candidate_ids, list) or not candidate_ids:
        _reject(f"{path}.candidate_ids must be a non-empty list")
    return AutonomousWindow(
        window_id=_identifier(payload["window_id"], path=f"{path}.window_id"),
        opens_at=_timestamp(payload["opens_at"], path=f"{path}.opens_at"),
        closes_at=_timestamp(payload["closes_at"], path=f"{path}.closes_at"),
        candidate_ids=tuple(
            _identifier(candidate_id, path=f"{path}.candidate_ids")
            for candidate_id in candidate_ids
        ),
    )


def parse_autonomous_session_arm(raw: bytes) -> AutonomousSessionArm:
    """Parse only the exact canonical bytes of a current, semantically valid arm."""

    if type(raw) is not bytes:
        _reject("arm input must be immutable bytes")
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except AutonomousArmRejected:
        raise
    except (_DuplicateFieldError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        _reject(f"arm is not valid strict JSON: {type(error).__name__}")
    fields = frozenset(
        {
            "schema",
            "schema_version",
            "arm_sha256",
            "session_id",
            "release_code_sha256",
            "account_fingerprint_sha256",
            "owner_policy_sha256",
            "strategy_policy_sha256",
            "risk_policy_sha256",
            "execution_protocol_sha256",
            "reasoner_route_sha256",
            "reasoner_model_config_sha256",
            "mode",
            "starts_at",
            "ends_at",
            "hard_flat_at",
            "windows",
        }
    )
    payload = _strict_object(decoded, fields=fields, path="arm")
    if (
        payload["schema"] != AUTONOMOUS_SESSION_ARM_SCHEMA
        or payload["schema_version"] != AUTONOMOUS_SESSION_ARM_SCHEMA_VERSION
    ):
        _reject("arm has an unsupported schema or version")
    windows_value = payload["windows"]
    if not isinstance(windows_value, list) or not windows_value:
        _reject("arm.windows must be a non-empty list")
    arm = AutonomousSessionArm(
        session_id=_identifier(payload["session_id"], path="arm.session_id"),
        release_code_sha256=_digest(payload["release_code_sha256"], path="arm.release_code_sha256"),
        account_fingerprint_sha256=_digest(
            payload["account_fingerprint_sha256"], path="arm.account_fingerprint_sha256"
        ),
        owner_policy_sha256=_digest(payload["owner_policy_sha256"], path="arm.owner_policy_sha256"),
        strategy_policy_sha256=_digest(
            payload["strategy_policy_sha256"], path="arm.strategy_policy_sha256"
        ),
        risk_policy_sha256=_digest(payload["risk_policy_sha256"], path="arm.risk_policy_sha256"),
        execution_protocol_sha256=_digest(
            payload["execution_protocol_sha256"], path="arm.execution_protocol_sha256"
        ),
        reasoner_route_sha256=_digest(
            payload["reasoner_route_sha256"], path="arm.reasoner_route_sha256"
        ),
        reasoner_model_config_sha256=_digest(
            payload["reasoner_model_config_sha256"], path="arm.reasoner_model_config_sha256"
        ),
        mode=payload["mode"]
        if isinstance(payload["mode"], str)
        else _reject("arm.mode must be text"),
        starts_at=_timestamp(payload["starts_at"], path="arm.starts_at"),
        ends_at=_timestamp(payload["ends_at"], path="arm.ends_at"),
        hard_flat_at=_timestamp(payload["hard_flat_at"], path="arm.hard_flat_at"),
        windows=tuple(
            _parse_window(value, path=f"arm.windows[{index}]")
            for index, value in enumerate(windows_value)
        ),
    )
    _validate_arm(arm)
    expected_hash = _digest(payload["arm_sha256"], path="arm.arm_sha256")
    if expected_hash != arm.arm_sha256:
        _reject("arm SHA-256 does not bind its semantic payload")
    if raw != autonomous_session_arm_bytes(arm):
        _reject("arm bytes are not canonical")
    return arm


AUTONOMOUS_OPPORTUNITY_SCHEMA: Final = "esscher.autonomous_opportunity_identity"
AUTONOMOUS_OPPORTUNITY_SCHEMA_VERSION: Final = 1
AUTONOMOUS_STORE_SCHEMA_VERSION: Final = 1


class AutonomousStoreConflict(RuntimeError):
    """Raised when durable state disagrees with an exact supplied identity."""


class AutonomousClaimState(StrEnum):
    """The only atomic claim outcomes visible to a candidate processor."""

    CLAIMED = "CLAIMED"
    IN_PROGRESS = "IN_PROGRESS"
    ALREADY_RECORDED = "ALREADY_RECORDED"


@dataclass(frozen=True, slots=True)
class AutonomousOpportunity:
    """Text-free, semantically self-identifying candidate work for one window."""

    session_id: str
    opportunity_id: str
    window_id: str
    window_sha256: str
    candidate_id: str
    strategy_context_sha256: str
    reasoner_route_sha256: str
    reasoner_model_config_sha256: str

    @classmethod
    def for_window(
        cls,
        *,
        arm: AutonomousSessionArm,
        window_id: str,
        opportunity_id: str,
        strategy_context_sha256: str,
        candidate_id: str | None = None,
    ) -> AutonomousOpportunity:
        """Bind a collector result to one exact arm window and K3/context identity."""

        _validate_arm(arm)
        window = _window_for_arm(arm, window_id)
        return cls(
            session_id=arm.session_id,
            opportunity_id=opportunity_id,
            window_id=window.window_id,
            window_sha256=window.window_sha256,
            candidate_id=candidate_id or window.candidate_ids[0],
            strategy_context_sha256=strategy_context_sha256,
            reasoner_route_sha256=arm.reasoner_route_sha256,
            reasoner_model_config_sha256=arm.reasoner_model_config_sha256,
        )

    @property
    def opportunity_sha256(self) -> str:
        return _sha256(_canonical_json(_opportunity_unsigned_payload(self)))


def _window_for_arm(arm: AutonomousSessionArm, window_id: object) -> AutonomousWindow:
    window_id = _identifier(window_id, path="window_id")
    matches = tuple(window for window in arm.windows if window.window_id == window_id)
    if len(matches) != 1:
        _reject("opportunity window_id is not present in the arm")
    return matches[0]


def _validate_opportunity(value: AutonomousOpportunity, *, arm: AutonomousSessionArm) -> None:
    if type(value) is not AutonomousOpportunity:
        _reject("candidate collector must return an AutonomousOpportunity")
    _validate_arm(arm)
    if value.session_id != arm.session_id:
        _reject("opportunity session does not match the arm")
    _identifier(value.opportunity_id, path="opportunity.opportunity_id")
    _identifier(value.window_id, path="opportunity.window_id")
    _identifier(value.candidate_id, path="opportunity.candidate_id")
    for field in (
        "window_sha256",
        "strategy_context_sha256",
        "reasoner_route_sha256",
        "reasoner_model_config_sha256",
    ):
        _digest(getattr(value, field), path=f"opportunity.{field}")
    window = _window_for_arm(arm, value.window_id)
    if (
        value.window_sha256 != window.window_sha256
        or value.candidate_id not in window.candidate_ids
        or value.reasoner_route_sha256 != arm.reasoner_route_sha256
        or value.reasoner_model_config_sha256 != arm.reasoner_model_config_sha256
    ):
        _reject("opportunity does not bind the arm's exact window, lane, and K3 identities")


def _opportunity_unsigned_payload(value: AutonomousOpportunity) -> dict[str, object]:
    if type(value) is not AutonomousOpportunity:
        _reject("candidate collector must return an AutonomousOpportunity")
    _identifier(value.session_id, path="opportunity.session_id")
    _identifier(value.opportunity_id, path="opportunity.opportunity_id")
    _identifier(value.window_id, path="opportunity.window_id")
    _identifier(value.candidate_id, path="opportunity.candidate_id")
    for field in (
        "window_sha256",
        "strategy_context_sha256",
        "reasoner_route_sha256",
        "reasoner_model_config_sha256",
    ):
        _digest(getattr(value, field), path=f"opportunity.{field}")
    return {
        "candidate_id": value.candidate_id,
        "opportunity_id": value.opportunity_id,
        "reasoner_model_config_sha256": value.reasoner_model_config_sha256,
        "reasoner_route_sha256": value.reasoner_route_sha256,
        "schema": AUTONOMOUS_OPPORTUNITY_SCHEMA,
        "schema_version": AUTONOMOUS_OPPORTUNITY_SCHEMA_VERSION,
        "session_id": value.session_id,
        "strategy_context_sha256": value.strategy_context_sha256,
        "window_id": value.window_id,
        "window_sha256": value.window_sha256,
    }


@dataclass(frozen=True, slots=True)
class ActiveLifecycleIdentity:
    """Sanitized durable identity for one exposure-bearing lifecycle only."""

    session_id: str
    lifecycle_id: str
    opportunity_id: str
    opportunity_sha256: str
    lifecycle_sha256: str

    @classmethod
    def for_candidate(
        cls,
        *,
        arm: AutonomousSessionArm,
        opportunity: AutonomousOpportunity,
        lifecycle_id: str,
    ) -> ActiveLifecycleIdentity:
        _validate_opportunity(opportunity, arm=arm)
        lifecycle_id = _identifier(lifecycle_id, path="lifecycle_id")
        unsigned = {
            "lifecycle_id": lifecycle_id,
            "opportunity_id": opportunity.opportunity_id,
            "opportunity_sha256": opportunity.opportunity_sha256,
            "schema": AUTONOMOUS_ACTIVE_LIFECYCLE_SCHEMA,
            "schema_version": AUTONOMOUS_ACTIVE_LIFECYCLE_SCHEMA_VERSION,
            "session_id": arm.session_id,
        }
        return cls(
            session_id=arm.session_id,
            lifecycle_id=lifecycle_id,
            opportunity_id=opportunity.opportunity_id,
            opportunity_sha256=opportunity.opportunity_sha256,
            lifecycle_sha256=_sha256(_canonical_json(unsigned)),
        )


def _active_lifecycle_unsigned_payload(value: ActiveLifecycleIdentity) -> dict[str, object]:
    if type(value) is not ActiveLifecycleIdentity:
        _reject("active lifecycle must be an ActiveLifecycleIdentity")
    return {
        "lifecycle_id": _identifier(value.lifecycle_id, path="active_lifecycle.lifecycle_id"),
        "opportunity_id": _identifier(value.opportunity_id, path="active_lifecycle.opportunity_id"),
        "opportunity_sha256": _digest(
            value.opportunity_sha256,
            path="active_lifecycle.opportunity_sha256",
        ),
        "schema": AUTONOMOUS_ACTIVE_LIFECYCLE_SCHEMA,
        "schema_version": AUTONOMOUS_ACTIVE_LIFECYCLE_SCHEMA_VERSION,
        "session_id": _identifier(value.session_id, path="active_lifecycle.session_id"),
    }


def active_lifecycle_bytes(value: ActiveLifecycleIdentity) -> bytes:
    """Return the strict self-identifying bytes persisted by the store."""

    unsigned = _active_lifecycle_unsigned_payload(value)
    expected = _sha256(_canonical_json(unsigned))
    if value.lifecycle_sha256 != expected:
        _reject("active lifecycle hash is invalid")
    return _canonical_json({**unsigned, "lifecycle_sha256": expected})


def parse_active_lifecycle(raw: bytes) -> ActiveLifecycleIdentity:
    """Parse a strict canonical stored lifecycle identity without any raw broker fields."""

    if type(raw) is not bytes:
        _reject("active lifecycle bytes must be immutable bytes")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateFieldError) as error:
        raise AutonomousArmRejected("active lifecycle bytes are invalid") from error
    fields = frozenset(
        {
            "lifecycle_id",
            "lifecycle_sha256",
            "opportunity_id",
            "opportunity_sha256",
            "schema",
            "schema_version",
            "session_id",
        }
    )
    data = _strict_object(payload, fields=fields, path="active_lifecycle")
    if data["schema"] != AUTONOMOUS_ACTIVE_LIFECYCLE_SCHEMA:
        _reject("active lifecycle schema mismatch")
    if data["schema_version"] != AUTONOMOUS_ACTIVE_LIFECYCLE_SCHEMA_VERSION:
        _reject("active lifecycle schema version mismatch")
    value = ActiveLifecycleIdentity(
        session_id=_identifier(data["session_id"], path="active_lifecycle.session_id"),
        lifecycle_id=_identifier(data["lifecycle_id"], path="active_lifecycle.lifecycle_id"),
        opportunity_id=_identifier(data["opportunity_id"], path="active_lifecycle.opportunity_id"),
        opportunity_sha256=_digest(
            data["opportunity_sha256"], path="active_lifecycle.opportunity_sha256"
        ),
        lifecycle_sha256=_digest(
            data["lifecycle_sha256"], path="active_lifecycle.lifecycle_sha256"
        ),
    )
    if active_lifecycle_bytes(value) != raw:
        _reject("active lifecycle bytes are not canonical")
    return value


class AutonomousSessionStore:
    """SQLite-backed durable identities, claims, and safe terminal dispositions.

    The schema stores only canonical arm bytes plus identifiers, SHA-256 values,
    UTC clocks, and closed state names.  It has no columns for raw accounts,
    broker IDs, credentials, provider payloads, article text, or failure prose.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        try:
            self._connection = sqlite3.connect(
                self._path,
                timeout=30.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 30000")
            self._migrate()
        except sqlite3.Error as error:
            raise AutonomousStoreConflict("durable autonomous state is unavailable") from error

    def close(self) -> None:
        """Close the local SQLite handle; closing twice is harmless."""

        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> AutonomousSessionStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise AutonomousStoreConflict("durable autonomous state is closed")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._require_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _migrate(self) -> None:
        with self._lock:
            current = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if current == AUTONOMOUS_STORE_SCHEMA_VERSION:
            return
        if current != 0:
            raise AutonomousStoreConflict("durable autonomous state schema is unsupported")
        with self._transaction():
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_sessions (
                    session_id TEXT PRIMARY KEY,
                    arm_sha256 TEXT NOT NULL,
                    arm_json BLOB NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_windows (
                    session_id TEXT NOT NULL,
                    window_id TEXT NOT NULL,
                    window_sha256 TEXT NOT NULL,
                    opens_at TEXT NOT NULL,
                    closes_at TEXT NOT NULL,
                    candidate_ids_json BLOB NOT NULL,
                    PRIMARY KEY (session_id, window_id),
                    FOREIGN KEY (session_id) REFERENCES autonomous_sessions(session_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_opportunities (
                    session_id TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    opportunity_sha256 TEXT NOT NULL,
                    window_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    strategy_context_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    terminal_flat_proof_sha256 TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, opportunity_id),
                    FOREIGN KEY (session_id) REFERENCES autonomous_sessions(session_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_active_lifecycles (
                    session_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    lifecycle_sha256 TEXT NOT NULL,
                    opportunity_id TEXT NOT NULL,
                    identity_json BLOB NOT NULL,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, lifecycle_id),
                    UNIQUE (session_id, opportunity_id),
                    FOREIGN KEY (session_id) REFERENCES autonomous_sessions(session_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_manual_reasons (
                    session_id TEXT NOT NULL,
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    reason_code TEXT NOT NULL,
                    opportunity_id TEXT,
                    lifecycle_id TEXT,
                    observed_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES autonomous_sessions(session_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary_sha256 TEXT NOT NULL,
                    summary_json BLOB NOT NULL,
                    finalized_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES autonomous_sessions(session_id)
                )
                """
            )
            self._connection.execute(f"PRAGMA user_version = {AUTONOMOUS_STORE_SCHEMA_VERSION}")

    def ensure_arm(self, arm: AutonomousSessionArm) -> None:
        """Atomically persist a new arm, or require byte-for-byte replay identity."""

        arm_bytes = autonomous_session_arm_bytes(arm)
        arm_sha256 = arm.arm_sha256
        with self._transaction():
            row = self._connection.execute(
                "SELECT arm_sha256, arm_json FROM autonomous_sessions WHERE session_id = ?",
                (arm.session_id,),
            ).fetchone()
            if row is not None:
                if row["arm_sha256"] != arm_sha256 or bytes(row["arm_json"]) != arm_bytes:
                    raise AutonomousStoreConflict("session ID is already bound to a different arm")
                return
            self._connection.execute(
                """
                INSERT INTO autonomous_sessions
                (session_id, arm_sha256, arm_json, state, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    arm.session_id,
                    arm_sha256,
                    arm_bytes,
                    "ARMED",
                    _timestamp_text(arm.starts_at, path="starts_at"),
                ),
            )
            for window in arm.windows:
                self._connection.execute(
                    """
                    INSERT INTO autonomous_windows
                    (session_id, window_id, window_sha256, opens_at, closes_at, candidate_ids_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        arm.session_id,
                        window.window_id,
                        window.window_sha256,
                        _timestamp_text(window.opens_at, path="window.opens_at"),
                        _timestamp_text(window.closes_at, path="window.closes_at"),
                        _canonical_json(list(window.candidate_ids)),
                    ),
                )

    def load_arm(self, session_id: str) -> AutonomousSessionArm | None:
        """Load and revalidate the sole stored arm for a session ID."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT arm_sha256, arm_json FROM autonomous_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            arm = parse_autonomous_session_arm(bytes(row["arm_json"]))
        except AutonomousArmRejected as error:
            raise AutonomousStoreConflict("stored arm is invalid") from error
        if arm.arm_sha256 != row["arm_sha256"]:
            raise AutonomousStoreConflict("stored arm identity is invalid")
        return arm

    def window_ids(self, session_id: str) -> tuple[str, ...]:
        """Return the persisted windows in their deterministic processing order."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """
                SELECT window_id FROM autonomous_windows
                WHERE session_id = ? ORDER BY opens_at ASC, window_id ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(str(row["window_id"]) for row in rows)

    def opportunity_state(self, session_id: str, opportunity_id: str) -> str | None:
        """Return one safe persisted disposition, never a provider/broker payload."""

        session_id = _identifier(session_id, path="session_id")
        opportunity_id = _identifier(opportunity_id, path="opportunity_id")
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                """
                SELECT state FROM autonomous_opportunities
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (session_id, opportunity_id),
            ).fetchone()
        return None if row is None else str(row["state"])

    def claimed_opportunity_ids(self, session_id: str) -> tuple[str, ...]:
        """Return durable in-progress claim IDs in deterministic order."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """
                SELECT opportunity_id FROM autonomous_opportunities
                WHERE session_id = ? AND state = 'CLAIMED' ORDER BY opportunity_id ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(str(row["opportunity_id"]) for row in rows)

    def claim_opportunity(
        self,
        *,
        arm: AutonomousSessionArm,
        opportunity: AutonomousOpportunity,
        observed_at: datetime,
    ) -> AutonomousClaimState:
        """Atomically claim exactly one semantically bound opportunity identity."""

        self.ensure_arm(arm)
        _validate_opportunity(opportunity, arm=arm)
        observed_at = _utc_datetime(observed_at, path="observed_at")
        with self._transaction():
            window = self._connection.execute(
                """
                SELECT window_sha256, candidate_ids_json FROM autonomous_windows
                WHERE session_id = ? AND window_id = ?
                """,
                (arm.session_id, opportunity.window_id),
            ).fetchone()
            if window is None:
                raise AutonomousStoreConflict("opportunity references an unpersisted window")
            try:
                candidate_ids = tuple(json.loads(bytes(window["candidate_ids_json"])))
            except (TypeError, UnicodeDecodeError, ValueError) as error:
                raise AutonomousStoreConflict("stored window identity is invalid") from error
            if (
                window["window_sha256"] != opportunity.window_sha256
                or opportunity.candidate_id not in candidate_ids
            ):
                raise AutonomousStoreConflict(
                    "opportunity conflicts with its persisted window identity"
                )
            row = self._connection.execute(
                """
                SELECT opportunity_sha256, state FROM autonomous_opportunities
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (arm.session_id, opportunity.opportunity_id),
            ).fetchone()
            if row is not None:
                if row["opportunity_sha256"] != opportunity.opportunity_sha256:
                    raise AutonomousStoreConflict(
                        "opportunity ID conflicts with a different semantic identity"
                    )
                if row["state"] == AutonomousClaimState.CLAIMED.value:
                    return AutonomousClaimState.IN_PROGRESS
                return AutonomousClaimState.ALREADY_RECORDED
            self._connection.execute(
                """
                INSERT INTO autonomous_opportunities
                (session_id, opportunity_id, opportunity_sha256, window_id, candidate_id,
                 strategy_context_sha256, state, terminal_flat_proof_sha256, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    arm.session_id,
                    opportunity.opportunity_id,
                    opportunity.opportunity_sha256,
                    opportunity.window_id,
                    opportunity.candidate_id,
                    opportunity.strategy_context_sha256,
                    AutonomousClaimState.CLAIMED.value,
                    _timestamp_text(observed_at, path="observed_at"),
                ),
            )
        return AutonomousClaimState.CLAIMED

    def record_terminal_flat(
        self,
        *,
        arm: AutonomousSessionArm,
        opportunity: AutonomousOpportunity,
        terminal_flat_proof_sha256: str,
        observed_at: datetime,
    ) -> None:
        """Persist the exact terminal-flat outcome of an already claimed candidate."""

        self.ensure_arm(arm)
        _validate_opportunity(opportunity, arm=arm)
        proof = _digest(terminal_flat_proof_sha256, path="terminal_flat_proof_sha256")
        observed_at = _utc_datetime(observed_at, path="observed_at")
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT opportunity_sha256, state, terminal_flat_proof_sha256
                FROM autonomous_opportunities WHERE session_id = ? AND opportunity_id = ?
                """,
                (arm.session_id, opportunity.opportunity_id),
            ).fetchone()
            if row is None or row["opportunity_sha256"] != opportunity.opportunity_sha256:
                raise AutonomousStoreConflict(
                    "terminal outcome has no matching claimed opportunity"
                )
            if row["state"] == "TERMINAL_FLAT":
                if row["terminal_flat_proof_sha256"] != proof:
                    raise AutonomousStoreConflict(
                        "terminal opportunity replay has a different proof"
                    )
                return
            if row["state"] != AutonomousClaimState.CLAIMED.value:
                raise AutonomousStoreConflict("only a claimed opportunity may become terminal-flat")
            self._connection.execute(
                """
                UPDATE autonomous_opportunities
                SET state = ?, terminal_flat_proof_sha256 = ?, updated_at = ?
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (
                    "TERMINAL_FLAT",
                    proof,
                    _timestamp_text(observed_at, path="observed_at"),
                    arm.session_id,
                    opportunity.opportunity_id,
                ),
            )

    def record_disposition(
        self,
        *,
        arm: AutonomousSessionArm,
        opportunity: AutonomousOpportunity,
        disposition: str,
        observed_at: datetime,
    ) -> None:
        """Persist a pre-mutation abstention or rejection for a claimed opportunity."""

        if disposition not in {
            "ABSTAINED",
            "REJECTED_BEFORE_MUTATION",
            "MANUAL_RECONCILIATION_REQUIRED",
        }:
            raise AutonomousStoreConflict(
                "the supplied disposition is not a permitted terminal state"
            )
        self.ensure_arm(arm)
        _validate_opportunity(opportunity, arm=arm)
        observed_at = _utc_datetime(observed_at, path="observed_at")
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT opportunity_sha256, state FROM autonomous_opportunities
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (arm.session_id, opportunity.opportunity_id),
            ).fetchone()
            if row is None or row["opportunity_sha256"] != opportunity.opportunity_sha256:
                raise AutonomousStoreConflict("disposition has no matching claimed opportunity")
            if row["state"] == disposition:
                return
            if row["state"] != AutonomousClaimState.CLAIMED.value:
                raise AutonomousStoreConflict(
                    "only a claimed opportunity may receive this disposition"
                )
            self._connection.execute(
                """
                UPDATE autonomous_opportunities SET state = ?, updated_at = ?
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (
                    disposition,
                    _timestamp_text(observed_at, path="observed_at"),
                    arm.session_id,
                    opportunity.opportunity_id,
                ),
            )

    def record_active_lifecycle(
        self,
        *,
        arm: AutonomousSessionArm,
        opportunity: AutonomousOpportunity,
        lifecycle: ActiveLifecycleIdentity,
        observed_at: datetime,
    ) -> None:
        """Atomically bind one confirmed active lifecycle to its claimed opportunity."""

        self.ensure_arm(arm)
        _validate_opportunity(opportunity, arm=arm)
        if (
            lifecycle.session_id != arm.session_id
            or lifecycle.opportunity_id != opportunity.opportunity_id
            or lifecycle.opportunity_sha256 != opportunity.opportunity_sha256
        ):
            raise AutonomousStoreConflict("active lifecycle is not attributable to its opportunity")
        identity_bytes = active_lifecycle_bytes(lifecycle)
        observed_at = _utc_datetime(observed_at, path="observed_at")
        with self._transaction():
            opportunity_row = self._connection.execute(
                """
                SELECT opportunity_sha256, state FROM autonomous_opportunities
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (arm.session_id, opportunity.opportunity_id),
            ).fetchone()
            if (
                opportunity_row is None
                or opportunity_row["opportunity_sha256"] != opportunity.opportunity_sha256
            ):
                raise AutonomousStoreConflict(
                    "active lifecycle has no matching claimed opportunity"
                )
            existing = self._connection.execute(
                """
                SELECT identity_json, opportunity_id FROM autonomous_active_lifecycles
                WHERE session_id = ? AND lifecycle_id = ?
                """,
                (arm.session_id, lifecycle.lifecycle_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["opportunity_id"] == opportunity.opportunity_id
                    and bytes(existing["identity_json"]) == identity_bytes
                    and opportunity_row["state"] == "ACTIVE"
                ):
                    return
                raise AutonomousStoreConflict("active lifecycle ID conflicts with durable identity")
            if opportunity_row["state"] != AutonomousClaimState.CLAIMED.value:
                raise AutonomousStoreConflict("only a claimed opportunity may become active")
            by_opportunity = self._connection.execute(
                """
                SELECT lifecycle_id FROM autonomous_active_lifecycles
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (arm.session_id, opportunity.opportunity_id),
            ).fetchone()
            if by_opportunity is not None:
                raise AutonomousStoreConflict(
                    "opportunity already has a different active lifecycle"
                )
            rendered = _timestamp_text(observed_at, path="observed_at")
            self._connection.execute(
                """
                INSERT INTO autonomous_active_lifecycles
                (session_id, lifecycle_id, lifecycle_sha256, opportunity_id,
                 identity_json, state, updated_at)
                VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    arm.session_id,
                    lifecycle.lifecycle_id,
                    lifecycle.lifecycle_sha256,
                    opportunity.opportunity_id,
                    identity_bytes,
                    rendered,
                ),
            )
            self._connection.execute(
                """
                UPDATE autonomous_opportunities SET state = 'ACTIVE', updated_at = ?
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (rendered, arm.session_id, opportunity.opportunity_id),
            )

    def active_lifecycles(self, session_id: str) -> tuple[ActiveLifecycleIdentity, ...]:
        """Load exact persisted active identities in deterministic lifecycle-ID order."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """
                SELECT lifecycle_id, lifecycle_sha256, opportunity_id, identity_json
                FROM autonomous_active_lifecycles
                WHERE session_id = ? AND state = 'ACTIVE' ORDER BY lifecycle_id ASC
                """,
                (session_id,),
            ).fetchall()
        values: list[ActiveLifecycleIdentity] = []
        for row in rows:
            try:
                lifecycle = parse_active_lifecycle(bytes(row["identity_json"]))
            except AutonomousArmRejected as error:
                raise AutonomousStoreConflict("stored active lifecycle is invalid") from error
            if (
                lifecycle.session_id != session_id
                or lifecycle.lifecycle_id != row["lifecycle_id"]
                or lifecycle.lifecycle_sha256 != row["lifecycle_sha256"]
                or lifecycle.opportunity_id != row["opportunity_id"]
            ):
                raise AutonomousStoreConflict("stored active lifecycle identity mismatch")
            values.append(lifecycle)
        return tuple(values)

    def record_lifecycle_terminal_flat(
        self,
        *,
        arm: AutonomousSessionArm,
        lifecycle: ActiveLifecycleIdentity,
        terminal_flat_proof_sha256: str,
        observed_at: datetime,
    ) -> None:
        """Atomically persist hard-flat proof for one exact active lifecycle."""

        self.ensure_arm(arm)
        if lifecycle.session_id != arm.session_id:
            raise AutonomousStoreConflict("lifecycle belongs to another session")
        identity_bytes = active_lifecycle_bytes(lifecycle)
        proof = _digest(terminal_flat_proof_sha256, path="terminal_flat_proof_sha256")
        observed_at = _utc_datetime(observed_at, path="observed_at")
        with self._transaction():
            row = self._connection.execute(
                """
                SELECT identity_json, state FROM autonomous_active_lifecycles
                WHERE session_id = ? AND lifecycle_id = ?
                """,
                (arm.session_id, lifecycle.lifecycle_id),
            ).fetchone()
            if row is None or bytes(row["identity_json"]) != identity_bytes:
                raise AutonomousStoreConflict("hard-flat result has no matching active lifecycle")
            opportunity = self._connection.execute(
                """
                SELECT state, terminal_flat_proof_sha256 FROM autonomous_opportunities
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (arm.session_id, lifecycle.opportunity_id),
            ).fetchone()
            if opportunity is None:
                raise AutonomousStoreConflict("active lifecycle has no durable opportunity")
            if row["state"] == "TERMINAL_FLAT":
                if opportunity["terminal_flat_proof_sha256"] != proof:
                    raise AutonomousStoreConflict("hard-flat replay has a different terminal proof")
                return
            if row["state"] != "ACTIVE" or opportunity["state"] != "ACTIVE":
                raise AutonomousStoreConflict("lifecycle is not in a closeable active state")
            rendered = _timestamp_text(observed_at, path="observed_at")
            self._connection.execute(
                """
                UPDATE autonomous_active_lifecycles SET state = 'TERMINAL_FLAT', updated_at = ?
                WHERE session_id = ? AND lifecycle_id = ?
                """,
                (rendered, arm.session_id, lifecycle.lifecycle_id),
            )
            self._connection.execute(
                """
                UPDATE autonomous_opportunities
                SET state = 'TERMINAL_FLAT', terminal_flat_proof_sha256 = ?, updated_at = ?
                WHERE session_id = ? AND opportunity_id = ?
                """,
                (proof, rendered, arm.session_id, lifecycle.opportunity_id),
            )

    def active_lifecycle_ids(self, session_id: str) -> tuple[str, ...]:
        """Return only persisted safe lifecycle identities, in deterministic order."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """
                SELECT lifecycle_id FROM autonomous_active_lifecycles
                WHERE session_id = ? AND state = 'ACTIVE' ORDER BY lifecycle_id ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(str(row["lifecycle_id"]) for row in rows)

    def disposition_counts(self, session_id: str) -> Mapping[str, int]:
        """Return fixed safe disposition counts without raw result contents."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """
                SELECT state, COUNT(*) AS count FROM autonomous_opportunities
                WHERE session_id = ? GROUP BY state
                """,
                (session_id,),
            ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def session_state(self, session_id: str) -> str | None:
        """Return the durable session control state without exposing any raw data."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT state FROM autonomous_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return None if row is None else str(row["state"])

    def mark_manual_reconciliation_required(
        self,
        *,
        arm: AutonomousSessionArm,
        reason_code: str,
        observed_at: datetime,
        opportunity_id: str | None = None,
        lifecycle_id: str | None = None,
    ) -> None:
        """Durably freeze new exposure with an allowlisted, text-free reason."""

        self.ensure_arm(arm)
        reason = _reason_code(reason_code, path="reason_code", required=True)
        observed_at = _utc_datetime(observed_at, path="observed_at")
        if opportunity_id is not None:
            opportunity_id = _identifier(opportunity_id, path="opportunity_id")
        if lifecycle_id is not None:
            lifecycle_id = _identifier(lifecycle_id, path="lifecycle_id")
        with self._transaction():
            row = self._connection.execute(
                "SELECT session_id FROM autonomous_sessions WHERE session_id = ?",
                (arm.session_id,),
            ).fetchone()
            if row is None:
                raise AutonomousStoreConflict("manual state has no persisted arm")
            self._connection.execute(
                """
                UPDATE autonomous_sessions SET state = 'MANUAL_RECONCILIATION_REQUIRED'
                WHERE session_id = ?
                """,
                (arm.session_id,),
            )
            existing = self._connection.execute(
                """
                SELECT 1 FROM autonomous_manual_reasons
                WHERE session_id = ? AND reason_code = ?
                  AND opportunity_id IS ? AND lifecycle_id IS ?
                """,
                (arm.session_id, reason, opportunity_id, lifecycle_id),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO autonomous_manual_reasons
                    (session_id, reason_code, opportunity_id, lifecycle_id, observed_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        arm.session_id,
                        reason,
                        opportunity_id,
                        lifecycle_id,
                        _timestamp_text(observed_at, path="observed_at"),
                    ),
                )

    def manual_reasons(self, session_id: str) -> tuple[str, ...]:
        """Return ordered allowlisted reason codes only, never failure prose."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            rows = self._connection.execute(
                """
                SELECT reason_code FROM autonomous_manual_reasons
                WHERE session_id = ? ORDER BY sequence ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(str(row["reason_code"]) for row in rows)

    def finalize_session(
        self,
        *,
        arm: AutonomousSessionArm,
        finalized_at: datetime,
    ) -> AutonomousSessionSummary:
        """Freeze one canonical final summary from the exact durable state."""

        self.ensure_arm(arm)
        finalized_at = _utc_datetime(finalized_at, path="finalized_at")
        with self._transaction():
            existing = self._connection.execute(
                "SELECT summary_sha256, summary_json FROM autonomous_summaries "
                "WHERE session_id = ?",
                (arm.session_id,),
            ).fetchone()
            if existing is not None:
                summary = _parse_summary(bytes(existing["summary_json"]))
                if summary.summary_sha256 != existing["summary_sha256"]:
                    raise AutonomousStoreConflict("stored final summary identity mismatch")
                return summary
            count_rows = self._connection.execute(
                """
                SELECT state, COUNT(*) AS count FROM autonomous_opportunities
                WHERE session_id = ? GROUP BY state
                """,
                (arm.session_id,),
            ).fetchall()
            counts = {str(row["state"]): int(row["count"]) for row in count_rows}
            active_rows = self._connection.execute(
                """
                SELECT lifecycle_id FROM autonomous_active_lifecycles
                WHERE session_id = ? AND state = 'ACTIVE' ORDER BY lifecycle_id ASC
                """,
                (arm.session_id,),
            ).fetchall()
            reason_rows = self._connection.execute(
                """
                SELECT DISTINCT reason_code FROM autonomous_manual_reasons
                WHERE session_id = ? ORDER BY reason_code ASC
                """,
                (arm.session_id,),
            ).fetchall()
            summary = _build_summary(
                arm=arm,
                finalized_at=finalized_at,
                disposition_counts=counts,
                active_lifecycle_ids=tuple(str(row["lifecycle_id"]) for row in active_rows),
                manual_reasons=tuple(str(row["reason_code"]) for row in reason_rows),
            )
            raw = autonomous_session_summary_bytes(summary)
            self._connection.execute(
                """
                INSERT INTO autonomous_summaries
                (session_id, summary_sha256, summary_json, finalized_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    arm.session_id,
                    summary.summary_sha256,
                    raw,
                    _timestamp_text(finalized_at, path="finalized_at"),
                ),
            )
            self._connection.execute(
                "UPDATE autonomous_sessions SET state = ? WHERE session_id = ?",
                (
                    "FINALIZED"
                    if summary.terminal_flat_proven
                    else "MANUAL_RECONCILIATION_REQUIRED",
                    arm.session_id,
                ),
            )
            return summary

    def final_summary(self, session_id: str) -> AutonomousSessionSummary | None:
        """Read and revalidate the immutable final summary for one session."""

        session_id = _identifier(session_id, path="session_id")
        with self._lock:
            self._require_open()
            row = self._connection.execute(
                "SELECT summary_sha256, summary_json FROM autonomous_summaries "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        summary = _parse_summary(bytes(row["summary_json"]))
        if summary.summary_sha256 != row["summary_sha256"] or summary.session_id != session_id:
            raise AutonomousStoreConflict("stored final summary identity mismatch")
        return summary


class AutonomousCoordinatorRejected(RuntimeError):
    """Raised before a port call when a coordinator identity is not arm-safe."""


class AutonomousDisposition(StrEnum):
    """Closed externally visible candidate dispositions."""

    ABSTAINED = "ABSTAINED"
    REJECTED_BEFORE_MUTATION = "REJECTED_BEFORE_MUTATION"
    ACTIVE = "ACTIVE"
    TERMINAL_FLAT = "TERMINAL_FLAT"
    MANUAL_RECONCILIATION_REQUIRED = "MANUAL_RECONCILIATION_REQUIRED"


class MutationState(StrEnum):
    """Whether a candidate processor can prove it did not or did mutate state."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"


class ReconciliationStatus(StrEnum):
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


_ALLOWED_REASON_CODES: Final = frozenset(
    {
        "PROVIDER_TIMEOUT_BEFORE_MUTATION",
        "PROVIDER_TIMEOUT_UNKNOWN_MUTATION",
        "UNKNOWN_BROKER_STATE",
        "PARTIAL_FILL",
        "RECONCILIATION_FAILED",
        "RECONCILIATION_INCOMPLETE",
        "RECONCILIATION_IDENTITY_MISMATCH",
        "RISK_FREEZE",
        "PORT_OUTPUT_INVALID",
        "PORT_EXCEPTION",
        "CLAIM_RECOVERY_UNKNOWN",
        "HARD_FLAT_UNRESOLVED",
        "ARM_IDENTITY_MISMATCH",
        "WINDOW_NOT_DUE",
        "WINDOW_EXPIRED",
        "LATE_WINDOW",
        "UNEXPECTED_ACTIVE_LIFECYCLE",
        # Issue #90 production PAPER_MCP vocabulary extensions.
        "MUTATION_GATE_CLOSED",
        "ACTIVITY_MANUAL_ROUTE",
        "BLOCKED_RETRY_BUDGET_EXHAUSTED",
    }
)


def _reason_code(value: object, *, path: str, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or value not in _ALLOWED_REASON_CODES:
        _reject(f"{path} must be an allowlisted reason code")
    return value


@dataclass(frozen=True, slots=True)
class SanitizedIdentityReceipt:
    """Closed receipt metadata: IDs, hashes, UTC time, and no arbitrary text."""

    receipt_id: str
    subject_id: str
    subject_sha256: str
    observed_at: datetime
    status: str
    reason_code: str | None
    receipt_sha256: str

    @classmethod
    def create(
        cls,
        *,
        receipt_id: str,
        subject_id: str,
        subject_sha256: str,
        observed_at: datetime,
        status: str,
        reason_code: str | None = None,
    ) -> SanitizedIdentityReceipt:
        unsigned = {
            "observed_at": _timestamp_text(observed_at, path="receipt.observed_at"),
            "reason_code": _reason_code(reason_code, path="receipt.reason_code"),
            "receipt_id": _identifier(receipt_id, path="receipt.receipt_id"),
            "schema": "esscher.autonomous_sanitized_receipt",
            "schema_version": 1,
            "status": _identifier(status, path="receipt.status"),
            "subject_id": _identifier(subject_id, path="receipt.subject_id"),
            "subject_sha256": _digest(subject_sha256, path="receipt.subject_sha256"),
        }
        return cls(
            receipt_id=unsigned["receipt_id"],
            subject_id=unsigned["subject_id"],
            subject_sha256=unsigned["subject_sha256"],
            observed_at=observed_at,
            status=unsigned["status"],
            reason_code=unsigned["reason_code"],
            receipt_sha256=_sha256(_canonical_json(unsigned)),
        )


def _receipt_unsigned_payload(value: SanitizedIdentityReceipt) -> dict[str, object]:
    if type(value) is not SanitizedIdentityReceipt:
        _reject("receipt must be a SanitizedIdentityReceipt")
    return {
        "observed_at": _timestamp_text(value.observed_at, path="receipt.observed_at"),
        "reason_code": _reason_code(value.reason_code, path="receipt.reason_code"),
        "receipt_id": _identifier(value.receipt_id, path="receipt.receipt_id"),
        "schema": "esscher.autonomous_sanitized_receipt",
        "schema_version": 1,
        "status": _identifier(value.status, path="receipt.status"),
        "subject_id": _identifier(value.subject_id, path="receipt.subject_id"),
        "subject_sha256": _digest(value.subject_sha256, path="receipt.subject_sha256"),
    }


def _validate_receipt(
    value: SanitizedIdentityReceipt,
    *,
    subject_id: str,
    subject_sha256: str,
    status: str,
    reason_code: str | None,
) -> None:
    payload = _receipt_unsigned_payload(value)
    if value.receipt_sha256 != _sha256(_canonical_json(payload)):
        _reject("receipt hash is invalid")
    if (
        value.subject_id != subject_id
        or value.subject_sha256 != subject_sha256
        or value.status != status
        or value.reason_code != reason_code
    ):
        _reject("receipt is not attributable to its typed result")


@dataclass(frozen=True, slots=True)
class ReconciliationRequest:
    session_id: str
    arm_sha256: str
    account_fingerprint_sha256: str
    execution_protocol_sha256: str
    observed_at: datetime
    phase: str
    active_lifecycle_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationReceipt:
    session_id: str
    arm_sha256: str
    account_fingerprint_sha256: str
    execution_protocol_sha256: str
    observed_at: datetime
    status: ReconciliationStatus
    active_lifecycle_ids: tuple[str, ...]
    receipt: SanitizedIdentityReceipt

    @classmethod
    def complete(cls, *, request: ReconciliationRequest) -> ReconciliationReceipt:
        active_ids = tuple(sorted(request.active_lifecycle_ids))
        return cls(
            session_id=request.session_id,
            arm_sha256=request.arm_sha256,
            account_fingerprint_sha256=request.account_fingerprint_sha256,
            execution_protocol_sha256=request.execution_protocol_sha256,
            observed_at=request.observed_at,
            status=ReconciliationStatus.COMPLETE,
            active_lifecycle_ids=active_ids,
            receipt=SanitizedIdentityReceipt.create(
                receipt_id=f"RECON-{request.arm_sha256[:16]}",
                subject_id=request.session_id,
                subject_sha256=request.arm_sha256,
                observed_at=request.observed_at,
                status="RECONCILIATION_COMPLETE",
            ),
        )


def _validate_reconciliation(
    value: ReconciliationReceipt,
    *,
    request: ReconciliationRequest,
) -> None:
    if type(value) is not ReconciliationReceipt:
        _reject("reconciliation port returned an untyped result")
    if (
        value.session_id != request.session_id
        or value.arm_sha256 != request.arm_sha256
        or value.account_fingerprint_sha256 != request.account_fingerprint_sha256
        or value.execution_protocol_sha256 != request.execution_protocol_sha256
        or value.observed_at != request.observed_at
        or value.status is not ReconciliationStatus.COMPLETE
    ):
        _reject("reconciliation is incomplete or not attributable to this armed session")
    active_ids = tuple(
        _identifier(item, path="reconciliation.active_lifecycle_ids")
        for item in value.active_lifecycle_ids
    )
    if active_ids != tuple(sorted(set(active_ids))):
        _reject("reconciliation active lifecycle identities are not canonical")
    if active_ids != request.active_lifecycle_ids:
        _reject("reconciliation active lifecycle identities do not match durable state")
    _validate_receipt(
        value.receipt,
        subject_id=request.session_id,
        subject_sha256=request.arm_sha256,
        status="RECONCILIATION_COMPLETE",
        reason_code=None,
    )


@dataclass(frozen=True, slots=True)
class CandidateProcessingRequest:
    arm: AutonomousSessionArm
    opportunity: AutonomousOpportunity
    observed_at: datetime


def _result_unsigned_payload(value: CandidateProcessingResult) -> dict[str, object]:
    return {
        "active_lifecycle_sha256": (
            None if value.active_lifecycle is None else value.active_lifecycle.lifecycle_sha256
        ),
        "disposition": value.disposition.value,
        "freeze": value.freeze,
        "mutation_state": value.mutation_state.value,
        "opportunity_id": value.opportunity_id,
        "opportunity_sha256": value.opportunity_sha256,
        "reason_code": value.reason_code,
        "receipt_sha256": value.receipt.receipt_sha256,
        "schema": "esscher.autonomous_candidate_result",
        "schema_version": 1,
        "session_id": value.session_id,
        "terminal_flat_proof_sha256": value.terminal_flat_proof_sha256,
    }


@dataclass(frozen=True, slots=True)
class CandidateProcessingResult:
    session_id: str
    opportunity_id: str
    opportunity_sha256: str
    disposition: AutonomousDisposition
    mutation_state: MutationState
    freeze: bool
    active_lifecycle: ActiveLifecycleIdentity | None
    terminal_flat_proof_sha256: str | None
    reason_code: str | None
    receipt: SanitizedIdentityReceipt
    result_sha256: str

    @classmethod
    def _create(
        cls,
        *,
        request: CandidateProcessingRequest,
        disposition: AutonomousDisposition,
        mutation_state: MutationState,
        terminal_flat_proof_sha256: str | None,
        reason_code: str | None,
        freeze: bool = False,
        active_lifecycle: ActiveLifecycleIdentity | None = None,
    ) -> CandidateProcessingResult:
        if type(freeze) is not bool:
            _reject("candidate.freeze must be a boolean")
        if active_lifecycle is not None:
            active_lifecycle_bytes(active_lifecycle)
            if (
                active_lifecycle.session_id != request.arm.session_id
                or active_lifecycle.opportunity_id != request.opportunity.opportunity_id
                or active_lifecycle.opportunity_sha256 != request.opportunity.opportunity_sha256
            ):
                _reject("candidate active lifecycle is not attributable to its opportunity")
        proof = (
            None
            if terminal_flat_proof_sha256 is None
            else _digest(terminal_flat_proof_sha256, path="terminal_flat_proof_sha256")
        )
        reason = _reason_code(reason_code, path="candidate.reason_code")
        receipt = SanitizedIdentityReceipt.create(
            receipt_id=f"PROCESS-{request.opportunity.opportunity_sha256[:16]}",
            subject_id=request.opportunity.opportunity_id,
            subject_sha256=request.opportunity.opportunity_sha256,
            observed_at=request.observed_at,
            status=f"CANDIDATE_{disposition.value}",
            reason_code=reason,
        )
        unsigned = {
            "active_lifecycle_sha256": (
                None if active_lifecycle is None else active_lifecycle.lifecycle_sha256
            ),
            "disposition": disposition.value,
            "freeze": freeze,
            "mutation_state": mutation_state.value,
            "opportunity_id": request.opportunity.opportunity_id,
            "opportunity_sha256": request.opportunity.opportunity_sha256,
            "reason_code": reason,
            "receipt_sha256": receipt.receipt_sha256,
            "schema": "esscher.autonomous_candidate_result",
            "schema_version": 1,
            "session_id": request.arm.session_id,
            "terminal_flat_proof_sha256": proof,
        }
        return cls(
            session_id=request.arm.session_id,
            opportunity_id=request.opportunity.opportunity_id,
            opportunity_sha256=request.opportunity.opportunity_sha256,
            disposition=disposition,
            mutation_state=mutation_state,
            freeze=freeze,
            active_lifecycle=active_lifecycle,
            terminal_flat_proof_sha256=proof,
            reason_code=reason,
            receipt=receipt,
            result_sha256=_sha256(_canonical_json(unsigned)),
        )

    @classmethod
    def abstained(
        cls,
        *,
        request: CandidateProcessingRequest,
        reason_code: str,
    ) -> CandidateProcessingResult:
        return cls._create(
            request=request,
            disposition=AutonomousDisposition.ABSTAINED,
            mutation_state=MutationState.NOT_ATTEMPTED,
            terminal_flat_proof_sha256=None,
            reason_code=reason_code,
        )

    @classmethod
    def terminal_flat(
        cls,
        *,
        request: CandidateProcessingRequest,
        terminal_flat_proof_sha256: str,
    ) -> CandidateProcessingResult:
        return cls._create(
            request=request,
            disposition=AutonomousDisposition.TERMINAL_FLAT,
            mutation_state=MutationState.CONFIRMED,
            terminal_flat_proof_sha256=terminal_flat_proof_sha256,
            reason_code=None,
        )

    @classmethod
    def active(
        cls,
        *,
        request: CandidateProcessingRequest,
        lifecycle_id: str,
    ) -> CandidateProcessingResult:
        return cls._create(
            request=request,
            disposition=AutonomousDisposition.ACTIVE,
            mutation_state=MutationState.CONFIRMED,
            terminal_flat_proof_sha256=None,
            reason_code=None,
            active_lifecycle=ActiveLifecycleIdentity.for_candidate(
                arm=request.arm,
                opportunity=request.opportunity,
                lifecycle_id=lifecycle_id,
            ),
        )

    @classmethod
    def rejected_before_mutation(
        cls,
        *,
        request: CandidateProcessingRequest,
        reason_code: str,
        freeze: bool = False,
    ) -> CandidateProcessingResult:
        return cls._create(
            request=request,
            disposition=AutonomousDisposition.REJECTED_BEFORE_MUTATION,
            mutation_state=MutationState.NOT_ATTEMPTED,
            terminal_flat_proof_sha256=None,
            reason_code=reason_code,
            freeze=freeze,
        )

    @classmethod
    def manual_reconciliation_required(
        cls,
        *,
        request: CandidateProcessingRequest,
        mutation_state: MutationState,
        reason_code: str,
    ) -> CandidateProcessingResult:
        return cls._create(
            request=request,
            disposition=AutonomousDisposition.MANUAL_RECONCILIATION_REQUIRED,
            mutation_state=mutation_state,
            terminal_flat_proof_sha256=None,
            reason_code=reason_code,
        )


def _validate_processing_result(
    value: CandidateProcessingResult,
    *,
    request: CandidateProcessingRequest,
) -> None:
    if type(value) is not CandidateProcessingResult:
        _reject("candidate processor returned an untyped result")
    if (
        value.session_id != request.arm.session_id
        or value.opportunity_id != request.opportunity.opportunity_id
        or value.opportunity_sha256 != request.opportunity.opportunity_sha256
    ):
        _reject("candidate result is not attributable to its exact opportunity")
    if value.result_sha256 != _sha256(_canonical_json(_result_unsigned_payload(value))):
        _reject("candidate result hash is invalid")
    _validate_receipt(
        value.receipt,
        subject_id=request.opportunity.opportunity_id,
        subject_sha256=request.opportunity.opportunity_sha256,
        status=f"CANDIDATE_{value.disposition.value}",
        reason_code=value.reason_code,
    )
    if type(value.freeze) is not bool:
        _reject("candidate freeze flag is invalid")
    if value.active_lifecycle is not None:
        active_lifecycle_bytes(value.active_lifecycle)
        if (
            value.active_lifecycle.session_id != request.arm.session_id
            or value.active_lifecycle.opportunity_id != request.opportunity.opportunity_id
            or value.active_lifecycle.opportunity_sha256 != request.opportunity.opportunity_sha256
        ):
            _reject("candidate active lifecycle identity mismatch")
    if value.disposition in {
        AutonomousDisposition.ABSTAINED,
        AutonomousDisposition.REJECTED_BEFORE_MUTATION,
    }:
        if (
            value.mutation_state is not MutationState.NOT_ATTEMPTED
            or value.active_lifecycle is not None
            or value.terminal_flat_proof_sha256 is not None
            or _reason_code(value.reason_code, path="candidate.reason_code", required=True) is None
        ):
            _reject("pre-mutation candidate result is inconsistent")
        return
    if value.disposition is AutonomousDisposition.TERMINAL_FLAT:
        if (
            value.mutation_state is not MutationState.CONFIRMED
            or value.freeze
            or value.active_lifecycle is not None
            or value.terminal_flat_proof_sha256 is None
            or value.reason_code is not None
        ):
            _reject("terminal-flat candidate result is inconsistent")
        _digest(value.terminal_flat_proof_sha256, path="terminal_flat_proof_sha256")
        return
    if value.disposition is AutonomousDisposition.MANUAL_RECONCILIATION_REQUIRED:
        if (
            value.mutation_state not in {MutationState.UNKNOWN, MutationState.PARTIAL}
            or value.freeze
            or value.active_lifecycle is not None
            or value.terminal_flat_proof_sha256 is not None
            or _reason_code(value.reason_code, path="candidate.reason_code", required=True) is None
        ):
            _reject("manual candidate result is inconsistent")
        return
    if value.disposition is AutonomousDisposition.ACTIVE:
        if (
            value.mutation_state is not MutationState.CONFIRMED
            or value.freeze
            or value.active_lifecycle is None
            or value.terminal_flat_proof_sha256 is not None
            or value.reason_code is not None
        ):
            _reject("active candidate result is inconsistent")
        return
    _reject("candidate disposition is unsupported")


@dataclass(frozen=True, slots=True)
class LifecycleCloseRequest:
    arm: AutonomousSessionArm
    lifecycle: ActiveLifecycleIdentity
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class LifecycleCloseResult:
    session_id: str
    lifecycle_id: str
    lifecycle_sha256: str
    disposition: AutonomousDisposition
    mutation_state: MutationState
    terminal_flat_proof_sha256: str | None
    reason_code: str | None
    receipt: SanitizedIdentityReceipt
    result_sha256: str

    @classmethod
    def terminal_flat(
        cls,
        *,
        request: LifecycleCloseRequest,
        terminal_flat_proof_sha256: str,
    ) -> LifecycleCloseResult:
        return cls._create(
            request=request,
            disposition=AutonomousDisposition.TERMINAL_FLAT,
            mutation_state=MutationState.CONFIRMED,
            terminal_flat_proof_sha256=terminal_flat_proof_sha256,
            reason_code=None,
        )

    @classmethod
    def manual_reconciliation_required(
        cls,
        *,
        request: LifecycleCloseRequest,
        mutation_state: MutationState,
        reason_code: str,
    ) -> LifecycleCloseResult:
        return cls._create(
            request=request,
            disposition=AutonomousDisposition.MANUAL_RECONCILIATION_REQUIRED,
            mutation_state=mutation_state,
            terminal_flat_proof_sha256=None,
            reason_code=reason_code,
        )

    @classmethod
    def _create(
        cls,
        *,
        request: LifecycleCloseRequest,
        disposition: AutonomousDisposition,
        mutation_state: MutationState,
        terminal_flat_proof_sha256: str | None,
        reason_code: str | None,
    ) -> LifecycleCloseResult:
        _validate_arm(request.arm)
        active_lifecycle_bytes(request.lifecycle)
        observed_at = _utc_datetime(request.observed_at, path="close.observed_at")
        if request.lifecycle.session_id != request.arm.session_id:
            _reject("close lifecycle belongs to another session")
        proof = (
            None
            if terminal_flat_proof_sha256 is None
            else _digest(terminal_flat_proof_sha256, path="close.terminal_flat_proof_sha256")
        )
        reason = _reason_code(reason_code, path="close.reason_code")
        receipt = SanitizedIdentityReceipt.create(
            receipt_id=f"CLOSE-{request.lifecycle.lifecycle_sha256[:16]}",
            subject_id=request.lifecycle.lifecycle_id,
            subject_sha256=request.lifecycle.lifecycle_sha256,
            observed_at=observed_at,
            status=f"LIFECYCLE_{disposition.value}",
            reason_code=reason,
        )
        unsigned = {
            "disposition": disposition.value,
            "lifecycle_id": request.lifecycle.lifecycle_id,
            "lifecycle_sha256": request.lifecycle.lifecycle_sha256,
            "mutation_state": mutation_state.value,
            "reason_code": reason,
            "receipt_sha256": receipt.receipt_sha256,
            "schema": "esscher.autonomous_lifecycle_close_result",
            "schema_version": 1,
            "session_id": request.arm.session_id,
            "terminal_flat_proof_sha256": proof,
        }
        return cls(
            session_id=request.arm.session_id,
            lifecycle_id=request.lifecycle.lifecycle_id,
            lifecycle_sha256=request.lifecycle.lifecycle_sha256,
            disposition=disposition,
            mutation_state=mutation_state,
            terminal_flat_proof_sha256=proof,
            reason_code=reason,
            receipt=receipt,
            result_sha256=_sha256(_canonical_json(unsigned)),
        )


def _validate_close_result(
    value: LifecycleCloseResult,
    *,
    request: LifecycleCloseRequest,
) -> None:
    if type(value) is not LifecycleCloseResult:
        _reject("lifecycle closer returned an untyped result")
    unsigned = {
        "disposition": value.disposition.value,
        "lifecycle_id": value.lifecycle_id,
        "lifecycle_sha256": value.lifecycle_sha256,
        "mutation_state": value.mutation_state.value,
        "reason_code": value.reason_code,
        "receipt_sha256": value.receipt.receipt_sha256,
        "schema": "esscher.autonomous_lifecycle_close_result",
        "schema_version": 1,
        "session_id": value.session_id,
        "terminal_flat_proof_sha256": value.terminal_flat_proof_sha256,
    }
    if (
        value.session_id != request.arm.session_id
        or value.lifecycle_id != request.lifecycle.lifecycle_id
        or value.lifecycle_sha256 != request.lifecycle.lifecycle_sha256
        or value.result_sha256 != _sha256(_canonical_json(unsigned))
    ):
        _reject("lifecycle close result is not attributable to its active identity")
    _validate_receipt(
        value.receipt,
        subject_id=request.lifecycle.lifecycle_id,
        subject_sha256=request.lifecycle.lifecycle_sha256,
        status=f"LIFECYCLE_{value.disposition.value}",
        reason_code=value.reason_code,
    )
    if value.disposition is AutonomousDisposition.TERMINAL_FLAT:
        if (
            value.mutation_state is not MutationState.CONFIRMED
            or value.terminal_flat_proof_sha256 is None
            or value.reason_code is not None
        ):
            _reject("terminal lifecycle close result is inconsistent")
        _digest(value.terminal_flat_proof_sha256, path="close.terminal_flat_proof_sha256")
        return
    if value.disposition is AutonomousDisposition.MANUAL_RECONCILIATION_REQUIRED:
        if (
            value.mutation_state not in {MutationState.UNKNOWN, MutationState.PARTIAL}
            or value.terminal_flat_proof_sha256 is not None
            or _reason_code(value.reason_code, path="close.reason_code", required=True) is None
        ):
            _reject("manual lifecycle close result is inconsistent")
        return
    _reject("lifecycle closer returned an unsupported disposition")


class ReconciliationPort(Protocol):
    def reconcile(self, request: ReconciliationRequest) -> ReconciliationReceipt: ...


class DueWindowCollectorPort(Protocol):
    def collect_due(self, request: DueWindowRequest) -> tuple[AutonomousOpportunity, ...]: ...


class CandidateProcessorPort(Protocol):
    def process(self, request: CandidateProcessingRequest) -> CandidateProcessingResult: ...


class LifecycleCloserPort(Protocol):
    def close_and_reconcile(self, request: LifecycleCloseRequest) -> LifecycleCloseResult: ...


@dataclass(frozen=True, slots=True)
class DueWindowRequest:
    arm: AutonomousSessionArm
    window: AutonomousWindow
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class AutonomousSessionPorts:
    reconciler: ReconciliationPort
    collector: DueWindowCollectorPort
    processor: CandidateProcessorPort
    lifecycle_closer: LifecycleCloserPort


@dataclass(frozen=True, slots=True)
class AutonomousRunResult:
    session_id: str
    arm_sha256: str
    observed_at: datetime
    processed_opportunity_ids: tuple[str, ...]
    disposition_counts: Mapping[AutonomousDisposition, int]
    manual_reasons: tuple[str, ...]

    @classmethod
    def from_store(
        cls,
        *,
        arm: AutonomousSessionArm,
        store: AutonomousSessionStore,
        observed_at: datetime,
        processed_opportunity_ids: tuple[str, ...],
    ) -> AutonomousRunResult:
        stored = store.disposition_counts(arm.session_id)
        counts = {
            disposition: stored.get(disposition.value, 0) for disposition in AutonomousDisposition
        }
        return cls(
            session_id=arm.session_id,
            arm_sha256=arm.arm_sha256,
            observed_at=observed_at,
            processed_opportunity_ids=processed_opportunity_ids,
            disposition_counts=MappingProxyType(counts),
            manual_reasons=store.manual_reasons(arm.session_id),
        )


@dataclass(frozen=True, slots=True)
class AutonomousSessionSummary:
    session_id: str
    arm_sha256: str
    finalized_at: datetime
    disposition_counts: Mapping[str, int]
    active_lifecycle_ids: tuple[str, ...]
    manual_reasons: tuple[str, ...]
    terminal_flat_proven: bool
    summary_sha256: str


def _summary_unsigned_payload(value: AutonomousSessionSummary) -> dict[str, object]:
    if type(value) is not AutonomousSessionSummary:
        _reject("summary must be an AutonomousSessionSummary")
    counts: dict[str, int] = {}
    if not isinstance(value.disposition_counts, Mapping):
        _reject("summary disposition counts must be a mapping")
    for disposition in AutonomousDisposition:
        count = value.disposition_counts.get(disposition.value, 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            _reject("summary disposition counts must be non-negative integers")
        counts[disposition.value] = count
    active = tuple(
        _identifier(item, path="summary.active_lifecycle_ids")
        for item in value.active_lifecycle_ids
    )
    reasons = tuple(
        _reason_code(item, path="summary.manual_reasons", required=True)
        for item in value.manual_reasons
    )
    if active != tuple(sorted(set(active))) or reasons != tuple(sorted(set(reasons))):
        _reject("summary identities and reasons must be canonical unique tuples")
    if type(value.terminal_flat_proven) is not bool:
        _reject("summary terminal_flat_proven must be a boolean")
    if value.terminal_flat_proven != (not active and not reasons):
        _reject("summary terminal-flat proof disagrees with active/manual state")
    return {
        "active_lifecycle_ids": list(active),
        "arm_sha256": _digest(value.arm_sha256, path="summary.arm_sha256"),
        "disposition_counts": counts,
        "finalized_at": _timestamp_text(value.finalized_at, path="summary.finalized_at"),
        "manual_reasons": list(reasons),
        "schema": AUTONOMOUS_SESSION_SUMMARY_SCHEMA,
        "schema_version": AUTONOMOUS_SESSION_SUMMARY_SCHEMA_VERSION,
        "session_id": _identifier(value.session_id, path="summary.session_id"),
        "terminal_flat_proven": value.terminal_flat_proven,
    }


def autonomous_session_summary_bytes(value: AutonomousSessionSummary) -> bytes:
    unsigned = _summary_unsigned_payload(value)
    expected = _sha256(_canonical_json(unsigned))
    if value.summary_sha256 != expected:
        _reject("summary SHA-256 does not bind its semantic payload")
    return _canonical_json({**unsigned, "summary_sha256": expected})


def _build_summary(
    *,
    arm: AutonomousSessionArm,
    finalized_at: datetime,
    disposition_counts: Mapping[str, int],
    active_lifecycle_ids: tuple[str, ...],
    manual_reasons: tuple[str, ...],
) -> AutonomousSessionSummary:
    active = tuple(sorted(set(active_lifecycle_ids)))
    reasons = tuple(sorted(set(manual_reasons)))
    draft = AutonomousSessionSummary(
        session_id=arm.session_id,
        arm_sha256=arm.arm_sha256,
        finalized_at=_utc_datetime(finalized_at, path="summary.finalized_at"),
        disposition_counts=MappingProxyType(
            {
                disposition.value: disposition_counts.get(disposition.value, 0)
                for disposition in AutonomousDisposition
            }
        ),
        active_lifecycle_ids=active,
        manual_reasons=reasons,
        terminal_flat_proven=not active and not reasons,
        summary_sha256="0" * 64,
    )
    digest = _sha256(_canonical_json(_summary_unsigned_payload(draft)))
    return AutonomousSessionSummary(
        session_id=draft.session_id,
        arm_sha256=draft.arm_sha256,
        finalized_at=draft.finalized_at,
        disposition_counts=draft.disposition_counts,
        active_lifecycle_ids=draft.active_lifecycle_ids,
        manual_reasons=draft.manual_reasons,
        terminal_flat_proven=draft.terminal_flat_proven,
        summary_sha256=digest,
    )


def _parse_summary(raw: bytes) -> AutonomousSessionSummary:
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (
        AutonomousArmRejected,
        _DuplicateFieldError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise AutonomousStoreConflict("stored autonomous summary is invalid") from error
    fields = frozenset(
        {
            "schema",
            "schema_version",
            "summary_sha256",
            "session_id",
            "arm_sha256",
            "finalized_at",
            "disposition_counts",
            "active_lifecycle_ids",
            "manual_reasons",
            "terminal_flat_proven",
        }
    )
    payload = _strict_object(decoded, fields=fields, path="summary")
    if (
        payload["schema"] != AUTONOMOUS_SESSION_SUMMARY_SCHEMA
        or payload["schema_version"] != AUTONOMOUS_SESSION_SUMMARY_SCHEMA_VERSION
    ):
        _reject("summary has an unsupported schema")
    counts_value = payload["disposition_counts"]
    active_value = payload["active_lifecycle_ids"]
    reasons_value = payload["manual_reasons"]
    if (
        not isinstance(counts_value, Mapping)
        or not isinstance(active_value, list)
        or not isinstance(reasons_value, list)
    ):
        _reject("summary collections are malformed")
    value = AutonomousSessionSummary(
        session_id=_identifier(payload["session_id"], path="summary.session_id"),
        arm_sha256=_digest(payload["arm_sha256"], path="summary.arm_sha256"),
        finalized_at=_timestamp(payload["finalized_at"], path="summary.finalized_at"),
        disposition_counts=MappingProxyType(dict(counts_value)),
        active_lifecycle_ids=tuple(active_value),
        manual_reasons=tuple(reasons_value),
        terminal_flat_proven=(
            payload["terminal_flat_proven"]
            if type(payload["terminal_flat_proven"]) is bool
            else _reject("summary terminal_flat_proven must be boolean")
        ),
        summary_sha256=_digest(payload["summary_sha256"], path="summary.summary_sha256"),
    )
    if raw != autonomous_session_summary_bytes(value):
        _reject("stored autonomous summary is non-canonical")
    return value


class AutonomousSessionCoordinator:
    """One fail-closed, port-injected coordinator for a frozen PAPER session."""

    def __init__(
        self,
        *,
        arm: AutonomousSessionArm,
        store: AutonomousSessionStore,
        ports: AutonomousSessionPorts,
        release_code_sha256: str,
        account_fingerprint_sha256: str,
    ) -> None:
        self.arm = arm
        self.store = store
        self.ports = ports
        self._release_code_sha256 = _digest(release_code_sha256, path="release_code_sha256")
        self._account_fingerprint_sha256 = _digest(
            account_fingerprint_sha256,
            path="account_fingerprint_sha256",
        )

    def _validate_before_ports(self) -> None:
        _validate_arm(self.arm)
        if (
            self.arm.release_code_sha256 != self._release_code_sha256
            or self.arm.account_fingerprint_sha256 != self._account_fingerprint_sha256
        ):
            raise AutonomousCoordinatorRejected(
                "runtime release/account identity does not match the arm"
            )

    def _run_hard_flat(self, *, observed_at: datetime) -> AutonomousRunResult:
        if self.store.final_summary(self.arm.session_id) is not None:
            return AutonomousRunResult.from_store(
                arm=self.arm,
                store=self.store,
                observed_at=observed_at,
                processed_opportunity_ids=(),
            )
        for lifecycle in self.store.active_lifecycles(self.arm.session_id):
            request = LifecycleCloseRequest(
                arm=self.arm,
                lifecycle=lifecycle,
                observed_at=observed_at,
            )
            try:
                result = self.ports.lifecycle_closer.close_and_reconcile(request)
                _validate_close_result(result, request=request)
            except Exception:
                self.store.mark_manual_reconciliation_required(
                    arm=self.arm,
                    reason_code="HARD_FLAT_UNRESOLVED",
                    observed_at=observed_at,
                    opportunity_id=lifecycle.opportunity_id,
                    lifecycle_id=lifecycle.lifecycle_id,
                )
                continue
            if result.disposition is AutonomousDisposition.TERMINAL_FLAT:
                self.store.record_lifecycle_terminal_flat(
                    arm=self.arm,
                    lifecycle=lifecycle,
                    terminal_flat_proof_sha256=result.terminal_flat_proof_sha256 or "",
                    observed_at=observed_at,
                )
            else:
                self.store.mark_manual_reconciliation_required(
                    arm=self.arm,
                    reason_code=result.reason_code or "HARD_FLAT_UNRESOLVED",
                    observed_at=observed_at,
                    opportunity_id=lifecycle.opportunity_id,
                    lifecycle_id=lifecycle.lifecycle_id,
                )
        if self.store.active_lifecycle_ids(self.arm.session_id):
            self.store.mark_manual_reconciliation_required(
                arm=self.arm,
                reason_code="HARD_FLAT_UNRESOLVED",
                observed_at=observed_at,
            )
        self.store.finalize_session(arm=self.arm, finalized_at=observed_at)
        return AutonomousRunResult.from_store(
            arm=self.arm,
            store=self.store,
            observed_at=observed_at,
            processed_opportunity_ids=(),
        )

    def run(self, *, observed_at: datetime) -> AutonomousRunResult:
        """Reconcile first, then process all currently due unique candidates once."""

        self._validate_before_ports()
        observed_at = _utc_datetime(observed_at, path="observed_at")
        self.store.ensure_arm(self.arm)
        if self.store.claimed_opportunity_ids(self.arm.session_id):
            self.store.mark_manual_reconciliation_required(
                arm=self.arm,
                reason_code="CLAIM_RECOVERY_UNKNOWN",
                observed_at=observed_at,
            )
            return AutonomousRunResult.from_store(
                arm=self.arm,
                store=self.store,
                observed_at=observed_at,
                processed_opportunity_ids=(),
            )
        if observed_at >= self.arm.hard_flat_at or observed_at >= self.arm.ends_at:
            return self._run_hard_flat(observed_at=observed_at)
        if self.store.session_state(self.arm.session_id) == "MANUAL_RECONCILIATION_REQUIRED":
            return AutonomousRunResult.from_store(
                arm=self.arm,
                store=self.store,
                observed_at=observed_at,
                processed_opportunity_ids=(),
            )

        reconciliation_request = ReconciliationRequest(
            session_id=self.arm.session_id,
            arm_sha256=self.arm.arm_sha256,
            account_fingerprint_sha256=self.arm.account_fingerprint_sha256,
            execution_protocol_sha256=self.arm.execution_protocol_sha256,
            observed_at=observed_at,
            phase="STARTUP",
            active_lifecycle_ids=self.store.active_lifecycle_ids(self.arm.session_id),
        )
        try:
            reconciliation = self.ports.reconciler.reconcile(reconciliation_request)
            _validate_reconciliation(reconciliation, request=reconciliation_request)
        except Exception:
            self.store.mark_manual_reconciliation_required(
                arm=self.arm,
                reason_code="RECONCILIATION_IDENTITY_MISMATCH",
                observed_at=observed_at,
            )
            return AutonomousRunResult.from_store(
                arm=self.arm,
                store=self.store,
                observed_at=observed_at,
                processed_opportunity_ids=(),
            )
        due_windows = tuple(
            window
            for window in self.arm.windows
            if window.opens_at <= observed_at < window.closes_at
        )
        processed: list[str] = []
        for window in due_windows:
            collected = self.ports.collector.collect_due(
                DueWindowRequest(arm=self.arm, window=window, observed_at=observed_at)
            )
            if type(collected) is not tuple:
                raise AutonomousCoordinatorRejected("collector must return an immutable tuple")
            unique: dict[str, AutonomousOpportunity] = {}
            for opportunity in collected:
                _validate_opportunity(opportunity, arm=self.arm)
                if opportunity.window_id != window.window_id:
                    raise AutonomousCoordinatorRejected(
                        "collector returned an opportunity for a different window"
                    )
                prior = unique.get(opportunity.opportunity_id)
                if prior is not None and prior.opportunity_sha256 != opportunity.opportunity_sha256:
                    raise AutonomousCoordinatorRejected(
                        "collector returned conflicting opportunity identities"
                    )
                unique[opportunity.opportunity_id] = opportunity
            for opportunity_id in sorted(unique):
                opportunity = unique[opportunity_id]
                claim = self.store.claim_opportunity(
                    arm=self.arm,
                    opportunity=opportunity,
                    observed_at=observed_at,
                )
                if claim is not AutonomousClaimState.CLAIMED:
                    continue
                request = CandidateProcessingRequest(
                    arm=self.arm,
                    opportunity=opportunity,
                    observed_at=observed_at,
                )
                try:
                    result = self.ports.processor.process(request)
                    _validate_processing_result(result, request=request)
                except Exception:
                    self.store.mark_manual_reconciliation_required(
                        arm=self.arm,
                        reason_code="CLAIM_RECOVERY_UNKNOWN",
                        observed_at=observed_at,
                        opportunity_id=opportunity.opportunity_id,
                    )
                    return AutonomousRunResult.from_store(
                        arm=self.arm,
                        store=self.store,
                        observed_at=observed_at,
                        processed_opportunity_ids=tuple(processed),
                    )
                if result.disposition is AutonomousDisposition.TERMINAL_FLAT:
                    self.store.record_terminal_flat(
                        arm=self.arm,
                        opportunity=opportunity,
                        terminal_flat_proof_sha256=result.terminal_flat_proof_sha256 or "",
                        observed_at=observed_at,
                    )
                elif result.disposition is AutonomousDisposition.ACTIVE:
                    assert result.active_lifecycle is not None
                    self.store.record_active_lifecycle(
                        arm=self.arm,
                        opportunity=opportunity,
                        lifecycle=result.active_lifecycle,
                        observed_at=observed_at,
                    )
                else:
                    self.store.record_disposition(
                        arm=self.arm,
                        opportunity=opportunity,
                        disposition=result.disposition.value,
                        observed_at=observed_at,
                    )
                processed.append(opportunity.opportunity_id)
                if result.disposition is AutonomousDisposition.MANUAL_RECONCILIATION_REQUIRED:
                    self.store.mark_manual_reconciliation_required(
                        arm=self.arm,
                        reason_code=result.reason_code or "PORT_OUTPUT_INVALID",
                        observed_at=observed_at,
                        opportunity_id=opportunity.opportunity_id,
                    )
                    return AutonomousRunResult.from_store(
                        arm=self.arm,
                        store=self.store,
                        observed_at=observed_at,
                        processed_opportunity_ids=tuple(processed),
                    )
                if result.freeze:
                    self.store.mark_manual_reconciliation_required(
                        arm=self.arm,
                        reason_code=result.reason_code or "RISK_FREEZE",
                        observed_at=observed_at,
                        opportunity_id=opportunity.opportunity_id,
                    )
                    return AutonomousRunResult.from_store(
                        arm=self.arm,
                        store=self.store,
                        observed_at=observed_at,
                        processed_opportunity_ids=tuple(processed),
                    )
        return AutonomousRunResult.from_store(
            arm=self.arm,
            store=self.store,
            observed_at=observed_at,
            processed_opportunity_ids=tuple(processed),
        )


__all__ = [
    "AUTONOMOUS_OPPORTUNITY_SCHEMA",
    "AUTONOMOUS_OPPORTUNITY_SCHEMA_VERSION",
    "AUTONOMOUS_SESSION_ARM_SCHEMA",
    "AUTONOMOUS_SESSION_ARM_SCHEMA_VERSION",
    "AUTONOMOUS_SESSION_SUMMARY_SCHEMA",
    "AUTONOMOUS_SESSION_SUMMARY_SCHEMA_VERSION",
    "AUTONOMOUS_STORE_SCHEMA_VERSION",
    "AUTONOMOUS_WINDOW_SCHEMA",
    "AUTONOMOUS_WINDOW_SCHEMA_VERSION",
    "AutonomousArmRejected",
    "AutonomousClaimState",
    "AutonomousCoordinatorRejected",
    "AutonomousDisposition",
    "AutonomousOpportunity",
    "AutonomousRunResult",
    "AutonomousSessionArm",
    "AutonomousSessionCoordinator",
    "AutonomousSessionPorts",
    "AutonomousSessionStore",
    "AutonomousSessionSummary",
    "AutonomousStoreConflict",
    "AutonomousWindow",
    "CandidateProcessingRequest",
    "CandidateProcessingResult",
    "DueWindowRequest",
    "LifecycleCloseRequest",
    "LifecycleCloseResult",
    "MutationState",
    "ReconciliationReceipt",
    "ReconciliationRequest",
    "SanitizedIdentityReceipt",
    "autonomous_session_arm_bytes",
    "autonomous_session_arm_sha256",
    "autonomous_session_summary_bytes",
    "parse_autonomous_session_arm",
]
