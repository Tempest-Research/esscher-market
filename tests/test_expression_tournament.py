"""Contract tests for the Gate D expression tournament and compiler.

Every observation is synthetic and deterministic; no live capture occurs.
Tests cover geometry, quote freshness/skew, spreads, size, ties, symbols,
Decimal arithmetic, and every NO_PACKAGE rejection path.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ringdown_market.execution.expression import (
    EXECUTABLE_DATA,
    INDICATIVE_DATA,
    NO_PACKAGE,
    BorrowLocateEvidence,
    ExpressionKind,
    ExpressionMarketSnapshot,
    ExpressionReason,
    ExpressionRejected,
    FeedIdentity,
    OptionContractObservation,
    PackageObservation,
    PromotedExpressionPolicy,
    ShareObservation,
    TournamentEvent,
    TwoSidedQuote,
    compile_expression,
    compile_or_no_package,
    compiled_expression_bytes,
    expression_market_snapshot_sha256,
    gate_d_report_bytes,
    parse_promoted_expression_policy,
    promoted_expression_policy_bytes,
    promoted_expression_policy_sha256,
    run_gate_d_tournament,
)
from ringdown_market.execution.expression.compiler import no_package_payload
from ringdown_market.execution.models import VerticalType
from ringdown_market.strategy.contracts import sha256_bytes, strategy_decision_bytes
from ringdown_market.strategy.models import (
    DecisionDisposition,
    Direction,
    ReactionRelation,
    StrategyDecision,
)

CLOCK = datetime(2026, 9, 11, 13, 36, 0, tzinfo=UTC)
DECISION_AT = datetime(2026, 9, 11, 13, 35, 50, tzinfo=UTC)
EXPIRY = date(2026, 9, 18)
_H = sha256_bytes(b"test-hash-seed")

EQUITY_FEED = FeedIdentity(
    "SYNTHETIC_SIP_EQUITY_FEED", "read_only_equity_quote", "equity_quote.v1", "1"
)
OPTION_FEED = FeedIdentity(
    "SYNTHETIC_OPTION_SNAPSHOT_FEED", "read_only_option_chain", "option_chain_snapshot.v1", "1"
)
PACKAGE_FEED = FeedIdentity(
    "SYNTHETIC_PACKAGE_FEED", "read_only_package_quote", "package_quote.v1", "1"
)
UNPINNED_FEED = FeedIdentity("ROGUE_FEED", "unknown_tool", "unknown_schema", "9")


def _quote(bid, ask, *, size=100, at=None) -> TwoSidedQuote:
    return TwoSidedQuote(
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_size=size,
        ask_size=size,
        observed_at=at or (CLOCK - timedelta(seconds=2)),
    )


def _share(*, feed=EQUITY_FEED, data_class=EXECUTABLE_DATA, quote=None) -> ShareObservation:
    return ShareObservation(
        symbol="KR",
        quote=quote or _quote("61.40", "61.44"),
        feed=feed,
        data_class=data_class,
    )


def _contract(
    occ: str,
    option_type: str,
    strike,
    *,
    delta,
    open_interest=200,
    bid="0.80",
    ask="0.84",
    feed=OPTION_FEED,
    data_class=EXECUTABLE_DATA,
    quote=None,
    expiry=EXPIRY,
) -> OptionContractObservation:
    return OptionContractObservation(
        symbol=occ,
        underlying="KR",
        expiry=expiry,
        option_type=option_type,
        strike=Decimal(strike),
        quote=quote or _quote(bid, ask),
        feed=feed,
        data_class=data_class,
        open_interest=open_interest,
        reported_delta=Decimal(delta) if delta is not None else None,
    )


def _up_chain() -> tuple[OptionContractObservation, ...]:
    return (
        _contract("KR260918C00061000", "CALL", "61", delta="0.45"),
        _contract("KR260918C00062000", "CALL", "62", delta="0.30", bid="0.45", ask="0.48"),
        _contract("KR260918C00063500", "CALL", "63.5", delta="0.15", bid="0.20", ask="0.22"),
    )


def _down_chain() -> tuple[OptionContractObservation, ...]:
    return (
        _contract("KR260918P00060000", "PUT", "60", delta="-0.30", bid="0.40", ask="0.43"),
        _contract("KR260918P00061000", "PUT", "61", delta="-0.45", bid="0.75", ask="0.79"),
    )


def _package(
    long_occ: str, short_occ: str, *, net_bid="0.32", net_ask="0.36", at=None
) -> PackageObservation:
    return PackageObservation(
        package_id=f"{long_occ}+{short_occ}",
        legs=(long_occ, short_occ),
        net_bid=Decimal(net_bid),
        net_ask=Decimal(net_ask),
        size=10,
        observed_at=at or (CLOCK - timedelta(seconds=2)),
        feed=PACKAGE_FEED,
        data_class=EXECUTABLE_DATA,
    )


def _borrow() -> BorrowLocateEvidence:
    return BorrowLocateEvidence(
        symbol="KR",
        located_quantity=100,
        source="SYNTHETIC_LOCATE_FEED",
        observed_at=CLOCK - timedelta(minutes=5),
        content_sha256=_H,
    )


def _snapshot(
    *,
    chain=None,
    packages=(),
    share=None,
    borrow=None,
    clock=CLOCK,
    decision_sha256=None,
) -> ExpressionMarketSnapshot:
    return ExpressionMarketSnapshot(
        snapshot_id="snapshot-test",
        underlying="KR",
        observation_clock_at=clock,
        decision_sha256=decision_sha256 or _H,
        share=share or _share(),
        chain=tuple(chain if chain is not None else _up_chain()),
        packages=tuple(packages),
        borrow_locate=borrow,
    )


def _decision(
    direction: Direction = Direction.UP, *, disposition=DecisionDisposition.ACCEPTED
) -> StrategyDecision:
    accepted = disposition is DecisionDisposition.ACCEPTED
    effective_direction = direction if accepted else Direction.UNCERTAIN
    if disposition is DecisionDisposition.ABSTAINED:
        reasoner_direction: Direction | None = Direction.UNCERTAIN
        reaction_relation = ReactionRelation.NOT_APPLICABLE
    elif accepted:
        reasoner_direction = direction
        reaction_relation = ReactionRelation.CONTINUE
    else:
        reasoner_direction = None
        reaction_relation = ReactionRelation.NONE
    return StrategyDecision(
        event_id="KR-2026Q2-EARNINGS",
        security_id="CIK-0000056873",
        candidate_id="EARNINGS_RESIDUAL_CONTINUATION_V1",
        cohort_id="BMO",
        policy_sha256=_H,
        candidate_manifest_sha256=_H,
        strategy_snapshot_sha256=_H,
        feature_receipt_sha256=_H,
        reasoner_exchange_sha256=_H,
        reasoner_decision_sha256=_H,
        producer_build_sha256=_H,
        decision_at=DECISION_AT,
        reasoner_direction=reasoner_direction,
        direction=effective_direction,
        disposition=disposition,
        reaction_relation=reaction_relation,
        evidence_ids=("earnings-release",),
        contradictions=(),
        unknowns=(),
        strongest_falsifier=None,
        reason_codes=() if accepted else ("PREFLIGHT_INELIGIBLE",),
        summary=None,
    )


def _policy(
    kind: ExpressionKind = ExpressionKind.DEBIT_VERTICAL,
    *,
    gate_d_report_sha256=None,
    evidence_threshold="0",
    evidence_min_events=1,
    quote_max_age_ms=5000,
    cross_leg_skew_max_ms=1000,
    spread_max_bps="500",
    min_quote_size=1,
    min_dte=7,
    max_dte=21,
    delta_min="0.20",
    delta_max="0.60",
    width_min="0.5",
    width_max="10",
    liquidity_min_open_interest=50,
    operational_loss_budget="500",
) -> PromotedExpressionPolicy:
    return PromotedExpressionPolicy(
        policy_id="PROMOTED_EXPRESSION_POLICY_V1",
        version="v1",
        gate_d_report_sha256=gate_d_report_sha256 or _H,
        expression_kind=kind,
        objective="AFTER_COST_EXPECTED_EDGE_VS_CASH",
        evidence_threshold=Decimal(evidence_threshold),
        evidence_min_events=evidence_min_events,
        operational_loss_budget=Decimal(operational_loss_budget),
        quote_max_age_ms=quote_max_age_ms,
        cross_leg_skew_max_ms=cross_leg_skew_max_ms,
        spread_max_bps=Decimal(spread_max_bps),
        min_quote_size=min_quote_size,
        min_dte=min_dte,
        max_dte=max_dte,
        delta_min=Decimal(delta_min),
        delta_max=Decimal(delta_max),
        width_min=Decimal(width_min),
        width_max=Decimal(width_max),
        liquidity_min_open_interest=liquidity_min_open_interest,
    )


def _compile_kwargs(direction=Direction.UP, *, kind=ExpressionKind.DEBIT_VERTICAL, **overrides):
    decision = _decision(direction)
    decision_bytes = strategy_decision_bytes(decision)
    snapshot = _snapshot(
        chain=_up_chain() if direction is Direction.UP else _down_chain(),
        packages=(
            [_package("KR260918C00061000", "KR260918C00062000")]
            if direction is Direction.UP
            else [_package("KR260918P00061000", "KR260918P00060000")]
        ),
        borrow=_borrow(),
        decision_sha256=sha256_bytes(decision_bytes),
    )
    policy = _policy(kind)
    kwargs = {
        "decision": decision,
        "decision_bytes": decision_bytes,
        "snapshot": snapshot,
        "policy": policy,
        "policy_sha256": promoted_expression_policy_sha256(policy),
        "gate_d_report_sha256": policy.gate_d_report_sha256,
        "compiled_at": CLOCK,
    }
    kwargs.update(overrides)
    return kwargs


def _rejects_with(reason: ExpressionReason, **overrides) -> ExpressionReason:
    status, result = compile_or_no_package(**_compile_kwargs(**overrides))
    assert status == NO_PACKAGE
    assert result is reason
    return result


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_identical_compiled_expression_bytes() -> None:
    first = compile_expression(**_compile_kwargs())
    second = compile_expression(**_compile_kwargs())
    assert compiled_expression_bytes(first) == compiled_expression_bytes(second)


def test_identical_events_produce_identical_gate_d_report_bytes() -> None:
    policy = _policy()
    event = TournamentEvent(
        event_id="KR-2026Q2-EARNINGS",
        decision_direction="UP",
        decision_sha256=_H,
        outcome_direction="UP",
        snapshot=_snapshot(packages=[_package("KR260918C00061000", "KR260918C00062000")]),
    )
    first = run_gate_d_tournament(
        report_id="gate-d-test",
        policy=policy,
        policy_sha256=promoted_expression_policy_sha256(policy),
        events=[event],
        evaluated_at=CLOCK,
    )
    second = run_gate_d_tournament(
        report_id="gate-d-test",
        policy=policy,
        policy_sha256=promoted_expression_policy_sha256(policy),
        events=[event],
        evaluated_at=CLOCK,
    )
    assert gate_d_report_bytes(first) == gate_d_report_bytes(second)


# ---------------------------------------------------------------------------
# Tournament comparison contract
# ---------------------------------------------------------------------------


def test_tournament_compares_all_four_expressions_with_failures_in_denominator() -> None:
    policy = _policy()
    healthy = TournamentEvent(
        event_id="KR-2026Q2-EARNINGS",
        decision_direction="UP",
        decision_sha256=_H,
        outcome_direction="UP",
        snapshot=_snapshot(packages=[_package("KR260918C00061000", "KR260918C00062000")]),
    )
    broken = TournamentEvent(
        event_id="KR-2026Q3-EARNINGS",
        decision_direction="UP",
        decision_sha256=_H,
        outcome_direction="DOWN",
        snapshot=_snapshot(chain=(), packages=()),
    )
    report = run_gate_d_tournament(
        report_id="gate-d-test",
        policy=policy,
        policy_sha256=promoted_expression_policy_sha256(policy),
        events=[healthy, broken],
        evaluated_at=CLOCK,
    )
    summaries = {summary["expression_kind"]: summary for summary in report.summaries}
    for kind in ("CASH_NO_TRADE", "SHARES", "ONE_LONG_OPTION", "DEBIT_VERTICAL"):
        summary = summaries[kind]
        assert summary["events_total"] == 2
        assert summary["events_compared"] + summary["events_rejected"] == 2
    assert summaries["CASH_NO_TRADE"]["events_compared"] == 2
    assert summaries["ONE_LONG_OPTION"]["events_rejected"] == 1
    assert summaries["DEBIT_VERTICAL"]["events_rejected"] == 1
    assert report.promoted is not None
    assert report.promotion_reason_codes == ()


def test_tournament_emits_no_expression_below_threshold() -> None:
    policy = _policy(evidence_threshold="9999")
    event = TournamentEvent(
        event_id="KR-2026Q2-EARNINGS",
        decision_direction="UP",
        decision_sha256=_H,
        outcome_direction="UP",
        snapshot=_snapshot(packages=[_package("KR260918C00061000", "KR260918C00062000")]),
    )
    report = run_gate_d_tournament(
        report_id="gate-d-test",
        policy=policy,
        policy_sha256=promoted_expression_policy_sha256(policy),
        events=[event],
        evaluated_at=CLOCK,
    )
    assert report.promoted is None
    assert report.promotion_reason_codes == ("BELOW_EVIDENCE_THRESHOLD",)
    assert gate_d_report_bytes(report)
    assert report.event_payloads[0]["snapshot_sha256"] == expression_market_snapshot_sha256(
        event.snapshot
    )


def test_tournament_requires_enough_evidence_events() -> None:
    policy = _policy(evidence_min_events=5, evidence_threshold="0")
    event = TournamentEvent(
        event_id="KR-2026Q2-EARNINGS",
        decision_direction="UP",
        decision_sha256=_H,
        outcome_direction="UP",
        snapshot=_snapshot(packages=[_package("KR260918C00061000", "KR260918C00062000")]),
    )
    report = run_gate_d_tournament(
        report_id="gate-d-test",
        policy=policy,
        policy_sha256=promoted_expression_policy_sha256(policy),
        events=[event],
        evaluated_at=CLOCK,
    )
    assert report.promoted is None
    assert "INSUFFICIENT_EVENTS" in report.promotion_reason_codes


# ---------------------------------------------------------------------------
# Compiler happy paths
# ---------------------------------------------------------------------------


def test_compile_debit_vertical_up_emits_bull_call_permit_boundary_fields() -> None:
    compiled = compile_expression(**_compile_kwargs())
    assert compiled.expression_kind is ExpressionKind.DEBIT_VERTICAL
    block = compiled.debit_vertical
    assert block is not None
    assert block["vertical_type"] == VerticalType.BULL_CALL.value
    assert block["quantity"] == 1
    long_strike = Decimal(block["long_leg"]["strike"])
    short_strike = Decimal(block["short_leg"]["strike"])
    assert long_strike < short_strike
    assert Decimal(block["limit_price"]) < Decimal(block["width"])
    assert block["long_leg"]["symbol"] == "KR260918C00061000"
    assert block["short_leg"]["symbol"] == "KR260918C00062000"


def test_compile_debit_vertical_down_emits_bear_put_geometry() -> None:
    compiled = compile_expression(**_compile_kwargs(direction=Direction.DOWN))
    block = compiled.debit_vertical
    assert block is not None
    assert block["vertical_type"] == VerticalType.BEAR_PUT.value
    assert Decimal(block["long_leg"]["strike"]) > Decimal(block["short_leg"]["strike"])


def test_compile_long_option_up_emits_call_with_premium_at_risk() -> None:
    compiled = compile_expression(**_compile_kwargs(kind=ExpressionKind.ONE_LONG_OPTION))
    block = compiled.long_option
    assert block is not None
    assert block["option_type"] == "CALL"
    assert block["side"] == "BUY"
    assert block["quantity"] == 1
    assert Decimal(block["premium_at_risk"]) == Decimal("0.84") * 100
    assert block["dte"] == 7


def test_compile_shares_up_buys_at_ask() -> None:
    compiled = compile_expression(**_compile_kwargs(kind=ExpressionKind.SHARES))
    block = compiled.shares
    assert block is not None
    assert block["side"] == "BUY"
    assert block["price_rule"] == "ASK"
    assert Decimal(block["exposure"]) == Decimal("61.44")


def test_compile_shares_down_requires_borrow_locate() -> None:
    compiled = compile_expression(
        **_compile_kwargs(direction=Direction.DOWN, kind=ExpressionKind.SHARES)
    )
    block = compiled.shares
    assert block is not None
    assert block["side"] == "SELL_SHORT"
    assert block["borrow_locate_sha256"] == _H


def test_compile_cash_emits_no_position_blocks() -> None:
    compiled = compile_expression(**_compile_kwargs(kind=ExpressionKind.CASH_NO_TRADE))
    assert compiled.shares is None
    assert compiled.long_option is None
    assert compiled.debit_vertical is None


# ---------------------------------------------------------------------------
# NO_PACKAGE paths
# ---------------------------------------------------------------------------


def test_uncertain_decision_never_reaches_compilation() -> None:
    decision = _decision(Direction.UNCERTAIN, disposition=DecisionDisposition.ABSTAINED)
    kwargs = _compile_kwargs()
    kwargs["decision"] = decision
    kwargs["decision_bytes"] = strategy_decision_bytes(decision)
    kwargs["snapshot"] = _snapshot(
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    with pytest.raises(ExpressionRejected) as caught:
        compile_expression(**kwargs)
    assert caught.value.reason is ExpressionReason.DIRECTION_NOT_VALIDATED


def test_rejected_disposition_never_reaches_compilation() -> None:
    decision = _decision(Direction.UP, disposition=DecisionDisposition.REJECTED)
    kwargs = _compile_kwargs()
    kwargs["decision"] = decision
    kwargs["decision_bytes"] = strategy_decision_bytes(decision)
    kwargs["snapshot"] = _snapshot(
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert status == NO_PACKAGE
    assert result is ExpressionReason.DIRECTION_NOT_VALIDATED


def test_decision_binding_mismatch_fails_closed() -> None:
    kwargs = _compile_kwargs()
    kwargs["snapshot"] = _snapshot(
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256="0" * 64,
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.DECISION_BINDING_MISMATCH)


def test_snapshot_predating_decision_fails_closed() -> None:
    early_clock = DECISION_AT - timedelta(minutes=1)
    early_quote_at = early_clock - timedelta(seconds=2)
    kwargs = _compile_kwargs()
    kwargs["snapshot"] = _snapshot(
        share=_share(quote=_quote("61.40", "61.44", at=early_quote_at)),
        chain=(
            _contract(
                "KR260918C00061000",
                "CALL",
                "61",
                delta="0.45",
                quote=_quote("0.80", "0.84", at=early_quote_at),
            ),
            _contract(
                "KR260918C00062000",
                "CALL",
                "62",
                delta="0.30",
                bid="0.45",
                ask="0.48",
                quote=_quote("0.45", "0.48", at=early_quote_at),
            ),
        ),
        packages=[
            _package(
                "KR260918C00061000",
                "KR260918C00062000",
                at=early_quote_at,
            )
        ],
        clock=early_clock,
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.TIME_INCONSISTENT)


def test_gate_d_receipt_mismatch_fails_closed() -> None:
    kwargs = _compile_kwargs()
    kwargs["gate_d_report_sha256"] = "1" * 64
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.GATE_D_RECEIPT_MISMATCH)


def test_unknown_feed_fails_closed() -> None:
    kwargs = _compile_kwargs(kind=ExpressionKind.SHARES)
    kwargs["snapshot"] = _snapshot(
        share=_share(feed=UNPINNED_FEED),
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.UNKNOWN_FEED)


def test_indicative_quotes_never_become_executable() -> None:
    kwargs = _compile_kwargs(kind=ExpressionKind.SHARES)
    kwargs["snapshot"] = _snapshot(
        share=_share(data_class=INDICATIVE_DATA),
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.INDICATIVE_ONLY)


def test_stale_quote_fails_closed() -> None:
    stale_quote = _quote("61.40", "61.44", at=CLOCK - timedelta(seconds=60))
    kwargs = _compile_kwargs(kind=ExpressionKind.SHARES)
    kwargs["snapshot"] = _snapshot(
        share=_share(quote=stale_quote),
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.STALE_QUOTE)


def test_crossed_quote_fails_closed() -> None:
    crossed = _quote("61.50", "61.40")
    kwargs = _compile_kwargs(kind=ExpressionKind.SHARES)
    kwargs["snapshot"] = _snapshot(
        share=_share(quote=crossed),
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.CROSSED_QUOTE)


def test_insufficient_size_fails_closed() -> None:
    kwargs = _compile_kwargs(kind=ExpressionKind.SHARES)
    kwargs["policy"] = _policy(ExpressionKind.SHARES, min_quote_size=500)
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.INSUFFICIENT_SIZE)


def test_spread_too_wide_fails_closed() -> None:
    wide = _quote("61.00", "61.90")
    kwargs = _compile_kwargs(kind=ExpressionKind.SHARES)
    kwargs["policy"] = _policy(ExpressionKind.SHARES, spread_max_bps="10")
    kwargs["snapshot"] = _snapshot(
        share=_share(quote=wide),
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.SPREAD_TOO_WIDE)


def test_asynchronous_leg_quotes_fail_closed() -> None:
    long_contract = _contract(
        "KR260918C00061000",
        "CALL",
        "61",
        delta="0.45",
        quote=_quote("0.80", "0.84", at=CLOCK - timedelta(seconds=1)),
    )
    short_contract = _contract(
        "KR260918C00062000",
        "CALL",
        "62",
        delta="0.30",
        bid="0.45",
        ask="0.48",
        quote=_quote("0.45", "0.48", at=CLOCK - timedelta(seconds=3)),
    )
    kwargs = _compile_kwargs()
    kwargs["policy"] = _policy(ExpressionKind.DEBIT_VERTICAL, cross_leg_skew_max_ms=1000)
    kwargs["snapshot"] = _snapshot(
        chain=(long_contract, short_contract),
        packages=[_package("KR260918C00061000", "KR260918C00062000")],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.ASYNCHRONOUS_QUOTES)


def test_no_eligible_contract_fails_closed() -> None:
    out_of_window = (_contract("KR260918C00061000", "CALL", "61", delta="0.90"),)
    kwargs = _compile_kwargs(kind=ExpressionKind.ONE_LONG_OPTION)
    kwargs["snapshot"] = _snapshot(
        chain=out_of_window,
        packages=(),
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.UNSUPPORTED_CONTRACT)


def test_zero_dte_fails_closed() -> None:
    same_day = _contract("KR260911C00061000", "CALL", "61", delta="0.45", expiry=date(2026, 9, 11))
    kwargs = _compile_kwargs(kind=ExpressionKind.ONE_LONG_OPTION)
    kwargs["policy"] = _policy(ExpressionKind.ONE_LONG_OPTION, min_dte=0, max_dte=21)
    kwargs["snapshot"] = _snapshot(
        chain=(same_day,),
        packages=(),
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.LIFECYCLE_CHECK_FAILED)


def test_width_out_of_bounds_fails_closed() -> None:
    kwargs = _compile_kwargs()
    kwargs["policy"] = _policy(ExpressionKind.DEBIT_VERTICAL, width_min="5", width_max="10")
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.WIDTH_OUT_OF_BOUNDS)


def test_missing_package_fails_closed() -> None:
    kwargs = _compile_kwargs()
    kwargs["snapshot"] = _snapshot(
        chain=_up_chain(),
        packages=(),
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.PACKAGE_UNAVAILABLE)


def test_debit_not_below_width_fails_closed() -> None:
    expensive = _package("KR260918C00061000", "KR260918C00062000", net_bid="1.05", net_ask="1.10")
    kwargs = _compile_kwargs()
    kwargs["snapshot"] = _snapshot(
        chain=_up_chain(),
        packages=[expensive],
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.DEBIT_NOT_BELOW_WIDTH)


def test_missing_borrow_locate_blocks_short_shares() -> None:
    kwargs = _compile_kwargs(direction=Direction.DOWN, kind=ExpressionKind.SHARES)
    kwargs["snapshot"] = _snapshot(
        chain=_down_chain(),
        packages=[_package("KR260918P00061000", "KR260918P00060000")],
        borrow=None,
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.BORROW_LOCATE_MISSING)


def test_exposure_budget_exceeded_fails_closed() -> None:
    kwargs = _compile_kwargs(kind=ExpressionKind.SHARES)
    kwargs["policy"] = _policy(ExpressionKind.SHARES, operational_loss_budget="10")
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.EXPOSURE_BUDGET_EXCEEDED)


def test_no_package_payload_is_canonical_and_labeled() -> None:
    payload = no_package_payload(
        reason=ExpressionReason.NO_QUOTE,
        event_id="KR-2026Q2-EARNINGS",
        decision_sha256=_H,
        snapshot_sha256=_H,
        policy_sha256=_H,
        gate_d_report_sha256=_H,
        compiled_at=CLOCK,
    )
    assert payload["no_package"] is True
    assert payload["expression_kind"] == NO_PACKAGE
    assert payload["no_package_reason"] == "NO_QUOTE"


# ---------------------------------------------------------------------------
# Geometry, ties, symbols, Decimal arithmetic
# ---------------------------------------------------------------------------


def test_selection_tie_breaks_deterministically_on_strike_then_symbol() -> None:
    twin_a = _contract("KR260918C00061000", "CALL", "61", delta="0.45", bid="0.80", ask="0.84")
    twin_b = _contract("KR260918C00062000", "CALL", "62", delta="0.44", bid="0.80", ask="0.84")
    snapshot = _snapshot(chain=(twin_a, twin_b))
    from ringdown_market.execution.expression.geometry import select_long_contract
    from ringdown_market.execution.models import OptionType

    selected = select_long_contract(
        snapshot,
        _policy(ExpressionKind.ONE_LONG_OPTION),
        option_type=OptionType.CALL,
        asof=CLOCK.date(),
    )
    assert selected.symbol == "KR260918C00061000"


def test_decimal_premium_and_max_loss_are_exact() -> None:
    compiled = compile_expression(**_compile_kwargs())
    block = compiled.debit_vertical
    assert block is not None
    assert Decimal(block["limit_price"]) == Decimal("0.36")
    assert Decimal(block["width"]) == Decimal("1")
    assert Decimal(block["maximum_loss"]) == Decimal("36")
    assert isinstance(Decimal(block["limit_price"]), Decimal)


def test_occ_symbol_mismatch_fails_closed() -> None:
    bad_symbol = _contract("KR260918C00099000", "CALL", "61", delta="0.45")
    kwargs = _compile_kwargs(kind=ExpressionKind.ONE_LONG_OPTION)
    kwargs["snapshot"] = _snapshot(
        chain=(bad_symbol,),
        packages=(),
        decision_sha256=sha256_bytes(kwargs["decision_bytes"]),
    )
    status, result = compile_or_no_package(**kwargs)
    assert (status, result) == (NO_PACKAGE, ExpressionReason.UNSUPPORTED_CONTRACT)


def test_snapshot_rejects_observation_after_clock() -> None:
    late_quote = _quote("61.40", "61.44", at=CLOCK + timedelta(seconds=5))
    with pytest.raises(ValueError):
        _snapshot(share=_share(quote=late_quote))


def test_snapshot_rejects_unsorted_or_duplicate_chain() -> None:
    duplicate = _contract("KR260918C00061000", "CALL", "61", delta="0.45")
    with pytest.raises(ValueError):
        _snapshot(chain=(duplicate, duplicate))


# ---------------------------------------------------------------------------
# Policy serialization contract
# ---------------------------------------------------------------------------


def test_policy_round_trip_and_tamper_rejection() -> None:
    policy = _policy()
    raw = promoted_expression_policy_bytes(policy)
    assert parse_promoted_expression_policy(raw).policy_id == policy.policy_id
    tampered = raw.replace(b"DEBIT_VERTICAL", b"SHARES__________")
    with pytest.raises(ExpressionRejected):
        parse_promoted_expression_policy(tampered)


def test_policy_rejects_duplicate_fields() -> None:
    policy = _policy()
    raw = promoted_expression_policy_bytes(policy)
    text = raw.decode("utf-8")
    duplicated = text.replace('"version":"v1"', '"version":"v1","version":"v1"', 1)
    with pytest.raises(ExpressionRejected):
        parse_promoted_expression_policy(duplicated.encode("utf-8"))
