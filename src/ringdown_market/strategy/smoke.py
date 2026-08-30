"""Offline route-smoke harness.

Runs an injected reasoner route against a frozen strategy input and records
latency (from supplied exchange timestamps, never a wall clock) and schema
outcomes.  The harness imports no broker, account, order, or network
capability and cannot mutate anything; it only observes the route receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ringdown_market.strategy.contracts import (
    StrategyContractRejected,
    canonical_json_bytes,
    parse_reasoner_decision,
    reasoner_exchange_payload,
    sha256_bytes,
)
from ringdown_market.strategy.models import ExchangeStatus, StrategyInput
from ringdown_market.strategy.reasoner import ReasonerRoute, ReasonerRouteRequest

_REPORT_SCHEMA: Final = "esscher.route_smoke_report"
_REPORT_SCHEMA_VERSION: Final = 1
SMOKE_BUILD_SHA256: Final = sha256_bytes(
    canonical_json_bytes(
        {
            "producer": "esscher.strategy.route_smoke",
            "contract": _REPORT_SCHEMA,
            "version": _REPORT_SCHEMA_VERSION,
        }
    )
)


@dataclass(frozen=True, slots=True)
class RouteSmokeReport:
    """Deterministic latency/schema record for one route invocation."""

    event_id: str
    candidate_id: str
    status: ExchangeStatus
    error_code: str | None
    schema_ok: bool
    latency_ms: int | None
    reasoner_exchange_sha256: str
    producer_build_sha256: str

    @property
    def payload(self) -> dict[str, object]:
        return {
            "schema": _REPORT_SCHEMA,
            "schema_version": _REPORT_SCHEMA_VERSION,
            "event_id": self.event_id,
            "candidate_id": self.candidate_id,
            "status": self.status.value,
            "error_code": self.error_code,
            "schema_ok": self.schema_ok,
            "latency_ms": self.latency_ms,
            "reasoner_exchange_sha256": self.reasoner_exchange_sha256,
            "producer_build_sha256": self.producer_build_sha256,
        }

    @property
    def bytes(self) -> bytes:
        return canonical_json_bytes(self.payload)


def run_route_smoke(
    route: ReasonerRoute,
    strategy_input: StrategyInput,
    *,
    started_at: datetime,
    ablate_text: bool = False,
) -> RouteSmokeReport:
    """Invoke the route once and record latency/schema outcomes only."""

    result = route(
        ReasonerRouteRequest(
            strategy_input=strategy_input,
            started_at=started_at,
            ablate_text=ablate_text,
        )
    )
    exchange = result.exchange
    schema_ok = False
    if result.raw_response_bytes is not None:
        try:
            parse_reasoner_decision(result.raw_response_bytes)
            schema_ok = True
        except StrategyContractRejected:
            schema_ok = False
    latency_ms = None
    if exchange.responded_at is not None:
        latency_ms = int((exchange.responded_at - exchange.started_at).total_seconds() * 1000)
    return RouteSmokeReport(
        event_id=exchange.event_id,
        candidate_id=exchange.candidate_id,
        status=exchange.status,
        error_code=exchange.error_code,
        schema_ok=schema_ok,
        latency_ms=latency_ms,
        reasoner_exchange_sha256=sha256_bytes(
            canonical_json_bytes(reasoner_exchange_payload(exchange))
        ),
        producer_build_sha256=SMOKE_BUILD_SHA256,
    )
