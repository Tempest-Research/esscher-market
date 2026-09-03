"""Fixture/adapter contract so the #82 fake replay consumes shadow decisions.

The shadow replay plan is a strict, immutable handoff artifact: the runtime
fake-proven runner can consume the same decisions without reinterpretation and
without account or broker access.  Every planned lifecycle is terminal-flat by
construction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, NoReturn

from .direction_receipts import _DuplicateFieldError, _unique_object
from .evaluation import EventEvaluation
from .shadow_ledger import SHADOW_LIFECYCLE, SHADOW_PNL_CLASS, shadow_decision_episode
from .shadow_runner import ShadowRunResult

SHADOW_REPLAY_PLAN_SCHEMA: Final = "esscher.shadow_replay_plan"
SCHEMA_VERSION: Final = 1
PLAN_LABELS: Final = (
    "NO_BROKER_MUTATION",
    "NO_CREDENTIALS",
    "SHADOW_ONLY",
    "SOURCE_GROUNDED",
)


class ReplayPlanReason(StrEnum):
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    DUPLICATE_FIELD = "DUPLICATE_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MALFORMED_VALUE = "MALFORMED_VALUE"
    NOT_TERMINAL_FLAT = "NOT_TERMINAL_FLAT"


class ReplayPlanRejected(ValueError):
    def __init__(self, reason: ReplayPlanReason, path: str, detail: str) -> None:
        super().__init__(f"{reason.value} at {path}: {detail}")
        self.reason = reason
        self.path = path
        self.detail = detail


def _reject(reason: ReplayPlanReason, path: str, detail: str) -> NoReturn:
    raise ReplayPlanRejected(reason, path, detail)


_PLAN_FIELDS: Final = frozenset(
    {"schema", "schema_version", "report_sha256", "claim", "labels", "events"}
)
_EVENT_FIELDS: Final = frozenset(
    {
        "event_id",
        "decision_episode_id",
        "direction",
        "entry_at",
        "exit_at",
        "lifecycle_outcome",
        "pnl_classification",
        "final_flat",
        "net_pnl",
    }
)


def _decode(raw: bytes, *, path: str) -> Mapping[str, object]:
    if type(raw) is not bytes:
        _reject(ReplayPlanReason.MALFORMED_VALUE, path, "plan must be immutable bytes")
    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateFieldError):
        _reject(ReplayPlanReason.MALFORMED_VALUE, path, "plan must be strict UTF-8 JSON")
    if not isinstance(decoded, Mapping):
        _reject(ReplayPlanReason.MALFORMED_VALUE, path, "plan must be a JSON object")
    return decoded


@dataclass(frozen=True, slots=True)
class ShadowReplayEvent:
    event_id: str
    decision_episode_id: str
    direction: str
    entry_at: str
    exit_at: str
    lifecycle_outcome: str
    pnl_classification: str
    final_flat: bool
    net_pnl: Decimal


@dataclass(frozen=True, slots=True)
class ShadowReplayPlan:
    report_sha256: str
    claim: str
    labels: tuple[str, ...]
    events: tuple[ShadowReplayEvent, ...]


def build_shadow_replay_plan(
    result: ShadowRunResult,
    *,
    candidate_id: str,
    policy_sha256: str,
    evidence_sha256: str,
    feature_sha256: str,
    snapshot_sha256: str,
    costs: Mapping[str, Decimal],
) -> dict[str, object]:
    """Build the canonical #82 handoff plan from an accepted shadow run."""

    if not result.accepted:
        _reject(
            ReplayPlanReason.MALFORMED_VALUE,
            "shadow_replay_plan",
            "rejected shadow runs produce no replay plan",
        )
    events = []
    for receipt in result.receipts:
        evaluation = result.evaluations.get(receipt.event_id)
        if not isinstance(evaluation, EventEvaluation):
            _reject(
                ReplayPlanReason.MALFORMED_VALUE,
                f"events[{receipt.event_id}]",
                "accepted runs must carry one evaluation per event",
            )
        values, _, _ = shadow_decision_episode(
            receipt=receipt,
            evaluation=evaluation,
            symbol=result.symbols[receipt.event_id],
            candidate_id=candidate_id,
            policy_sha256=policy_sha256,
            evidence_sha256=evidence_sha256,
            feature_sha256=feature_sha256,
            snapshot_sha256=snapshot_sha256,
        )
        gross = Decimal(f"{evaluation.signed_residual:.12f}")
        events.append(
            {
                "event_id": receipt.event_id,
                "decision_episode_id": values["episode_id"],
                "direction": receipt.direction.value,
                "entry_at": evaluation.entry_at.isoformat().replace("+00:00", "Z"),
                "exit_at": evaluation.exit_at.isoformat().replace("+00:00", "Z"),
                "lifecycle_outcome": SHADOW_LIFECYCLE,
                "pnl_classification": SHADOW_PNL_CLASS,
                "final_flat": True,
                "net_pnl": str(gross - costs.get(receipt.event_id, Decimal("0"))),
            }
        )
    return {
        "schema": SHADOW_REPLAY_PLAN_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "report_sha256": result.sha256,
        "claim": result.claim,
        "labels": list(PLAN_LABELS),
        "events": events,
    }


def shadow_replay_plan_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def parse_shadow_replay_plan(raw: bytes, *, path: str = "shadow_replay_plan") -> ShadowReplayPlan:
    """Strictly parse one canonical shadow replay plan."""

    record = _decode(raw, path=path)
    extra = set(record) - _PLAN_FIELDS
    if extra:
        _reject(ReplayPlanReason.UNKNOWN_FIELD, path, f"unknown fields: {sorted(extra)}")
    missing = _PLAN_FIELDS - set(record)
    if missing:
        _reject(ReplayPlanReason.MISSING_FIELD, path, f"missing fields: {sorted(missing)}")
    if record["schema"] != SHADOW_REPLAY_PLAN_SCHEMA or record["schema_version"] != SCHEMA_VERSION:
        _reject(ReplayPlanReason.UNSUPPORTED_SCHEMA, path, "unsupported plan schema or version")
    labels = record["labels"]
    if not isinstance(labels, list) or tuple(labels) != PLAN_LABELS:
        _reject(
            ReplayPlanReason.MALFORMED_VALUE,
            f"{path}.labels",
            "labels must match the plan contract",
        )
    raw_events = record["events"]
    if not isinstance(raw_events, list):
        _reject(ReplayPlanReason.MALFORMED_VALUE, f"{path}.events", "events must be a list")
    events = []
    for index, item in enumerate(raw_events):
        if not isinstance(item, Mapping):
            _reject(
                ReplayPlanReason.MALFORMED_VALUE, f"{path}.events[{index}]", "must be an object"
            )
        event_extra = set(item) - _EVENT_FIELDS
        if event_extra:
            _reject(
                ReplayPlanReason.UNKNOWN_FIELD,
                f"{path}.events[{index}]",
                f"unknown fields: {sorted(event_extra)}",
            )
        event_missing = _EVENT_FIELDS - set(item)
        if event_missing:
            _reject(
                ReplayPlanReason.MISSING_FIELD,
                f"{path}.events[{index}]",
                f"missing fields: {sorted(event_missing)}",
            )
        if item["final_flat"] is not True:
            _reject(
                ReplayPlanReason.NOT_TERMINAL_FLAT,
                f"{path}.events[{index}].final_flat",
                "shadow replay events must be terminal-flat",
            )
        try:
            net = Decimal(str(item["net_pnl"]))
        except Exception as error:
            _reject(ReplayPlanReason.MALFORMED_VALUE, f"{path}.events[{index}].net_pnl", str(error))
        events.append(
            ShadowReplayEvent(
                event_id=str(item["event_id"]),
                decision_episode_id=str(item["decision_episode_id"]),
                direction=str(item["direction"]),
                entry_at=str(item["entry_at"]),
                exit_at=str(item["exit_at"]),
                lifecycle_outcome=str(item["lifecycle_outcome"]),
                pnl_classification=str(item["pnl_classification"]),
                final_flat=True,
                net_pnl=net,
            )
        )
    return ShadowReplayPlan(
        report_sha256=str(record["report_sha256"]),
        claim=str(record["claim"]),
        labels=tuple(labels),
        events=tuple(events),
    )


def plan_identity(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(shadow_replay_plan_bytes(payload)).hexdigest()
