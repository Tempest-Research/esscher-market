"""RiskPolicy V2 owner-bound canonical contract tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from ringdown_market.autonomy import autonomous_policy_sha256
from ringdown_market.autonomy.universe import (
    AbstainReason,
    AllocationStatus,
    DefinedRiskOpportunity,
    PortfolioState,
    RiskTier,
    allocate_defined_risk,
)
from ringdown_market.execution.expression.compiler import (
    CompiledExpression,
    compiled_expression_sha256,
)
from ringdown_market.execution.expression.reasons import ExpressionKind
from ringdown_market.execution.models import (
    DebitVerticalPermit,
    OptionLeg,
    OptionSide,
    OptionType,
    PositionIntent,
    VerticalType,
    debit_vertical_permit_id,
)
from ringdown_market.risk import (
    RISK_POLICY_V2_SCHEMA,
    RISK_POLICY_V2_SCHEMA_VERSION,
    AccountSnapshot,
    ControlState,
    RiskAbstentionV2,
    RiskAllocationPreviewV2,
    RiskApprovalV2,
    RiskKernel,
    RiskLedger,
    RiskPolicyV2,
    RiskReason,
    RiskRejected,
    load_risk_policy_v2,
    parse_risk_policy_v2,
    risk_policy_v2_bytes,
    risk_policy_v2_payload,
    risk_policy_v2_sha256,
)
from ringdown_market.risk.snapshots import OrderSnapshot, PositionSnapshot

NOW = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _payload() -> dict[str, object]:
    value = json.loads(risk_policy_v2_bytes())
    assert isinstance(value, dict)
    return value


def test_packaged_v2_policy_is_canonical_owner_bound_and_exact() -> None:
    policy = load_risk_policy_v2()
    owner_sha256 = autonomous_policy_sha256()

    assert isinstance(policy, RiskPolicyV2)
    assert policy.starting_equity == Decimal("100000")
    assert policy.risk_tiers == (Decimal("0.10"), Decimal("0.05"), Decimal("0.20"))
    assert policy.max_per_underlying_open_debit_fraction == Decimal("0.20")
    assert policy.max_aggregate_open_debit_fraction == Decimal("0.50")
    assert policy.emergency_drawdown_freeze_fraction == Decimal("0.50")
    assert policy.daily_loss_stop is None
    assert policy.trade_count_cap is None
    assert policy.open_expression_count_cap is None
    assert policy.cash_only is True
    assert policy.defined_risk_only is True
    assert policy.truth_max_age_seconds == 30
    assert policy.owner_policy_sha256 == owner_sha256
    assert policy.constants_source_sha256 == owner_sha256
    assert policy.constants_verified is True
    assert risk_policy_v2_payload(policy)["schema"] == RISK_POLICY_V2_SCHEMA
    assert risk_policy_v2_payload(policy)["schema_version"] == RISK_POLICY_V2_SCHEMA_VERSION
    assert risk_policy_v2_bytes(policy) == risk_policy_v2_bytes()
    assert risk_policy_v2_sha256(policy) == risk_policy_v2_sha256()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("unapproved_override", True),
        ("starting_equity", 100000.0),
        ("trade_count_cap", 1),
        ("daily_loss_stop", "0"),
        ("open_expression_count_cap", 1),
        ("owner_policy_sha256", "0" * 64),
        ("constants_source_sha256", "0" * 64),
    ),
)
def test_v2_parser_rejects_unknown_float_nonnull_cap_and_unbound_owner_values(
    field: str, value: object
) -> None:
    payload = _payload()
    payload[field] = value

    with pytest.raises(RiskRejected):
        parse_risk_policy_v2(_canonical(payload))


def test_v2_parser_rejects_duplicate_and_noncanonical_bytes() -> None:
    duplicate = risk_policy_v2_bytes().replace(
        b'"cash_only":true,', b'"cash_only":true,"cash_only":true,', 1
    )

    with pytest.raises(RiskRejected, match="duplicate"):
        parse_risk_policy_v2(duplicate)
    with pytest.raises(RiskRejected, match="not canonical"):
        parse_risk_policy_v2(risk_policy_v2_bytes() + b"\n")


@pytest.mark.parametrize("cash", (Decimal("-0.01"), Decimal("NaN"), Decimal("Infinity")))
def test_account_snapshot_cash_is_optional_for_v1_and_strict_when_present(cash: Decimal) -> None:
    legacy = AccountSnapshot(
        equity=Decimal("100000"),
        buying_power=Decimal("400000"),
        currency="USD",
        observed_at=NOW,
    )

    assert legacy.cash is None
    assert AccountSnapshot(
        equity=Decimal("100000"),
        buying_power=Decimal("400000"),
        currency="USD",
        observed_at=NOW,
        cash=Decimal("0"),
    ).cash == Decimal("0")
    with pytest.raises(RiskRejected, match=r"account\.cash"):
        AccountSnapshot(
            equity=Decimal("100000"),
            buying_power=Decimal("400000"),
            currency="USD",
            observed_at=NOW,
            cash=cash,
        )


class V2Truth:
    """Read-only fresh broker truth used only by V2 contract tests."""

    def __init__(
        self,
        *,
        account: AccountSnapshot | None,
        positions: tuple[PositionSnapshot, ...] = (),
        orders: tuple[OrderSnapshot, ...] = (),
        clock: object = NOW,
    ) -> None:
        self.account_snapshot = account
        self.position_snapshots = positions
        self.order_snapshots = orders
        self.clock = clock

    def account(self) -> AccountSnapshot | None:
        return self.account_snapshot

    def positions(self) -> tuple[PositionSnapshot, ...]:
        return self.position_snapshots

    def orders(self) -> tuple[OrderSnapshot, ...]:
        return self.order_snapshots

    def broker_clock(self) -> object:
        return self.clock


def _v2_account(
    *,
    equity: Decimal = Decimal("100000"),
    cash: Decimal | None = Decimal("100000"),
    buying_power: Decimal = Decimal("400000"),
    observed_at: datetime = NOW,
) -> AccountSnapshot:
    return AccountSnapshot(
        equity=equity,
        cash=cash,
        buying_power=buying_power,
        currency="USD",
        observed_at=observed_at,
    )


def _v2_compiled(
    *,
    event_id: str = "KR-2026Q2-EARNINGS",
    underlying: str = "KR",
    maximum_loss: Decimal = Decimal("5000"),
    decision_sha256: str = "a" * 64,
) -> CompiledExpression:
    long_strike = Decimal("100")
    short_strike = long_strike + (maximum_loss / Decimal("100")) + Decimal("1")
    long_symbol = f"{underlying}260918C{int(long_strike * 1000):08d}"
    short_symbol = f"{underlying}260918C{int(short_strike * 1000):08d}"
    return CompiledExpression(
        expression_kind=ExpressionKind.DEBIT_VERTICAL,
        event_id=event_id,
        decision_sha256=decision_sha256,
        snapshot_sha256="b" * 64,
        policy_sha256=risk_policy_v2_sha256(),
        gate_d_report_sha256="c" * 64,
        compiled_at=NOW,
        shares=None,
        long_option=None,
        debit_vertical={
            "underlying": underlying,
            "vertical_type": "BULL_CALL",
            "expiry": "2026-09-18",
            "quantity": 1,
            "order_type": "LIMIT",
            "legging": "ATOMIC_PACKAGE",
            "limit_price": str(maximum_loss / Decimal("100")),
            "limit_price_rule": "PACKAGE_NET_ASK",
            "width": str(short_strike - long_strike),
            "maximum_loss": str(maximum_loss),
            "package_id": f"{long_symbol}+{short_symbol}",
            "long_leg": {
                "symbol": long_symbol,
                "option_type": "CALL",
                "strike": str(long_strike),
            },
            "short_leg": {
                "symbol": short_symbol,
                "option_type": "CALL",
                "strike": str(short_strike),
            },
        },
    )


def _v2_permit(
    kernel: RiskKernel,
    compiled: CompiledExpression,
    *,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> DebitVerticalPermit:
    block = compiled.debit_vertical
    assert isinstance(block, dict)
    long = block["long_leg"]
    short = block["short_leg"]
    assert isinstance(long, dict)
    assert isinstance(short, dict)
    issued = issued_at or NOW - timedelta(seconds=1)
    expires = expires_at or NOW + timedelta(seconds=30)
    candidate = DebitVerticalPermit._from_frozen_decision(
        permit_id="UNBOUND",
        event_run_id=compiled.event_id,
        policy_sha256=kernel.policy_sha256,
        snapshot_sha256=compiled.snapshot_sha256,
        decision_sha256=compiled.decision_sha256,
        evidence_sha256="d" * 64,
        protocol_sha256="e" * 64,
        execution_protocol_sha256="f" * 64,
        issued_at=issued,
        expires_at=expires,
        vertical_type=VerticalType.BULL_CALL,
        quantity=1,
        limit_price=Decimal(str(block["limit_price"])),
        legs=(
            OptionLeg(
                symbol=str(long["symbol"]),
                underlying=str(block["underlying"]),
                expiry=date.fromisoformat(str(block["expiry"])),
                option_type=OptionType.CALL,
                strike=Decimal(str(long["strike"])),
                side=OptionSide.BUY,
                position_intent=PositionIntent.BUY_TO_OPEN,
            ),
            OptionLeg(
                symbol=str(short["symbol"]),
                underlying=str(block["underlying"]),
                expiry=date.fromisoformat(str(block["expiry"])),
                option_type=OptionType.CALL,
                strike=Decimal(str(short["strike"])),
                side=OptionSide.SELL,
                position_intent=PositionIntent.SELL_TO_OPEN,
            ),
        ),
    )
    return replace(candidate, permit_id=debit_vertical_permit_id(candidate))


def _v2_opportunity(
    compiled: CompiledExpression,
    *,
    opportunity_id: str = "OPP-KR-1",
    risk_tier: RiskTier = RiskTier.FIVE_PERCENT,
    decision_ready: bool = True,
) -> DefinedRiskOpportunity:
    block = compiled.debit_vertical
    assert isinstance(block, dict)
    return DefinedRiskOpportunity(
        opportunity_id=opportunity_id,
        decision_id=compiled.decision_sha256,
        expression_id=compiled_expression_sha256(compiled),
        underlying=str(block["underlying"]),
        risk_tier=risk_tier,
        max_debit_per_contract=Decimal(str(block["maximum_loss"])),
        decision_ready=decision_ready,
    )


def _v2_kernel(tmp_path, *, account: AccountSnapshot | None = None) -> tuple[RiskKernel, V2Truth]:
    truth = V2Truth(account=account or _v2_account())
    return RiskKernel(load_risk_policy_v2(), RiskLedger(tmp_path / "risk.sqlite3"), truth), truth


def _freeze_v2(
    kernel: RiskKernel, compiled: CompiledExpression, *, candidate_id: str = "candidate-1"
) -> None:
    kernel.freeze_candidate(
        event_id=compiled.event_id,
        candidate_id=candidate_id,
        compiled=compiled,
        evidence_mode="EVALUATED",
        now=NOW,
    )


def _authorize_v2(
    kernel: RiskKernel,
    compiled: CompiledExpression,
    *,
    opportunity: DefinedRiskOpportunity | None = None,
    candidate_id: str = "candidate-1",
    permit: DebitVerticalPermit | None = None,
    now: datetime = NOW,
) -> RiskApprovalV2 | RiskAbstentionV2:
    return kernel.authorize_entry_v2(
        event_id=compiled.event_id,
        candidate_id=candidate_id,
        opportunity=opportunity or _v2_opportunity(compiled),
        compiled=compiled,
        permit=permit or _v2_permit(kernel, compiled),
        now=now,
    )


def test_v2_kernel_atomically_binds_allocation_permit_and_zero_cash_abstains(tmp_path) -> None:
    kernel, _ = _v2_kernel(tmp_path)
    compiled = _v2_compiled()
    opportunity = _v2_opportunity(compiled)
    permit = _v2_permit(kernel, compiled)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(kernel, compiled)

    approval = kernel.authorize_entry_v2(
        event_id=compiled.event_id,
        candidate_id="candidate-1",
        opportunity=opportunity,
        compiled=compiled,
        permit=permit,
        now=NOW,
    )

    assert isinstance(approval, RiskApprovalV2)
    assert approval.quantity == 1
    assert approval.max_loss == Decimal("5000")
    assert approval.risk_tier is RiskTier.FIVE_PERCENT
    assert approval.reservation_id == approval.allocation_reservation_id
    row = kernel.ledger.v2_reservation_for_event(compiled.event_id)
    assert row is not None
    assert row["opportunity_id"] == opportunity.opportunity_id
    assert row["account_cash"] == "100000"
    assert row["permit_sha256"] == approval.permit_sha256
    replay = kernel.authorize_entry_v2(
        event_id=compiled.event_id,
        candidate_id="candidate-1",
        opportunity=opportunity,
        compiled=compiled,
        permit=permit,
        now=NOW,
    )
    assert replay == approval

    zero, _ = _v2_kernel(tmp_path / "zero", account=_v2_account(cash=Decimal("0")))
    zero_compiled = _v2_compiled(event_id="MSFT-2026Q3-EARNINGS", underlying="MSFT")
    zero_opportunity = _v2_opportunity(zero_compiled, opportunity_id="OPP-MSFT-0")
    assert zero.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(zero, zero_compiled)
    abstention = zero.authorize_entry_v2(
        event_id=zero_compiled.event_id,
        candidate_id="candidate-1",
        opportunity=zero_opportunity,
        compiled=zero_compiled,
        permit=_v2_permit(zero, zero_compiled),
        now=NOW,
    )
    assert isinstance(abstention, RiskAbstentionV2)
    assert abstention.reason_codes == (AbstainReason.CASH_INSUFFICIENT,)
    assert zero.ledger.v2_reservation_for_event(zero_compiled.event_id) is None


def test_v2_preview_is_non_authoritative_read_only_and_matches_unchanged_authorization(
    tmp_path,
) -> None:
    kernel, _ = _v2_kernel(tmp_path)
    compiled = _v2_compiled()
    opportunity = _v2_opportunity(compiled)
    permit = _v2_permit(kernel, compiled)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    before_events = kernel.ledger.passport_events()

    preview = kernel.preview_allocation_v2(opportunity=opportunity, compiled=compiled, now=NOW)

    assert isinstance(preview, RiskAllocationPreviewV2)
    assert preview.authority == "NON_AUTHORITATIVE_PREVIEW"
    assert preview.allocation.status is AllocationStatus.ALLOCATED
    assert preview.allocation.quantity == 1
    assert preview.allocation.max_loss == Decimal("5000")
    assert kernel.ledger.candidate_for_event(compiled.event_id) is None
    assert kernel.ledger.v2_reservation_for_event(compiled.event_id) is None
    assert kernel.ledger.passport_events() == before_events

    _freeze_v2(kernel, compiled)
    approved = _authorize_v2(kernel, compiled, opportunity=opportunity, permit=permit)

    assert isinstance(approved, RiskApprovalV2)
    assert approved.quantity == preview.allocation.quantity
    assert approved.max_loss == preview.allocation.max_loss


def test_v2_preview_recomputes_recent_reservation_and_truth_drift_without_mutation(
    tmp_path,
) -> None:
    kernel, truth = _v2_kernel(tmp_path)
    first = _v2_compiled(maximum_loss=Decimal("20000"))
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(kernel, first)
    first_approval = _authorize_v2(
        kernel,
        first,
        opportunity=_v2_opportunity(
            first, opportunity_id="OPP-KR-RECENT", risk_tier=RiskTier.TWENTY_PERCENT
        ),
    )
    assert isinstance(first_approval, RiskApprovalV2)
    second = _v2_compiled(
        event_id="MSFT-2026Q2-EARNINGS", underlying="MSFT", maximum_loss=Decimal("20000")
    )
    second_opportunity = _v2_opportunity(
        second, opportunity_id="OPP-MSFT-RECENT", risk_tier=RiskTier.TWENTY_PERCENT
    )
    before_events = kernel.ledger.passport_events()

    with_recent_reservation = kernel.preview_allocation_v2(
        opportunity=second_opportunity, compiled=second, now=NOW
    )

    assert with_recent_reservation.allocation.status is AllocationStatus.ALLOCATED
    assert with_recent_reservation.allocation.quantity == 1
    assert kernel.ledger.v2_reservation_for_event(second.event_id) is None
    assert kernel.ledger.passport_events() == before_events

    truth.account_snapshot = _v2_account(cash=Decimal("0"))
    drifted = kernel.preview_allocation_v2(opportunity=second_opportunity, compiled=second, now=NOW)

    assert drifted.allocation.status is AllocationStatus.ABSTAINED
    assert drifted.allocation.reason_codes == (AbstainReason.CASH_INSUFFICIENT,)
    assert kernel.ledger.v2_reservation_for_event(second.event_id) is None
    assert kernel.ledger.passport_events() == before_events


@pytest.mark.parametrize(
    ("tier", "quantity", "max_loss"),
    (
        (RiskTier.FIVE_PERCENT, 20, Decimal("5000")),
        (RiskTier.TEN_PERCENT, 40, Decimal("10000")),
        (RiskTier.TWENTY_PERCENT, 80, Decimal("20000")),
    ),
)
def test_v2_owner_tier_capacity_is_exact_on_a_100k_account(
    tier: RiskTier, quantity: int, max_loss: Decimal
) -> None:
    portfolio = PortfolioState(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        open_debit=Decimal("0"),
    )
    opportunity = DefinedRiskOpportunity(
        opportunity_id=f"OPP-{tier.name}",
        decision_id="a" * 64,
        expression_id="b" * 64,
        underlying="KR",
        risk_tier=tier,
        max_debit_per_contract=Decimal("250"),
        decision_ready=True,
    )

    allocation = allocate_defined_risk(portfolio, opportunity)

    assert allocation.status is AllocationStatus.ALLOCATED
    assert allocation.quantity == quantity
    assert allocation.max_loss == max_loss


def test_v2_reservations_allow_exact_20_and_50_percent_boundaries_then_abstain(tmp_path) -> None:
    kernel, _ = _v2_kernel(tmp_path)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE

    approved: list[RiskApprovalV2] = []
    for event_id, underlying, opportunity_id, tier, maximum_loss in (
        ("KR-2026Q2-EARNINGS", "KR", "OPP-KR-20", RiskTier.TWENTY_PERCENT, Decimal("20000")),
        (
            "MSFT-2026Q2-EARNINGS",
            "MSFT",
            "OPP-MSFT-20",
            RiskTier.TWENTY_PERCENT,
            Decimal("20000"),
        ),
        (
            "NVDA-2026Q2-EARNINGS",
            "NVDA",
            "OPP-NVDA-10",
            RiskTier.TEN_PERCENT,
            Decimal("10000"),
        ),
    ):
        compiled = _v2_compiled(
            event_id=event_id,
            underlying=underlying,
            maximum_loss=maximum_loss,
        )
        _freeze_v2(kernel, compiled, candidate_id=f"candidate-{underlying}")
        result = _authorize_v2(
            kernel,
            compiled,
            opportunity=_v2_opportunity(compiled, opportunity_id=opportunity_id, risk_tier=tier),
            candidate_id=f"candidate-{underlying}",
        )
        assert isinstance(result, RiskApprovalV2)
        approved.append(result)

    assert sum((approval.max_loss for approval in approved), Decimal("0")) == Decimal("50000")
    assert kernel.ledger.open_reservation_total() == Decimal("50000")

    exhausted = _v2_compiled(
        event_id="AMD-2026Q2-EARNINGS",
        underlying="AMD",
        maximum_loss=Decimal("5000"),
    )
    _freeze_v2(kernel, exhausted, candidate_id="candidate-AMD")
    result = _authorize_v2(
        kernel,
        exhausted,
        opportunity=_v2_opportunity(
            exhausted,
            opportunity_id="OPP-AMD-5",
            risk_tier=RiskTier.FIVE_PERCENT,
        ),
        candidate_id="candidate-AMD",
    )

    assert isinstance(result, RiskAbstentionV2)
    assert result.reason_codes == (AbstainReason.AGGREGATE_CAP_INSUFFICIENT,)
    assert kernel.ledger.v2_reservation_for_event(exhausted.event_id) is None


def test_v2_reserved_debit_allows_exact_per_underlying_boundary_then_abstains(tmp_path) -> None:
    kernel, _ = _v2_kernel(tmp_path)
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    first = _v2_compiled(maximum_loss=Decimal("20000"))
    _freeze_v2(kernel, first)
    result = _authorize_v2(
        kernel,
        first,
        opportunity=_v2_opportunity(
            first, opportunity_id="OPP-KR-20", risk_tier=RiskTier.TWENTY_PERCENT
        ),
    )
    assert isinstance(result, RiskApprovalV2)
    assert result.max_loss == Decimal("20000")

    next_kr = _v2_compiled(
        event_id="KR-2026Q3-EARNINGS",
        maximum_loss=Decimal("5000"),
    )
    _freeze_v2(kernel, next_kr, candidate_id="candidate-KR-2")
    exhausted = _authorize_v2(
        kernel,
        next_kr,
        opportunity=_v2_opportunity(
            next_kr, opportunity_id="OPP-KR-5", risk_tier=RiskTier.FIVE_PERCENT
        ),
        candidate_id="candidate-KR-2",
    )

    assert isinstance(exhausted, RiskAbstentionV2)
    assert exhausted.reason_codes == (AbstainReason.UNDERLYING_CAP_INSUFFICIENT,)
    assert kernel.ledger.v2_reservation_for_event(next_kr.event_id) is None


def test_v2_freezes_at_exact_50_percent_drawdown_and_allows_just_above_it(tmp_path) -> None:
    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    frozen, _ = _v2_kernel(
        frozen_dir,
        account=_v2_account(equity=Decimal("50000"), cash=Decimal("50000")),
    )
    frozen_compiled = _v2_compiled(maximum_loss=Decimal("2500"))
    assert frozen.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(frozen, frozen_compiled)

    with pytest.raises(RiskRejected) as at_threshold:
        _authorize_v2(
            frozen,
            frozen_compiled,
            opportunity=_v2_opportunity(
                frozen_compiled,
                opportunity_id="OPP-KR-FROZEN",
                risk_tier=RiskTier.FIVE_PERCENT,
            ),
        )
    assert at_threshold.value.reason is RiskReason.DRAWDOWN_LIMIT_BREACHED
    assert frozen.ledger.get_control_state()[0] is ControlState.ENTRY_DISABLED
    assert frozen.ledger.v2_reservation_for_event(frozen_compiled.event_id) is None

    eligible_dir = tmp_path / "eligible"
    eligible_dir.mkdir()
    eligible, _ = _v2_kernel(
        eligible_dir,
        account=_v2_account(equity=Decimal("50000.01"), cash=Decimal("50000.01")),
    )
    eligible_compiled = _v2_compiled(maximum_loss=Decimal("2500"))
    assert eligible.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(eligible, eligible_compiled)
    result = _authorize_v2(
        eligible,
        eligible_compiled,
        opportunity=_v2_opportunity(
            eligible_compiled,
            opportunity_id="OPP-KR-ELIGIBLE",
            risk_tier=RiskTier.FIVE_PERCENT,
        ),
    )

    assert isinstance(result, RiskApprovalV2)
    assert result.max_loss == Decimal("2500")


def test_v2_ignores_buying_power_and_requires_current_cash(tmp_path) -> None:
    kernel, _ = _v2_kernel(
        tmp_path,
        account=_v2_account(cash=Decimal("5000"), buying_power=Decimal("400000")),
    )
    compiled = _v2_compiled(maximum_loss=Decimal("10000"))
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(kernel, compiled)

    result = _authorize_v2(
        kernel,
        compiled,
        opportunity=_v2_opportunity(
            compiled,
            opportunity_id="OPP-KR-BUYING-POWER",
            risk_tier=RiskTier.TEN_PERCENT,
        ),
    )

    assert isinstance(result, RiskAbstentionV2)
    assert result.reason_codes == (AbstainReason.CASH_INSUFFICIENT,)
    assert kernel.ledger.v2_reservation_for_event(compiled.event_id) is None


def test_v2_rejects_missing_cash_but_zero_cash_is_a_valid_abstention(tmp_path) -> None:
    missing_dir = tmp_path / "missing"
    missing_dir.mkdir()
    missing, _ = _v2_kernel(missing_dir, account=_v2_account(cash=None))
    compiled = _v2_compiled()
    assert missing.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(missing, compiled)

    with pytest.raises(RiskRejected) as rejected:
        _authorize_v2(missing, compiled)
    assert rejected.value.reason is RiskReason.CONTRADICTORY_TRUTH
    assert missing.ledger.v2_reservation_for_event(compiled.event_id) is None

    zero_dir = tmp_path / "zero"
    zero_dir.mkdir()
    zero, _ = _v2_kernel(zero_dir, account=_v2_account(cash=Decimal("0")))
    zero_compiled = _v2_compiled()
    assert zero.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(zero, zero_compiled)
    abstention = _authorize_v2(zero, zero_compiled)

    assert isinstance(abstention, RiskAbstentionV2)
    assert abstention.reason_codes == (AbstainReason.CASH_INSUFFICIENT,)
    assert zero.ledger.v2_reservation_for_event(zero_compiled.event_id) is None


def test_v2_reserved_rows_consume_underlying_aggregate_and_cash_capacity(tmp_path) -> None:
    kernel, _ = _v2_kernel(tmp_path, account=_v2_account(cash=Decimal("25000")))
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    first = _v2_compiled(maximum_loss=Decimal("20000"))
    _freeze_v2(kernel, first)
    approved = _authorize_v2(
        kernel,
        first,
        opportunity=_v2_opportunity(
            first, opportunity_id="OPP-KR-20", risk_tier=RiskTier.TWENTY_PERCENT
        ),
    )
    assert isinstance(approved, RiskApprovalV2)

    same_underlying = _v2_compiled(
        event_id="KR-2026Q3-EARNINGS",
        maximum_loss=Decimal("5000"),
    )
    _freeze_v2(kernel, same_underlying, candidate_id="candidate-KR-2")
    underlyer = _authorize_v2(
        kernel,
        same_underlying,
        opportunity=_v2_opportunity(
            same_underlying,
            opportunity_id="OPP-KR-5",
            risk_tier=RiskTier.FIVE_PERCENT,
        ),
        candidate_id="candidate-KR-2",
    )
    assert isinstance(underlyer, RiskAbstentionV2)
    assert underlyer.reason_codes == (AbstainReason.UNDERLYING_CAP_INSUFFICIENT,)

    cash_boundary = _v2_compiled(
        event_id="MSFT-2026Q2-EARNINGS",
        underlying="MSFT",
        maximum_loss=Decimal("5000"),
    )
    _freeze_v2(kernel, cash_boundary, candidate_id="candidate-MSFT")
    second = _authorize_v2(
        kernel,
        cash_boundary,
        opportunity=_v2_opportunity(
            cash_boundary,
            opportunity_id="OPP-MSFT-5",
            risk_tier=RiskTier.FIVE_PERCENT,
        ),
        candidate_id="candidate-MSFT",
    )
    assert isinstance(second, RiskApprovalV2)
    assert kernel.ledger.open_reservation_total() == Decimal("25000")

    no_cash = _v2_compiled(
        event_id="AMD-2026Q2-EARNINGS",
        underlying="AMD",
        maximum_loss=Decimal("5000"),
    )
    _freeze_v2(kernel, no_cash, candidate_id="candidate-AMD")
    exhausted = _authorize_v2(
        kernel,
        no_cash,
        opportunity=_v2_opportunity(
            no_cash,
            opportunity_id="OPP-AMD-5",
            risk_tier=RiskTier.FIVE_PERCENT,
        ),
        candidate_id="candidate-AMD",
    )

    assert isinstance(exhausted, RiskAbstentionV2)
    assert exhausted.reason_codes == (AbstainReason.CASH_INSUFFICIENT,)
    assert kernel.ledger.v2_reservation_for_event(no_cash.event_id) is None


def test_v2_consumed_rows_hold_debit_capacity_without_double_subtracting_broker_cash(
    tmp_path,
) -> None:
    kernel, truth = _v2_kernel(tmp_path, account=_v2_account(cash=Decimal("40000")))
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    first = _v2_compiled(maximum_loss=Decimal("20000"))
    _freeze_v2(kernel, first)
    approval = _authorize_v2(
        kernel,
        first,
        opportunity=_v2_opportunity(
            first, opportunity_id="OPP-KR-CONSUMED", risk_tier=RiskTier.TWENTY_PERCENT
        ),
    )
    assert isinstance(approval, RiskApprovalV2)
    kernel.record_submission(
        event_id=first.event_id,
        permit_id=approval.permit_id,
        broker_order_id="order-KR-1",
        now=NOW,
    )
    filled = OrderSnapshot(
        order_id="order-KR-1",
        symbol="KR",
        status="FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW,
    )
    truth.order_snapshots = (filled,)
    kernel.reconcile_fill(
        event_id=first.event_id,
        permit_id=approval.permit_id,
        fill=filled,
        now=NOW,
    )
    truth.account_snapshot = _v2_account(cash=Decimal("20000"))
    truth.position_snapshots = (
        PositionSnapshot(
            underlying="KR",
            quantity=Decimal("1"),
            market_value=Decimal("20000"),
            observed_at=NOW,
        ),
    )

    second = _v2_compiled(
        event_id="MSFT-2026Q2-EARNINGS",
        underlying="MSFT",
        maximum_loss=Decimal("20000"),
    )
    _freeze_v2(kernel, second, candidate_id="candidate-MSFT")
    result = _authorize_v2(
        kernel,
        second,
        opportunity=_v2_opportunity(
            second,
            opportunity_id="OPP-MSFT-CASH-ONCE",
            risk_tier=RiskTier.TWENTY_PERCENT,
        ),
        candidate_id="candidate-MSFT",
    )

    assert kernel.ledger.reservation_state(first.event_id) == "CONSUMED"
    assert isinstance(result, RiskApprovalV2)
    assert result.max_loss == Decimal("20000")
    assert kernel.ledger.open_reservation_total() == Decimal("40000")


def test_v2_separate_sqlite_connections_cannot_overcommit_racing_20k_opportunities(
    tmp_path,
) -> None:
    database = tmp_path / "risk.sqlite3"
    policy = load_risk_policy_v2()
    truth = V2Truth(account=_v2_account())
    bootstrap = RiskKernel(policy, RiskLedger(database), truth)
    assert bootstrap.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    specifications: list[
        tuple[CompiledExpression, DefinedRiskOpportunity, DebitVerticalPermit, str]
    ] = []
    for index, (event_id, underlying) in enumerate(
        (
            ("KR-2026Q2-EARNINGS", "KR"),
            ("KR-2026Q3-EARNINGS", "KR"),
            ("MSFT-2026Q2-EARNINGS", "MSFT"),
            ("NVDA-2026Q2-EARNINGS", "NVDA"),
        ),
        start=1,
    ):
        compiled = _v2_compiled(
            event_id=event_id,
            underlying=underlying,
            maximum_loss=Decimal("20000"),
            decision_sha256=(f"{index:x}" * 64),
        )
        candidate_id = f"candidate-{index}"
        _freeze_v2(bootstrap, compiled, candidate_id=candidate_id)
        specifications.append(
            (
                compiled,
                _v2_opportunity(
                    compiled,
                    opportunity_id=f"OPP-{underlying}-{index}",
                    risk_tier=RiskTier.TWENTY_PERCENT,
                ),
                _v2_permit(bootstrap, compiled),
                candidate_id,
            )
        )

    barrier = Barrier(len(specifications))

    def authorize(
        specification: tuple[CompiledExpression, DefinedRiskOpportunity, DebitVerticalPermit, str],
    ) -> RiskApprovalV2 | RiskAbstentionV2 | RiskRejected:
        compiled, opportunity, permit, candidate_id = specification
        ledger = RiskLedger(database)
        try:
            kernel = RiskKernel(policy, ledger, truth)
            barrier.wait(timeout=10)
            try:
                return _authorize_v2(
                    kernel,
                    compiled,
                    opportunity=opportunity,
                    candidate_id=candidate_id,
                    permit=permit,
                )
            except RiskRejected as error:
                return error
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=len(specifications)) as executor:
        results = list(executor.map(authorize, specifications))

    assert all(
        isinstance(result, (RiskApprovalV2, RiskAbstentionV2, RiskRejected)) for result in results
    )
    ledger = RiskLedger(database)
    try:
        rows = ledger.v2_open_reservation_rows()
    finally:
        ledger.close()
    aggregate = sum((Decimal(str(row["amount"])) for row in rows), Decimal("0"))
    assert aggregate <= Decimal("50000")
    assert len(rows) <= 2
    for underlying in {"KR", "MSFT", "NVDA"}:
        held = sum(
            (Decimal(str(row["amount"])) for row in rows if row["underlying"] == underlying),
            Decimal("0"),
        )
        assert held <= Decimal("20000")


def _fresh_process_v2_state(database: Path, event_id: str) -> dict[str, str]:
    project_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    source_path = str(project_root / "src")
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_path if not existing_python_path else source_path + os.pathsep + existing_python_path
    )
    script = (
        "import json, sys\n"
        "from ringdown_market.risk import RiskLedger\n"
        "ledger = RiskLedger(sys.argv[1])\n"
        "try:\n"
        "    row = ledger.v2_reservation_for_event(sys.argv[2])\n"
        "    print(json.dumps({key: str(row[key]) for key in ('state', 'amount', 'underlying')}, "
        "sort_keys=True))\n"
        "finally:\n"
        "    ledger.close()\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(database), event_id],
        capture_output=True,
        check=False,
        cwd=project_root,
        env=environment,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    return {str(key): str(value) for key, value in result.items()}


def test_v2_restart_sees_reserved_and_consumed_capacity_and_replays_exactly(tmp_path) -> None:
    database = tmp_path / "restart.sqlite3"
    policy = load_risk_policy_v2()
    truth = V2Truth(account=_v2_account())
    first = RiskKernel(policy, RiskLedger(database), truth)
    compiled = _v2_compiled()
    opportunity = _v2_opportunity(compiled)
    permit = _v2_permit(first, compiled)
    assert first.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(first, compiled)
    approval = _authorize_v2(first, compiled, opportunity=opportunity, permit=permit)
    assert isinstance(approval, RiskApprovalV2)
    first.ledger.close()

    assert _fresh_process_v2_state(database, compiled.event_id) == {
        "amount": "5000",
        "state": "RESERVED",
        "underlying": "KR",
    }

    reopened = RiskKernel(policy, RiskLedger(database), truth)
    replay = _authorize_v2(
        reopened,
        compiled,
        opportunity=opportunity,
        permit=permit,
        now=NOW + timedelta(seconds=1),
    )
    assert replay == approval
    reopened.record_submission(
        event_id=compiled.event_id,
        permit_id=approval.permit_id,
        broker_order_id="order-KR-restart",
        now=NOW + timedelta(seconds=1),
    )
    filled = OrderSnapshot(
        order_id="order-KR-restart",
        symbol="KR",
        status="FILLED",
        filled_quantity=Decimal("1"),
        observed_at=NOW + timedelta(seconds=1),
    )
    truth.account_snapshot = _v2_account(observed_at=NOW + timedelta(seconds=1))
    truth.position_snapshots = (
        PositionSnapshot(
            underlying="KR",
            quantity=Decimal("1"),
            market_value=Decimal("5000"),
            observed_at=NOW + timedelta(seconds=1),
        ),
    )
    truth.order_snapshots = (filled,)
    truth.clock = NOW + timedelta(seconds=1)
    reopened.reconcile_fill(
        event_id=compiled.event_id,
        permit_id=approval.permit_id,
        fill=filled,
        now=NOW + timedelta(seconds=1),
    )
    reopened.ledger.close()

    assert _fresh_process_v2_state(database, compiled.event_id) == {
        "amount": "5000",
        "state": "CONSUMED",
        "underlying": "KR",
    }

    consumed = RiskKernel(policy, RiskLedger(database), truth)
    with pytest.raises(RiskRejected) as consumed_replay:
        _authorize_v2(
            consumed,
            compiled,
            opportunity=opportunity,
            permit=permit,
            now=NOW + timedelta(seconds=2),
        )
    assert consumed_replay.value.reason is RiskReason.EVENT_LIFECYCLE_INVALID
    changed = _v2_permit(
        consumed,
        compiled,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    with pytest.raises(RiskRejected) as conflict:
        _authorize_v2(
            consumed,
            compiled,
            opportunity=opportunity,
            permit=changed,
            now=NOW + timedelta(seconds=2),
        )
    assert conflict.value.reason is RiskReason.DUPLICATE_EVENT_RESERVATION
    consumed.ledger.close()


@pytest.mark.parametrize(
    "mismatch",
    (
        "event",
        "permit_event",
        "decision",
        "expression",
        "underlying",
        "quantity",
        "max_loss",
        "unready",
    ),
)
def test_v2_rejects_mismatched_or_unready_identity_without_a_reservation(
    tmp_path, mismatch: str
) -> None:
    kernel, _ = _v2_kernel(tmp_path)
    compiled = _v2_compiled()
    if mismatch == "quantity":
        block = dict(compiled.debit_vertical or {})
        block["quantity"] = 2
        compiled = replace(compiled, debit_vertical=block)
    opportunity = _v2_opportunity(compiled)
    if mismatch == "decision":
        opportunity = replace(opportunity, decision_id="f" * 64)
    elif mismatch == "expression":
        opportunity = replace(opportunity, expression_id="f" * 64)
    elif mismatch == "underlying":
        opportunity = replace(opportunity, underlying="MSFT")
    elif mismatch == "quantity":
        opportunity = replace(opportunity, max_debit_per_contract=Decimal("2500"))
    elif mismatch == "unready":
        opportunity = replace(opportunity, decision_ready=False)
    permit = _v2_permit(kernel, compiled)
    event_id = compiled.event_id
    if mismatch == "event":
        event_id = "MSFT-2026Q2-EARNINGS"
    elif mismatch == "permit_event":
        candidate = replace(permit, event_run_id="MSFT-2026Q2-EARNINGS")
        permit = replace(candidate, permit_id=debit_vertical_permit_id(candidate))
    elif mismatch == "max_loss":
        candidate = replace(permit, limit_price=Decimal("40"))
        permit = replace(candidate, permit_id=debit_vertical_permit_id(candidate))
    assert kernel.startup_reconciliation(now=NOW) is ControlState.ACTIVE
    _freeze_v2(kernel, compiled)

    with pytest.raises(RiskRejected):
        kernel.authorize_entry_v2(
            event_id=event_id,
            candidate_id="candidate-1",
            opportunity=opportunity,
            compiled=compiled,
            permit=permit,
            now=NOW,
        )

    assert kernel.ledger.v2_reservation_for_event(compiled.event_id) is None
    assert kernel.ledger.v2_open_reservation_rows() == []
