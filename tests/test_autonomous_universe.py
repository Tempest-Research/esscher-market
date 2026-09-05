"""RED-to-GREEN contract tests for the liquid-universe scan and allocator.

The scan filter and the allocator under test are pure deterministic contracts:
they never call a data provider, reasoner, account, broker, or order API.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from esscher.autonomy.universe import (
    AbstainReason,
    AllocationReservation,
    AllocationStatus,
    DefinedRiskOpportunity,
    PortfolioState,
    ProductKind,
    Readiness,
    ReadinessReason,
    RiskTier,
    ScanExclusionReason,
    ScanRejection,
    UnderlyingExposure,
    UniverseContractRejected,
    UniverseLane,
    UniverseObservation,
    allocate_defined_risk,
    defined_risk_opportunity_bytes,
    defined_risk_opportunity_sha256,
    parse_defined_risk_opportunity,
    scan_universe,
    universe_scan_bytes,
    universe_scan_sha256,
)

AS_OF = datetime(2026, 8, 28, 19, 36, tzinfo=UTC)
QUOTED = datetime(2026, 8, 28, 19, 35, tzinfo=UTC)
OBSERVED = datetime(2026, 8, 28, 19, 35, 30, tzinfo=UTC)


def _observation(**overrides: object) -> UniverseObservation:
    values: dict[str, object] = {
        "symbol": "INTC",
        "product_kind": ProductKind.US_COMMON_STOCK,
        "lane": UniverseLane.CATALYST_STOCK,
        "active": True,
        "tradable": True,
        "last": Decimal("23.50"),
        "bid": Decimal("23.49"),
        "ask": Decimal("23.51"),
        "quoted_at": QUOTED,
        "observed_at": OBSERVED,
        "option_contracts_active": 250,
        "option_page_complete": True,
        "news_records": 2,
        "news_page_complete": True,
        "iv_available": True,
        "greeks_available": True,
        "activity_rank": 3,
        "absolute_movement": Decimal("0.031"),
    }
    values.update(overrides)
    return UniverseObservation(**values)  # type: ignore[arg-type]


def _portfolio(**overrides: object) -> PortfolioState:
    values: dict[str, object] = {
        "equity": Decimal("100000"),
        "cash": Decimal("100000"),
        "open_debit": Decimal("0"),
        "exposures": (),
    }
    values.update(overrides)
    return PortfolioState(**values)  # type: ignore[arg-type]


def _opportunity(**overrides: object) -> DefinedRiskOpportunity:
    values: dict[str, object] = {
        "opportunity_id": "OPP-INTC-20260828-001",
        "decision_id": "DEC-INTC-20260828-001",
        "expression_id": "EXP-INTC-20260828-001",
        "underlying": "INTC",
        "risk_tier": RiskTier.FIVE_PERCENT,
        "max_debit_per_contract": Decimal("250"),
        "decision_ready": True,
    }
    values.update(overrides)
    return DefinedRiskOpportunity(**values)  # type: ignore[arg-type]


def _fixture_observations() -> tuple[UniverseObservation, ...]:
    return (
        _observation(
            symbol="SPY",
            product_kind=ProductKind.US_EXCHANGE_TRADED_PRODUCT,
            lane=UniverseLane.MARKET_ANCHOR,
            last=Decimal("559.40"),
            bid=Decimal("559.38"),
            ask=Decimal("559.42"),
            quoted_at=datetime(2026, 8, 28, 19, 35, tzinfo=UTC),
            option_contracts_active=5000,
            option_page_complete=False,
            news_records=0,
            news_page_complete=False,
            iv_available=None,
            greeks_available=None,
            activity_rank=1,
            absolute_movement=Decimal("0.004"),
        ),
        _observation(
            symbol="QQQ",
            product_kind=ProductKind.US_EXCHANGE_TRADED_PRODUCT,
            lane=UniverseLane.MARKET_ANCHOR,
            last=Decimal("479.10"),
            bid=Decimal("479.08"),
            ask=Decimal("479.12"),
            quoted_at=datetime(2026, 8, 28, 19, 35, 10, tzinfo=UTC),
            option_contracts_active=4200,
            option_page_complete=False,
            news_records=0,
            news_page_complete=False,
            iv_available=None,
            greeks_available=None,
            activity_rank=2,
            absolute_movement=Decimal("0.006"),
        ),
        _observation(
            symbol="INTC",
            quoted_at=datetime(2026, 8, 28, 19, 35, 5, tzinfo=UTC),
            option_contracts_active=250,
            option_page_complete=False,
            news_records=1,
            news_page_complete=False,
            iv_available=None,
            greeks_available=None,
            activity_rank=3,
        ),
        _observation(
            symbol="NVDA",
            last=Decimal("121.30"),
            bid=Decimal("121.28"),
            ask=Decimal("121.32"),
            quoted_at=datetime(2026, 8, 28, 19, 35, 5, tzinfo=UTC),
            option_contracts_active=1800,
            option_page_complete=False,
            news_records=1,
            news_page_complete=False,
            iv_available=None,
            greeks_available=None,
            activity_rank=4,
            absolute_movement=Decimal("0.028"),
        ),
        _observation(
            symbol="NIO",
            last=Decimal("4.20"),
            bid=Decimal("4.19"),
            ask=Decimal("4.21"),
            quoted_at=datetime(2026, 8, 28, 19, 35, 5, tzinfo=UTC),
            option_contracts_active=900,
            option_page_complete=True,
            news_records=3,
            news_page_complete=True,
            activity_rank=5,
            absolute_movement=Decimal("0.052"),
        ),
    )


# ---------------------------------------------------------------------------
# 1. Module API and one valid scan candidate
# ---------------------------------------------------------------------------


def test_single_valid_catalyst_is_decision_ready() -> None:
    result = scan_universe((_observation(),), as_of=AS_OF)

    assert result.rejections == ()
    assert [candidate.symbol for candidate in result.candidates] == ["INTC"]
    candidate = result.candidates[0]
    assert candidate.readiness is Readiness.DECISION_READY
    assert candidate.readiness_reasons == ()
    assert candidate.symbol == "INTC"
    assert candidate.lane is UniverseLane.CATALYST_STOCK


# ---------------------------------------------------------------------------
# 2. Product, price, active, tradable, quote, spread, and option exclusions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    (
        ProductKind.OTC,
        ProductKind.WARRANT,
        ProductKind.RIGHT,
        ProductKind.UNIT,
        ProductKind.LEVERAGED_INVERSE_ETP,
        ProductKind.NON_US_PRODUCT,
    ),
)
def test_excluded_product_kinds_are_rejected(kind: ProductKind) -> None:
    result = scan_universe((_observation(product_kind=kind),), as_of=AS_OF)

    assert result.candidates == ()
    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.EXCLUDED_PRODUCT_KIND,)),
    )


def test_inactive_product_is_rejected() -> None:
    result = scan_universe((_observation(active=False),), as_of=AS_OF)

    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.INACTIVE_PRODUCT,)),
    )


def test_untradable_product_is_rejected() -> None:
    result = scan_universe((_observation(tradable=False),), as_of=AS_OF)

    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.UNTRADABLE_PRODUCT,)),
    )


def test_inactive_and_untradable_reasons_are_collected_in_order() -> None:
    result = scan_universe((_observation(active=False, tradable=False),), as_of=AS_OF)

    assert result.rejections == (
        ScanRejection(
            symbol="INTC",
            reasons=(
                ScanExclusionReason.INACTIVE_PRODUCT,
                ScanExclusionReason.UNTRADABLE_PRODUCT,
            ),
        ),
    )


def test_underlying_price_below_five_dollars_is_rejected() -> None:
    result = scan_universe((_observation(last=Decimal("4.99")),), as_of=AS_OF)

    assert result.candidates == ()
    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.PRICE_BELOW_FLOOR,)),
    )


def test_underlying_price_at_five_dollars_is_allowed() -> None:
    result = scan_universe((_observation(last=Decimal("5")),), as_of=AS_OF)

    assert [candidate.symbol for candidate in result.candidates] == ["INTC"]


@pytest.mark.parametrize(
    "overrides",
    (
        {"last": None},
        {"bid": None},
        {"ask": None},
        {"quoted_at": None},
    ),
)
def test_missing_or_timed_out_quotes_are_rejected(overrides: dict[str, object]) -> None:
    result = scan_universe((_observation(**overrides),), as_of=AS_OF)

    assert result.candidates == ()
    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.QUOTE_MISSING,)),
    )


def test_stale_quote_is_rejected() -> None:
    stale = AS_OF - timedelta(minutes=16)
    result = scan_universe((_observation(quoted_at=stale),), as_of=AS_OF)

    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.QUOTE_STALE,)),
    )


def test_quote_at_exactly_max_age_is_allowed() -> None:
    boundary = AS_OF - timedelta(minutes=15)
    result = scan_universe((_observation(quoted_at=boundary),), as_of=AS_OF)

    assert [candidate.symbol for candidate in result.candidates] == ["INTC"]


def test_future_dated_quote_is_rejected_as_clock_skew() -> None:
    future = AS_OF + timedelta(seconds=1)
    result = scan_universe((_observation(quoted_at=future),), as_of=AS_OF)

    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.QUOTE_CLOCK_SKEW,)),
    )


def test_crossed_quote_is_rejected() -> None:
    result = scan_universe(
        (_observation(bid=Decimal("23.55"), ask=Decimal("23.50")),),
        as_of=AS_OF,
    )

    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.QUOTE_CROSSED,)),
    )


@pytest.mark.parametrize(
    ("bid", "ask"),
    ((Decimal("0"), Decimal("23.51")), (Decimal("23.49"), Decimal("0"))),
)
def test_one_sided_quote_is_rejected(bid: Decimal, ask: Decimal) -> None:
    result = scan_universe((_observation(bid=bid, ask=ask),), as_of=AS_OF)

    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.QUOTE_ONE_SIDED,)),
    )


def test_stock_spread_wider_than_100_bps_is_rejected() -> None:
    result = scan_universe(
        (
            _observation(
                last=Decimal("20.00"),
                bid=Decimal("19.90"),
                ask=Decimal("20.11"),
            ),
        ),
        as_of=AS_OF,
    )

    assert result.rejections == (
        ScanRejection(symbol="INTC", reasons=(ScanExclusionReason.SPREAD_TOO_WIDE,)),
    )


def test_stock_spread_at_exactly_100_bps_is_allowed() -> None:
    result = scan_universe(
        (
            _observation(
                last=Decimal("20.00"),
                bid=Decimal("19.90"),
                ask=Decimal("20.10"),
            ),
        ),
        as_of=AS_OF,
    )

    assert [candidate.symbol for candidate in result.candidates] == ["INTC"]
    assert result.candidates[0].spread_bps == Decimal("100")


def test_fewer_than_20_active_option_contracts_is_rejected() -> None:
    result = scan_universe((_observation(option_contracts_active=19),), as_of=AS_OF)

    assert result.rejections == (
        ScanRejection(
            symbol="INTC",
            reasons=(ScanExclusionReason.OPTION_LIQUIDITY_INSUFFICIENT,),
        ),
    )


def test_exactly_20_active_option_contracts_is_allowed() -> None:
    result = scan_universe((_observation(option_contracts_active=20),), as_of=AS_OF)

    assert [candidate.symbol for candidate in result.candidates] == ["INTC"]


def test_market_anchor_lane_only_admits_spy_and_qqq() -> None:
    result = scan_universe(
        (
            _observation(
                symbol="DIA",
                product_kind=ProductKind.US_EXCHANGE_TRADED_PRODUCT,
                lane=UniverseLane.MARKET_ANCHOR,
                last=Decimal("400.00"),
                bid=Decimal("399.90"),
                ask=Decimal("400.10"),
            ),
        ),
        as_of=AS_OF,
    )

    assert result.rejections == (
        ScanRejection(symbol="DIA", reasons=(ScanExclusionReason.INELIGIBLE_MARKET_ANCHOR,)),
    )


# ---------------------------------------------------------------------------
# 3. Scan-eligible versus decision-ready pagination and news behavior
# ---------------------------------------------------------------------------


def test_incomplete_option_page_is_scan_eligible_not_decision_ready() -> None:
    result = scan_universe((_observation(option_page_complete=False),), as_of=AS_OF)

    candidate = result.candidates[0]
    assert candidate.readiness is Readiness.SCAN_ELIGIBLE
    assert candidate.readiness_reasons == (ReadinessReason.OPTION_PAGE_INCOMPLETE,)


def test_incomplete_news_page_blocks_catalyst_decision_readiness() -> None:
    result = scan_universe((_observation(news_page_complete=False),), as_of=AS_OF)

    candidate = result.candidates[0]
    assert candidate.readiness is Readiness.SCAN_ELIGIBLE
    assert candidate.readiness_reasons == (ReadinessReason.NEWS_PAGE_INCOMPLETE,)


def test_catalyst_without_contemporaneous_news_is_not_decision_ready() -> None:
    result = scan_universe((_observation(news_records=0),), as_of=AS_OF)

    candidate = result.candidates[0]
    assert candidate.readiness is Readiness.SCAN_ELIGIBLE
    assert candidate.readiness_reasons == (ReadinessReason.NO_CONTEMPORANEOUS_NEWS,)


def test_catalyst_readiness_reasons_are_collected_in_order() -> None:
    result = scan_universe(
        (_observation(option_page_complete=False, news_page_complete=False),),
        as_of=AS_OF,
    )

    candidate = result.candidates[0]
    assert candidate.readiness_reasons == (
        ReadinessReason.OPTION_PAGE_INCOMPLETE,
        ReadinessReason.NEWS_PAGE_INCOMPLETE,
    )


def test_market_anchor_needs_no_news_to_be_decision_ready() -> None:
    result = scan_universe(
        (
            _observation(
                symbol="SPY",
                product_kind=ProductKind.US_EXCHANGE_TRADED_PRODUCT,
                lane=UniverseLane.MARKET_ANCHOR,
                last=Decimal("559.40"),
                bid=Decimal("559.38"),
                ask=Decimal("559.42"),
                news_records=0,
                news_page_complete=False,
            ),
        ),
        as_of=AS_OF,
    )

    candidate = result.candidates[0]
    assert candidate.readiness is Readiness.DECISION_READY
    assert candidate.readiness_reasons == ()


# ---------------------------------------------------------------------------
# 4. Missing IV/Greeks stay unavailable facts, never numeric zero
# ---------------------------------------------------------------------------


def test_missing_iv_and_greeks_are_preserved_as_unavailable() -> None:
    result = scan_universe(
        (_observation(iv_available=None, greeks_available=None),),
        as_of=AS_OF,
    )

    candidate = result.candidates[0]
    assert candidate.iv_available is None
    assert candidate.greeks_available is None
    raw = universe_scan_bytes(result)
    assert b'"iv_available":null' in raw
    assert b'"greeks_available":null' in raw
    assert b'"iv_available":0' not in raw
    assert b'"greeks_available":0' not in raw
    assert b'"iv_available":false' not in raw


# ---------------------------------------------------------------------------
# 5. Real five-symbol capability fixture and insertion-order byte identity
# ---------------------------------------------------------------------------


def test_real_fixture_routes_expected_symbols() -> None:
    result = scan_universe(_fixture_observations(), as_of=AS_OF)

    assert tuple(candidate.symbol for candidate in result.candidates) == (
        "QQQ",
        "SPY",
        "NVDA",
        "INTC",
    )
    assert result.rejections == (
        ScanRejection(symbol="NIO", reasons=(ScanExclusionReason.PRICE_BELOW_FLOOR,)),
    )


def test_real_fixture_stocks_are_scan_eligible_not_decision_ready() -> None:
    result = scan_universe(_fixture_observations(), as_of=AS_OF)

    by_symbol = {candidate.symbol: candidate for candidate in result.candidates}
    for symbol in ("INTC", "NVDA"):
        candidate = by_symbol[symbol]
        assert candidate.readiness is Readiness.SCAN_ELIGIBLE
        assert candidate.readiness_reasons == (
            ReadinessReason.OPTION_PAGE_INCOMPLETE,
            ReadinessReason.NEWS_PAGE_INCOMPLETE,
        )
    for symbol in ("QQQ", "SPY"):
        candidate = by_symbol[symbol]
        assert candidate.readiness is Readiness.SCAN_ELIGIBLE
        assert candidate.readiness_reasons == (ReadinessReason.OPTION_PAGE_INCOMPLETE,)


def test_insertion_order_does_not_change_canonical_bytes() -> None:
    fixture = _fixture_observations()
    shuffled = (fixture[2], fixture[4], fixture[0], fixture[3], fixture[1])
    reversed_order = tuple(reversed(fixture))

    baseline = universe_scan_bytes(scan_universe(fixture, as_of=AS_OF))
    assert universe_scan_bytes(scan_universe(shuffled, as_of=AS_OF)) == baseline
    assert universe_scan_bytes(scan_universe(reversed_order, as_of=AS_OF)) == baseline
    assert universe_scan_sha256(scan_universe(shuffled, as_of=AS_OF)) == universe_scan_sha256(
        scan_universe(fixture, as_of=AS_OF)
    )


# ---------------------------------------------------------------------------
# 6. Mutation changes the canonical universe hash or is rejected
# ---------------------------------------------------------------------------


def test_frozen_observations_reject_mutation() -> None:
    observation = _observation()

    with pytest.raises(FrozenInstanceError):
        observation.bid = Decimal("1")  # type: ignore[misc]


def test_later_item_mutation_changes_canonical_universe_hash() -> None:
    observations = _fixture_observations()
    baseline = universe_scan_sha256(scan_universe(observations, as_of=AS_OF))

    candidate = observations[3]
    assert candidate.symbol == "NVDA"
    mutated = replace(candidate, bid=candidate.bid + Decimal("0.01"))
    changed = universe_scan_sha256(
        scan_universe((*observations[:3], mutated, observations[4]), as_of=AS_OF),
    )

    assert changed != baseline


# ---------------------------------------------------------------------------
# Ranking: the exact deterministic order routes collection only
# ---------------------------------------------------------------------------


def test_ranking_prefers_anchors_over_fresher_catalysts() -> None:
    anchor = _observation(
        symbol="SPY",
        product_kind=ProductKind.US_EXCHANGE_TRADED_PRODUCT,
        lane=UniverseLane.MARKET_ANCHOR,
        last=Decimal("559.40"),
        bid=Decimal("559.38"),
        ask=Decimal("559.42"),
        quoted_at=QUOTED,
        activity_rank=9,
    )
    catalyst = _observation(
        quoted_at=datetime(2026, 8, 28, 19, 35, 50, tzinfo=UTC),
        activity_rank=1,
    )

    result = scan_universe((catalyst, anchor), as_of=AS_OF)

    assert tuple(candidate.symbol for candidate in result.candidates) == ("SPY", "INTC")


def test_ranking_prefers_fresher_quote_within_one_lane() -> None:
    older = _observation(symbol="AAA", quoted_at=QUOTED, activity_rank=1)
    fresher = _observation(
        symbol="BBB",
        quoted_at=datetime(2026, 8, 28, 19, 35, 45, tzinfo=UTC),
        activity_rank=2,
    )

    result = scan_universe((older, fresher), as_of=AS_OF)

    assert tuple(candidate.symbol for candidate in result.candidates) == ("BBB", "AAA")


def test_ranking_prefers_tighter_spread_when_quotes_are_equally_fresh() -> None:
    wide = _observation(symbol="AAA", bid=Decimal("23.48"), ask=Decimal("23.52"))
    tight = _observation(symbol="BBB")

    result = scan_universe((wide, tight), as_of=AS_OF)

    assert tuple(candidate.symbol for candidate in result.candidates) == ("BBB", "AAA")


def test_ranking_prefers_better_activity_rank_when_quote_quality_ties() -> None:
    ranked_worse = _observation(symbol="AAA", activity_rank=5)
    ranked_better = _observation(symbol="BBB", activity_rank=2)

    result = scan_universe((ranked_worse, ranked_better), as_of=AS_OF)

    assert tuple(candidate.symbol for candidate in result.candidates) == ("BBB", "AAA")


def test_ranking_prefers_larger_absolute_movement_when_supplied() -> None:
    small = _observation(
        symbol="AAA",
        activity_rank=None,
        absolute_movement=Decimal("0.02"),
    )
    large = _observation(
        symbol="BBB",
        activity_rank=None,
        absolute_movement=Decimal("0.05"),
    )

    result = scan_universe((small, large), as_of=AS_OF)

    assert tuple(candidate.symbol for candidate in result.candidates) == ("BBB", "AAA")


def test_ranking_prefers_recent_news_availability_before_symbol() -> None:
    without_news = _observation(symbol="AAA", activity_rank=None, absolute_movement=None)
    with_news = _observation(
        symbol="BBB",
        activity_rank=None,
        absolute_movement=None,
        news_records=0,
    )

    result = scan_universe((without_news, with_news), as_of=AS_OF)

    assert tuple(candidate.symbol for candidate in result.candidates) == ("AAA", "BBB")


def test_ranking_uses_symbol_as_final_tie_break() -> None:
    second = _observation(symbol="BBB", activity_rank=None, absolute_movement=None, news_records=0)
    first = _observation(symbol="AAA", activity_rank=None, absolute_movement=None, news_records=0)

    result = scan_universe((second, first), as_of=AS_OF)

    assert tuple(candidate.symbol for candidate in result.candidates) == ("AAA", "BBB")


def test_duplicate_symbols_are_rejected() -> None:
    with pytest.raises(UniverseContractRejected, match="DUPLICATE_SYMBOL"):
        scan_universe((_observation(), _observation()), as_of=AS_OF)


# ---------------------------------------------------------------------------
# 7. Exact 100k tier, quantity, aggregate-cap, and per-underlying-cap math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "quantity", "max_loss"),
    (
        (RiskTier.FIVE_PERCENT, 20, Decimal("5000")),
        (RiskTier.TEN_PERCENT, 40, Decimal("10000")),
        (RiskTier.TWENTY_PERCENT, 80, Decimal("20000")),
    ),
)
def test_100k_tier_allocation_examples(
    tier: RiskTier,
    quantity: int,
    max_loss: Decimal,
) -> None:
    decision = allocate_defined_risk(_portfolio(), _opportunity(risk_tier=tier))

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.reason_codes == ()
    assert decision.quantity == quantity
    assert decision.max_loss == max_loss


def test_quantity_is_floored_from_max_debit_per_contract() -> None:
    decision = allocate_defined_risk(
        _portfolio(),
        _opportunity(max_debit_per_contract=Decimal("251")),
    )

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.quantity == 19
    assert decision.max_loss == Decimal("4769")


def test_aggregate_50_percent_cap_binds_allocation() -> None:
    decision = allocate_defined_risk(
        _portfolio(open_debit=Decimal("49000")),
        _opportunity(risk_tier=RiskTier.TWENTY_PERCENT),
    )

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.quantity == 4
    assert decision.max_loss == Decimal("1000")
    assert decision.remaining_aggregate == Decimal("0")


def test_per_underlying_20_percent_cap_binds_allocation() -> None:
    decision = allocate_defined_risk(
        _portfolio(
            open_debit=Decimal("19500"),
            exposures=(UnderlyingExposure(underlying="INTC", open_debit=Decimal("19500")),),
        ),
        _opportunity(risk_tier=RiskTier.TWENTY_PERCENT),
    )

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.quantity == 2
    assert decision.max_loss == Decimal("500")
    assert decision.remaining_underlying == Decimal("0")


# ---------------------------------------------------------------------------
# 8. Cash binds without any buying-power input
# ---------------------------------------------------------------------------


def test_cash_lower_than_equity_prevents_borrowing() -> None:
    decision = allocate_defined_risk(
        _portfolio(cash=Decimal("3000")),
        _opportunity(risk_tier=RiskTier.TWENTY_PERCENT),
    )

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.quantity == 12
    assert decision.max_loss == Decimal("3000")
    assert decision.remaining_cash == Decimal("0")


def test_zero_cash_is_a_valid_state_that_abstains() -> None:
    decision = allocate_defined_risk(
        _portfolio(cash=Decimal("0")),
        _opportunity(risk_tier=RiskTier.TWENTY_PERCENT),
    )

    assert decision.status is AllocationStatus.ABSTAINED
    assert decision.reason_codes == (AbstainReason.CASH_INSUFFICIENT,)
    assert decision.quantity == 0
    assert decision.max_loss == Decimal("0")


def test_portfolio_state_has_no_buying_power_input() -> None:
    with pytest.raises(TypeError):
        PortfolioState(
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            open_debit=Decimal("0"),
            exposures=(),
            buying_power=Decimal("400000"),
        )


# ---------------------------------------------------------------------------
# 9. Open and reserved exposures reduce later capacity; no daily count cap
# ---------------------------------------------------------------------------


def _pending_reservation(
    *,
    opportunity_id: str,
    underlying: str,
    max_loss: Decimal,
    digest: str,
) -> AllocationReservation:
    return AllocationReservation(
        opportunity_id=opportunity_id,
        opportunity_sha256=digest * 64,
        reservation_id=digest * 63 + "0",
        underlying=underlying,
        quantity=1,
        max_loss=max_loss,
    )


def test_pending_reservations_reduce_aggregate_capacity() -> None:
    reservations = (
        _pending_reservation(
            opportunity_id="OPP-NVDA-PENDING",
            underlying="NVDA",
            max_loss=Decimal("20000"),
            digest="a",
        ),
        _pending_reservation(
            opportunity_id="OPP-AMD-PENDING",
            underlying="AMD",
            max_loss=Decimal("20000"),
            digest="b",
        ),
    )

    decision = allocate_defined_risk(
        _portfolio(),
        _opportunity(
            opportunity_id="OPP-MSFT-NEW",
            underlying="MSFT",
            risk_tier=RiskTier.TWENTY_PERCENT,
        ),
        existing_reservations=reservations,
    )

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.quantity == 40
    assert decision.max_loss == Decimal("10000")
    assert decision.remaining_aggregate == Decimal("0")


def test_pending_reservations_reduce_per_underlying_capacity() -> None:
    reservation = _pending_reservation(
        opportunity_id="OPP-INTC-PENDING",
        underlying="INTC",
        max_loss=Decimal("15000"),
        digest="a",
    )

    decision = allocate_defined_risk(
        _portfolio(),
        _opportunity(
            opportunity_id="OPP-INTC-NEW",
            risk_tier=RiskTier.TWENTY_PERCENT,
        ),
        existing_reservations=(reservation,),
    )

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.quantity == 20
    assert decision.max_loss == Decimal("5000")
    assert decision.remaining_underlying == Decimal("0")


def test_pending_reservations_reduce_unborrowed_cash_capacity() -> None:
    reservation = _pending_reservation(
        opportunity_id="OPP-NVDA-PENDING",
        underlying="NVDA",
        max_loss=Decimal("8000"),
        digest="a",
    )

    decision = allocate_defined_risk(
        _portfolio(cash=Decimal("10000")),
        _opportunity(
            opportunity_id="OPP-MSFT-NEW",
            underlying="MSFT",
            risk_tier=RiskTier.TWENTY_PERCENT,
        ),
        existing_reservations=(reservation,),
    )

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.quantity == 8
    assert decision.max_loss == Decimal("2000")
    assert decision.remaining_cash == Decimal("0")


def test_reserved_exposure_reduces_a_later_opportunity() -> None:
    first = allocate_defined_risk(
        _portfolio(),
        _opportunity(risk_tier=RiskTier.TEN_PERCENT),
    )
    assert first.status is AllocationStatus.ALLOCATED
    assert first.max_loss == Decimal("10000")

    later_portfolio = _portfolio(
        open_debit=first.max_loss,
        exposures=(UnderlyingExposure(underlying="INTC", open_debit=first.max_loss),),
    )
    second = allocate_defined_risk(
        later_portfolio,
        _opportunity(
            opportunity_id="OPP-INTC-20260828-002",
            risk_tier=RiskTier.TWENTY_PERCENT,
        ),
    )

    assert second.status is AllocationStatus.ALLOCATED
    assert second.quantity == 40
    assert second.max_loss == Decimal("10000")
    assert second.remaining_underlying == Decimal("0")


def test_distinct_opportunities_have_no_count_cap() -> None:
    state = _portfolio()
    reservation_loss = Decimal("0")
    exposures: list[UnderlyingExposure] = []
    symbols = ("INTC", "NVDA", "AMD")
    for index, symbol in enumerate(symbols, start=1):
        decision = allocate_defined_risk(
            state,
            _opportunity(
                opportunity_id=f"OPP-{symbol}-20260828-00{index}",
                underlying=symbol,
                risk_tier=RiskTier.TWENTY_PERCENT,
            ),
        )
        assert decision.status is AllocationStatus.ALLOCATED
        reservation_loss += decision.max_loss
        exposures.append(UnderlyingExposure(underlying=symbol, open_debit=decision.max_loss))
        state = _portfolio(
            cash=Decimal("100000") - reservation_loss,
            open_debit=reservation_loss,
            exposures=tuple(exposures),
        )

    exhausted = allocate_defined_risk(
        state,
        _opportunity(
            opportunity_id="OPP-MSFT-20260828-004",
            underlying="MSFT",
            risk_tier=RiskTier.TWENTY_PERCENT,
        ),
    )
    assert exhausted.status is AllocationStatus.ABSTAINED
    assert exhausted.reason_codes == (AbstainReason.AGGREGATE_CAP_INSUFFICIENT,)


def test_allocator_contract_has_no_daily_count_or_margin_fields() -> None:
    names = {field.name for field in fields(PortfolioState)}
    names |= {field.name for field in fields(DefinedRiskOpportunity)}

    for name in names:
        assert "daily" not in name
        assert "count" not in name
        assert "buying_power" not in name
        assert "margin" not in name
        assert "confidence" not in name
        assert "leverage" not in name


# ---------------------------------------------------------------------------
# 10. Idempotent reservation replay versus conflicting same-ID payload
# ---------------------------------------------------------------------------


def _reservation_for(
    opportunity: DefinedRiskOpportunity,
    decision: object,
) -> AllocationReservation:
    return AllocationReservation(
        opportunity_id=opportunity.opportunity_id,
        opportunity_sha256=defined_risk_opportunity_sha256(opportunity),
        reservation_id=decision.reservation_id,  # type: ignore[attr-defined]
        underlying=opportunity.underlying,
        quantity=decision.quantity,  # type: ignore[attr-defined]
        max_loss=decision.max_loss,  # type: ignore[attr-defined]
    )


def test_exact_canonical_replay_returns_the_same_reservation() -> None:
    portfolio = _portfolio()
    opportunity = _opportunity()
    first = allocate_defined_risk(portfolio, opportunity)
    reservation = _reservation_for(opportunity, first)

    replay = allocate_defined_risk(
        portfolio,
        opportunity,
        existing_reservations=(reservation,),
    )

    assert replay.status is AllocationStatus.ALLOCATED
    assert replay.reservation_id == first.reservation_id
    assert replay.quantity == first.quantity
    assert replay.max_loss == first.max_loss
    assert replay.reason_codes == ()


def test_conflicting_same_id_replay_is_rejected() -> None:
    portfolio = _portfolio()
    opportunity = _opportunity()
    first = allocate_defined_risk(portfolio, opportunity)
    reservation = _reservation_for(opportunity, first)
    conflicting = replace(opportunity, max_debit_per_contract=Decimal("300"))

    with pytest.raises(UniverseContractRejected, match="IDENTITY_CONFLICT"):
        allocate_defined_risk(
            portfolio,
            conflicting,
            existing_reservations=(reservation,),
        )


# ---------------------------------------------------------------------------
# 11. Float, negative, NaN, infinity, malformed ID, duplicate exposure cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "equity",
    (
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        100000.0,
        "100000",
    ),
)
def test_portfolio_rejects_invalid_equity(equity: object) -> None:
    with pytest.raises(UniverseContractRejected):
        _portfolio(equity=equity)


@pytest.mark.parametrize(
    "cash",
    (Decimal("-5"), Decimal("NaN"), 3000.0),
)
def test_portfolio_rejects_invalid_cash(cash: object) -> None:
    with pytest.raises(UniverseContractRejected):
        _portfolio(cash=cash)


@pytest.mark.parametrize(
    "open_debit",
    (Decimal("-1"), Decimal("NaN"), 10.0),
)
def test_portfolio_rejects_invalid_open_debit(open_debit: object) -> None:
    with pytest.raises(UniverseContractRejected):
        _portfolio(open_debit=open_debit)


@pytest.mark.parametrize(
    "open_debit",
    (Decimal("0"), Decimal("-10"), Decimal("Infinity"), 25.0),
)
def test_exposure_rejects_invalid_debit(open_debit: object) -> None:
    with pytest.raises(UniverseContractRejected):
        UnderlyingExposure(underlying="INTC", open_debit=open_debit)  # type: ignore[arg-type]


def test_portfolio_rejects_duplicate_underlying_exposures() -> None:
    with pytest.raises(UniverseContractRejected, match="DUPLICATE_EXPOSURE"):
        _portfolio(
            open_debit=Decimal("300"),
            exposures=(
                UnderlyingExposure(underlying="INTC", open_debit=Decimal("100")),
                UnderlyingExposure(underlying="INTC", open_debit=Decimal("200")),
            ),
        )


def test_portfolio_rejects_exposure_sum_above_aggregate_open_debit() -> None:
    with pytest.raises(UniverseContractRejected, match="INCONSISTENT_STATE"):
        _portfolio(
            open_debit=Decimal("100"),
            exposures=(UnderlyingExposure(underlying="INTC", open_debit=Decimal("300")),),
        )


def test_portfolio_rejects_open_debit_above_equity() -> None:
    with pytest.raises(UniverseContractRejected, match="INCONSISTENT_STATE"):
        _portfolio(open_debit=Decimal("100001"))


def test_portfolio_rejects_cash_above_equity() -> None:
    with pytest.raises(UniverseContractRejected, match="INCONSISTENT_STATE"):
        _portfolio(cash=Decimal("100001"))


@pytest.mark.parametrize(
    "max_debit",
    (Decimal("0"), Decimal("-250"), Decimal("NaN"), 250.0, "250"),
)
def test_opportunity_rejects_invalid_max_debit(max_debit: object) -> None:
    with pytest.raises(UniverseContractRejected):
        _opportunity(max_debit_per_contract=max_debit)


@pytest.mark.parametrize("tier", (Decimal("0.05"), 0.05, "0.05"))
def test_opportunity_rejects_non_tier_risk_values(tier: object) -> None:
    with pytest.raises(UniverseContractRejected):
        _opportunity(risk_tier=tier)


def test_opportunity_rejects_non_boolean_readiness_flag() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_BOOLEAN"):
        _opportunity(decision_ready="true")


@pytest.mark.parametrize("identifier", ("", "bad id!", "white space"))
def test_opportunity_rejects_malformed_identifiers(identifier: str) -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_IDENTIFIER"):
        _opportunity(opportunity_id=identifier)


def test_opportunity_rejects_lowercase_underlying() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_IDENTIFIER"):
        _opportunity(underlying="intc")


def test_duplicate_reservation_identities_are_rejected() -> None:
    reservation = AllocationReservation(
        opportunity_id="OPP-INTC-20260828-001",
        opportunity_sha256=defined_risk_opportunity_sha256(_opportunity()),
        reservation_id="a" * 64,
        underlying="INTC",
        quantity=1,
        max_loss=Decimal("250"),
    )

    with pytest.raises(UniverseContractRejected, match="DUPLICATE_RESERVATION"):
        allocate_defined_risk(
            _portfolio(),
            _opportunity(),
            existing_reservations=(reservation, reservation),
        )


def test_observation_rejects_float_financial_values() -> None:
    with pytest.raises(UniverseContractRejected, match="FLOAT_FORBIDDEN"):
        _observation(last=23.5)


def test_observation_rejects_float_timestamps() -> None:
    with pytest.raises(UniverseContractRejected, match="FLOAT_FORBIDDEN"):
        _observation(quoted_at=1_722_000_000.0)


def test_observation_rejects_naive_timestamps() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_CLOCK"):
        _observation(observed_at=datetime(2026, 8, 28, 19, 35, 30))


def test_observation_rejects_lowercase_symbol() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_IDENTIFIER"):
        _observation(symbol="intc")


def test_observation_rejects_non_boolean_flags() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_BOOLEAN"):
        _observation(active=1)


def test_observation_rejects_non_boolean_availability_facts() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_BOOLEAN"):
        _observation(iv_available=0)


def test_observation_rejects_negative_counts() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_COUNT"):
        _observation(option_contracts_active=-1)


def test_observation_rejects_boolean_counts() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_COUNT"):
        _observation(news_records=True)


def test_observation_rejects_non_positive_activity_rank() -> None:
    with pytest.raises(UniverseContractRejected, match="INVALID_RANK"):
        _observation(activity_rank=0)


def test_unready_opportunity_abstains_without_capacity_math() -> None:
    decision = allocate_defined_risk(_portfolio(), _opportunity(decision_ready=False))

    assert decision.status is AllocationStatus.ABSTAINED
    assert decision.reason_codes == (AbstainReason.OPPORTUNITY_NOT_READY,)
    assert decision.quantity == 0
    assert decision.max_loss == Decimal("0")
    assert decision.reservation_id is None


# ---------------------------------------------------------------------------
# 12. Canonical opportunity parser and serializer reject malformed bytes
# ---------------------------------------------------------------------------


def test_opportunity_canonical_bytes_round_trip() -> None:
    opportunity = _opportunity(risk_tier=RiskTier.TEN_PERCENT)

    raw = defined_risk_opportunity_bytes(opportunity)

    assert parse_defined_risk_opportunity(raw) == opportunity
    assert b'"risk_tier":"0.1"' in raw
    assert b'"max_debit_per_contract":"250"' in raw


def test_parser_rejects_unknown_fields() -> None:
    payload = json.loads(defined_risk_opportunity_bytes(_opportunity()))
    payload["buying_power"] = "999"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(UniverseContractRejected, match="UNKNOWN_FIELD"):
        parse_defined_risk_opportunity(raw)


def test_parser_rejects_missing_fields() -> None:
    payload = json.loads(defined_risk_opportunity_bytes(_opportunity()))
    del payload["underlying"]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(UniverseContractRejected, match="MISSING_FIELD"):
        parse_defined_risk_opportunity(raw)


def test_parser_rejects_duplicate_fields() -> None:
    raw = defined_risk_opportunity_bytes(_opportunity())
    marker = b'"decision_id":"DEC-INTC-20260828-001",'
    assert marker in raw
    duplicated = raw.replace(marker, marker + b'"decision_id":"DEC-INTC-20260828-002",', 1)

    with pytest.raises(UniverseContractRejected, match="DUPLICATE_FIELD"):
        parse_defined_risk_opportunity(duplicated)


def test_parser_rejects_float_literals() -> None:
    raw = defined_risk_opportunity_bytes(_opportunity()).replace(
        b'"max_debit_per_contract":"250"',
        b'"max_debit_per_contract":250.0',
        1,
    )

    with pytest.raises(UniverseContractRejected, match="INVALID_DOCUMENT"):
        parse_defined_risk_opportunity(raw)


def test_parser_rejects_noncanonical_bytes() -> None:
    payload = json.loads(defined_risk_opportunity_bytes(_opportunity()))
    raw = json.dumps(payload, indent=2).encode("utf-8")

    with pytest.raises(UniverseContractRejected, match="NON_CANONICAL_DOCUMENT"):
        parse_defined_risk_opportunity(raw)


def test_parser_rejects_unknown_schema() -> None:
    payload = json.loads(defined_risk_opportunity_bytes(_opportunity()))
    payload["schema"] = "esscher.something_else"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(UniverseContractRejected, match="UNSUPPORTED_SCHEMA"):
        parse_defined_risk_opportunity(raw)


def test_parser_rejects_unknown_risk_tier() -> None:
    payload = json.loads(defined_risk_opportunity_bytes(_opportunity()))
    payload["risk_tier"] = "0.07"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    with pytest.raises(UniverseContractRejected, match="UNKNOWN_STATE"):
        parse_defined_risk_opportunity(raw)


def test_first_owner_tier_drives_ten_percent_operative_sizing() -> None:
    """Owner sizing directive (2026-09-04, MS-Mesh, issue #68): the first
    approved tier is the operative one, sizing one position up to a $10,000
    max loss on a fresh $100k account while every frozen capacity cap still
    binds (20% per underlying, 50% aggregate, unborrowed cash)."""

    from esscher.application.autonomous_bridge import derived_risk_tier
    from esscher.risk.policy import load_risk_policy_v2

    policy = load_risk_policy_v2()
    assert policy.risk_tiers[0] == Decimal("0.10")
    operative = derived_risk_tier(policy)
    assert operative is RiskTier.TEN_PERCENT

    decision = allocate_defined_risk(_portfolio(), _opportunity(risk_tier=operative))

    assert decision.status is AllocationStatus.ALLOCATED
    assert decision.quantity == 40
    assert decision.max_loss == Decimal("10000")
    assert decision.remaining_tier == Decimal("0")
    assert decision.remaining_underlying == Decimal("10000")
    assert decision.remaining_aggregate == Decimal("40000")
    assert decision.remaining_cash == Decimal("90000")
