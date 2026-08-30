"""Frozen account-level PAPER risk limits for the $100,000 competition account."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

RISK_POLICY_SCHEMA = "esscher.paper_risk_policy"
RISK_POLICY_SCHEMA_VERSION = 1
RISK_POLICY_VERSION = "paper-account-kernel/v1"
COMPETITION_START_EQUITY = Decimal("100000.00")

RISK_POLICY = {
    "schema": RISK_POLICY_SCHEMA,
    "schema_version": RISK_POLICY_SCHEMA_VERSION,
    "policy_version": RISK_POLICY_VERSION,
    "run_mode": "PAPER",
    "start_equity_usd": "100000.00",
    "maximum_packages": 1,
    "maximum_open_packages": 1,
    "maximum_loss_per_trade_usd": "500.00",
    "maximum_loss_per_trade_pct": "0.50",
    "daily_loss_budget_usd": "1000.00",
    "daily_loss_budget_pct": "1.00",
    "entry_disable_drawdown_usd": "2000.00",
    "entry_disable_drawdown_pct": "2.00",
    "hard_kill_drawdown_usd": "3000.00",
    "hard_kill_drawdown_pct": "3.00",
    "maximum_new_entries_per_day": 2,
    "maximum_new_entries_per_period": 5,
    "tolerances": {
        "duplicate_event_or_package": 0,
        "naked_short_exposure": 0,
        "market_opening_orders": 0,
    },
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


RISK_POLICY_SHA256 = hashlib.sha256(_canonical_json_bytes(RISK_POLICY)).hexdigest()

_PERCENT = Decimal("100")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Effective Decimal-only limits derived from one start equity."""

    start_equity: Decimal
    maximum_loss_per_trade: Decimal
    daily_loss_budget: Decimal
    entry_disable_drawdown: Decimal
    hard_kill_drawdown: Decimal
    maximum_open_packages: int
    maximum_new_entries_per_day: int
    maximum_new_entries_per_period: int

    def __post_init__(self) -> None:
        for field in (
            "start_equity",
            "maximum_loss_per_trade",
            "daily_loss_budget",
            "entry_disable_drawdown",
            "hard_kill_drawdown",
        ):
            value = getattr(self, field)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be a positive finite Decimal")
        if self.entry_disable_drawdown >= self.hard_kill_drawdown:
            raise ValueError("entry-disable drawdown must sit below hard-kill drawdown")


def _min_of(absolute: Decimal, percent: Decimal, start_equity: Decimal) -> Decimal:
    return min(absolute, (start_equity * percent / _PERCENT).quantize(Decimal("0.01")))


def build_frozen_limits(start_equity: Decimal = COMPETITION_START_EQUITY) -> RiskLimits:
    """Build the frozen limits for one start equity; all arithmetic stays Decimal."""

    if not isinstance(start_equity, Decimal) or not start_equity.is_finite():
        raise ValueError("start_equity must be a finite Decimal")
    if start_equity <= 0:
        raise ValueError("start_equity must be positive")
    return RiskLimits(
        start_equity=start_equity,
        maximum_loss_per_trade=_min_of(
            Decimal(RISK_POLICY["maximum_loss_per_trade_usd"]),
            Decimal(RISK_POLICY["maximum_loss_per_trade_pct"]),
            start_equity,
        ),
        daily_loss_budget=_min_of(
            Decimal(RISK_POLICY["daily_loss_budget_usd"]),
            Decimal(RISK_POLICY["daily_loss_budget_pct"]),
            start_equity,
        ),
        entry_disable_drawdown=_min_of(
            Decimal(RISK_POLICY["entry_disable_drawdown_usd"]),
            Decimal(RISK_POLICY["entry_disable_drawdown_pct"]),
            start_equity,
        ),
        hard_kill_drawdown=_min_of(
            Decimal(RISK_POLICY["hard_kill_drawdown_usd"]),
            Decimal(RISK_POLICY["hard_kill_drawdown_pct"]),
            start_equity,
        ),
        maximum_open_packages=int(RISK_POLICY["maximum_open_packages"]),
        maximum_new_entries_per_day=int(RISK_POLICY["maximum_new_entries_per_day"]),
        maximum_new_entries_per_period=int(RISK_POLICY["maximum_new_entries_per_period"]),
    )
