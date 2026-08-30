"""Deterministic Gate D comparison harness and report contract.

The tournament compares cash/no-trade, shares, one long option, and a
defined-risk debit vertical for the same frozen directional decisions. All
failures stay in the denominator. A promoted expression must satisfy the
declared after-cost objective and evidence threshold; otherwise the report
says ``NO_EXPRESSION`` and PAPER mutation stays blocked.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from ringdown_market.execution.expression.economics import (
    ExpressionEconomics,
    after_cost_edge_basis_points,
    cash_economics,
    debit_vertical_economics,
    option_economics,
    shares_economics,
)
from ringdown_market.execution.expression.geometry import (
    select_long_contract,
    select_package,
    select_vertical_geometry,
)
from ringdown_market.execution.expression.observations import (
    ExpressionMarketSnapshot,
    expression_market_snapshot_sha256,
)
from ringdown_market.execution.expression.policy import PromotedExpressionPolicy
from ringdown_market.execution.expression.reasons import (
    NO_EXPRESSION,
    ExpressionKind,
    ExpressionReason,
    ExpressionRejected,
)
from ringdown_market.execution.models import OptionType
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes

GATE_D_REPORT_SCHEMA: Final = "esscher.gate_d_report"
GATE_D_REPORT_SCHEMA_VERSION: Final = 1
PROMOTION_BELOW_THRESHOLD: Final = "BELOW_EVIDENCE_THRESHOLD"
PROMOTION_INSUFFICIENT_EVENTS: Final = "INSUFFICIENT_EVENTS"
PROMOTION_NO_COMPARED_EVENTS: Final = "NO_COMPARED_EVENTS"
OPTION_HISTORY_AVAILABLE: Final = "AVAILABLE"
OPTION_HISTORY_NOT_RUN: Final = "NOT_RUN"
GATE_D_CLAIMS: Final = (
    "NOT_ALPHA_EVIDENCE",
    "OPTION_FILL_PROVES_ELIGIBILITY_NOT_SUPERIORITY",
    "UNDERLYING_RETURNS_ARE_NOT_OPTION_PNL",
    "NO_PAPER_MUTATION_AUTHORIZED",
)
_EXPRESSION_ORDER: Final = (
    ExpressionKind.CASH_NO_TRADE,
    ExpressionKind.SHARES,
    ExpressionKind.ONE_LONG_OPTION,
    ExpressionKind.DEBIT_VERTICAL,
)


@dataclass(frozen=True, slots=True)
class TournamentEvent:
    """One comparison event: validated decision plus immutable observations.

    The decision timestamp is carried by the snapshot observation clock and
    the exit clock is one frozen value shared by every expression, so all
    expressions are compared on identical event terms.
    """

    event_id: str
    decision_direction: str
    decision_sha256: str
    outcome_direction: str | None
    snapshot: ExpressionMarketSnapshot
    exit_clock_at: datetime

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id must be non-empty text")
        if self.decision_direction not in {"UP", "DOWN"}:
            raise ValueError("tournament events require a validated UP or DOWN decision")
        if self.outcome_direction not in {"UP", "DOWN", None}:
            raise ValueError("outcome_direction must be UP, DOWN, or absent")
        if self.exit_clock_at.tzinfo != UTC:
            raise ValueError("exit_clock_at must be UTC")
        if self.exit_clock_at < self.snapshot.observation_clock_at:
            raise ValueError("exit clock cannot precede the observation clock")


def _event_economics(
    event: TournamentEvent, policy: PromotedExpressionPolicy
) -> tuple[ExpressionEconomics, ...]:
    direction_is_up = event.decision_direction == "UP"
    snapshot = event.snapshot
    asof = snapshot.observation_clock_at.date()
    economics: list[ExpressionEconomics] = [cash_economics(event.event_id)]
    economics.append(
        shares_economics(event.event_id, snapshot, policy, direction_is_up=direction_is_up)
    )
    option_type = OptionType.CALL if direction_is_up else OptionType.PUT
    try:
        contract = select_long_contract(
            snapshot, policy, option_type=option_type, asof=asof, direction_is_up=direction_is_up
        )
    except ExpressionRejected as error:
        economics.append(
            ExpressionEconomics(
                event_id=event.event_id,
                expression_kind=ExpressionKind.ONE_LONG_OPTION,
                outcome="REJECTED",
                reason=error.reason,
                entry_cost=None,
                max_loss=None,
                entry_spread_cost=None,
            )
        )
        economics.append(
            ExpressionEconomics(
                event_id=event.event_id,
                expression_kind=ExpressionKind.DEBIT_VERTICAL,
                outcome="REJECTED",
                reason=error.reason,
                entry_cost=None,
                max_loss=None,
                entry_spread_cost=None,
            )
        )
        return tuple(economics)
    economics.append(option_economics(event.event_id, contract, policy))
    try:
        geometry = select_vertical_geometry(
            snapshot, policy, direction_is_up=direction_is_up, asof=asof
        )
        package = select_package(snapshot, geometry)
        economics.append(debit_vertical_economics(event.event_id, package, geometry.width, policy))
    except ExpressionRejected as error:
        economics.append(
            ExpressionEconomics(
                event_id=event.event_id,
                expression_kind=ExpressionKind.DEBIT_VERTICAL,
                outcome="REJECTED",
                reason=error.reason,
                entry_cost=None,
                max_loss=None,
                entry_spread_cost=None,
            )
        )
    return tuple(economics)


def _economics_payload(item: ExpressionEconomics) -> dict[str, object]:
    return {
        "expression_kind": item.expression_kind.value,
        "outcome": item.outcome,
        "reason": None if item.reason is None else item.reason.value,
        "entry_cost": None if item.entry_cost is None else str(item.entry_cost),
        "max_loss": None if item.max_loss is None else str(item.max_loss),
        "entry_spread_cost": (
            None if item.entry_spread_cost is None else str(item.entry_spread_cost)
        ),
    }


def _direction_hit_row(
    event: TournamentEvent,
    all_economics: Mapping[str, tuple[ExpressionEconomics, ...]],
    kind: ExpressionKind,
) -> int:
    """Count one directional hit only for compared rows of one expression."""

    if kind is ExpressionKind.CASH_NO_TRADE or event.outcome_direction is None:
        return 0
    rows = tuple(
        item
        for item in all_economics[event.event_id]
        if item.expression_kind is kind and item.compared
    )
    if not rows:
        return 0
    return 1 if event.outcome_direction == event.decision_direction else 0


@dataclass(frozen=True, slots=True)
class GateDReport:
    """The canonical Gate D comparison receipt."""

    report_id: str
    policy_sha256: str
    evaluated_at: datetime
    event_payloads: tuple[Mapping[str, object], ...]
    summaries: tuple[Mapping[str, object], ...]
    promoted: ExpressionKind | None
    promotion_reason_codes: tuple[str, ...]
    option_history_status: str
    option_history_blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.report_id:
            raise ValueError("report_id must be non-empty text")
        if len(self.policy_sha256) != 64:
            raise ValueError("policy_sha256 must be a SHA-256 digest")
        if self.evaluated_at.tzinfo != UTC:
            raise ValueError("evaluated_at must be UTC")
        if self.promoted is ExpressionKind.CASH_NO_TRADE:
            raise ValueError("cash is the baseline and cannot be promoted")
        if self.promotion_reason_codes != tuple(sorted(set(self.promotion_reason_codes))):
            raise ValueError("promotion_reason_codes must be sorted unique text")
        if self.option_history_status not in {
            OPTION_HISTORY_AVAILABLE,
            OPTION_HISTORY_NOT_RUN,
        }:
            raise ValueError("option_history_status must be AVAILABLE or NOT_RUN")
        if self.option_history_blockers != tuple(sorted(set(self.option_history_blockers))):
            raise ValueError("option_history_blockers must be sorted unique text")


def _summarize(
    events: Sequence[TournamentEvent],
    all_economics: Mapping[str, tuple[ExpressionEconomics, ...]],
    kind: ExpressionKind,
    policy: PromotedExpressionPolicy,
) -> Mapping[str, object]:
    rows = [
        economics
        for event in events
        for economics in all_economics[event.event_id]
        if economics.expression_kind is kind
    ]
    compared = tuple(row for row in rows if row.compared)
    rejected = tuple(row for row in rows if not row.compared)
    rejection_counts: dict[str, int] = {}
    for row in rejected:
        reason = row.reason.value if row.reason is not None else "UNKNOWN"
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    total_entry_cost = sum(
        (row.entry_cost for row in compared if row.entry_cost is not None), Decimal(0)
    )
    worst_max_loss = max(
        (row.max_loss for row in compared if row.max_loss is not None), default=Decimal(0)
    )
    hits = (
        sum(_direction_hit_row(event, all_economics, kind) for event in events)
        if kind is not ExpressionKind.CASH_NO_TRADE
        else 0
    )
    edge = after_cost_edge_basis_points(tuple(rows), direction_hits=hits)
    return {
        "expression_kind": kind.value,
        "events_total": len(events),
        "events_compared": len(compared),
        "events_rejected": len(rejected),
        "rejection_counts": [
            {"reason": reason, "count": count} for reason, count in sorted(rejection_counts.items())
        ],
        "total_entry_cost": str(total_entry_cost),
        "worst_max_loss": str(worst_max_loss),
        "direction_hits": hits if kind is not ExpressionKind.CASH_NO_TRADE else None,
        "after_cost_edge_bps": None if edge is None else str(edge),
    }


def _promote(
    summaries: Mapping[ExpressionKind, Mapping[str, object]],
    policy: PromotedExpressionPolicy,
) -> tuple[ExpressionKind | None, tuple[str, ...]]:
    reasons: set[str] = set()
    qualifying: list[tuple[ExpressionKind, Decimal]] = []
    for kind in (
        ExpressionKind.DEBIT_VERTICAL,
        ExpressionKind.ONE_LONG_OPTION,
        ExpressionKind.SHARES,
    ):
        summary = summaries[kind]
        compared = int(summary["events_compared"])  # type: ignore[arg-type]
        edge_text = summary["after_cost_edge_bps"]
        if compared < policy.evidence_min_events:
            reasons.add(PROMOTION_INSUFFICIENT_EVENTS)
            continue
        if edge_text is None:
            reasons.add(PROMOTION_NO_COMPARED_EVENTS)
            continue
        edge = Decimal(str(edge_text))
        if edge < policy.evidence_threshold:
            reasons.add(PROMOTION_BELOW_THRESHOLD)
            continue
        qualifying.append((kind, edge))
    if not qualifying:
        return None, tuple(sorted(reasons))
    qualifying.sort(key=lambda item: (-item[1], item[0].value))
    return qualifying[0][0], ()


def run_gate_d_tournament(
    *,
    report_id: str,
    policy: PromotedExpressionPolicy,
    policy_sha256: str,
    events: Sequence[TournamentEvent],
    evaluated_at: datetime,
) -> GateDReport:
    """Compare every expression over identical events and emit the receipt."""

    if not events:
        raise ExpressionRejected(
            ExpressionReason.UNSUPPORTED_INPUT,
            "tournament.events",
            "the tournament requires at least one event",
        )
    if evaluated_at.tzinfo != UTC:
        raise ExpressionRejected(
            ExpressionReason.UNSUPPORTED_INPUT,
            "tournament.evaluated_at",
            "evaluation clock must be UTC",
        )
    all_economics: dict[str, tuple[ExpressionEconomics, ...]] = {}
    event_payloads: list[dict[str, object]] = []
    for event in events:
        economics = _event_economics(event, policy)
        all_economics[event.event_id] = economics
        event_payloads.append(
            {
                "event_id": event.event_id,
                "decision_direction": event.decision_direction,
                "decision_sha256": event.decision_sha256,
                "outcome_direction": event.outcome_direction,
                "snapshot_sha256": expression_market_snapshot_sha256(event.snapshot),
                "decision_timestamp": event.snapshot.observation_clock_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "exit_clock_at": event.exit_clock_at.isoformat().replace("+00:00", "Z"),
                "economics": [_economics_payload(item) for item in economics],
            }
        )
    summaries = {
        kind: _summarize(events, all_economics, kind, policy) for kind in _EXPRESSION_ORDER
    }
    option_rows = tuple(
        economics
        for economics_list in all_economics.values()
        for economics in economics_list
        if economics.expression_kind is ExpressionKind.ONE_LONG_OPTION
    )
    option_compared = tuple(row for row in option_rows if row.compared)
    if option_compared:
        option_history_status = OPTION_HISTORY_AVAILABLE
        option_history_blockers: tuple[str, ...] = ()
    else:
        option_history_status = OPTION_HISTORY_NOT_RUN
        blockers = {row.reason.value for row in option_rows if row.reason is not None}
        option_history_blockers = tuple(sorted(blockers))
    promoted, promotion_reasons = _promote(summaries, policy)
    ordered_summaries = tuple(summaries[kind] for kind in _EXPRESSION_ORDER)
    return GateDReport(
        report_id=report_id,
        policy_sha256=policy_sha256,
        evaluated_at=evaluated_at,
        event_payloads=tuple(event_payloads),
        summaries=ordered_summaries,
        promoted=promoted,
        promotion_reason_codes=promotion_reasons if promoted is None else (),
        option_history_status=option_history_status,
        option_history_blockers=option_history_blockers,
    )


def gate_d_report_payload(value: GateDReport) -> dict[str, object]:
    """Return the single versioned serialization for one Gate D report."""

    return {
        "schema": GATE_D_REPORT_SCHEMA,
        "schema_version": GATE_D_REPORT_SCHEMA_VERSION,
        "report_id": value.report_id,
        "policy_sha256": value.policy_sha256,
        "evaluated_at": value.evaluated_at.isoformat().replace("+00:00", "Z"),
        "claims": list(GATE_D_CLAIMS),
        "paper_mutation_blocked": True,
        "option_history_status": value.option_history_status,
        "option_history_blockers": list(value.option_history_blockers),
        "events": [dict(payload) for payload in value.event_payloads],
        "summaries": [dict(summary) for summary in value.summaries],
        "promoted": None if value.promoted is None else value.promoted.value,
        "promotion_reason_codes": list(value.promotion_reason_codes)
        if value.promoted is None
        else [],
        "no_expression": value.promoted is None,
        "promotion_label": NO_EXPRESSION if value.promoted is None else value.promoted.value,
    }


def gate_d_report_bytes(value: GateDReport) -> bytes:
    """Serialize one Gate D report to deterministic canonical bytes."""

    return canonical_json_bytes(gate_d_report_payload(value))


def gate_d_report_sha256(value: GateDReport) -> str:
    return sha256_bytes(gate_d_report_bytes(value))
