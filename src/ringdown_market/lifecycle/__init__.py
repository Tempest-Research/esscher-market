"""Monitored PAPER lifecycle for the frozen strategy exit.

The lifecycle worker drives one risk-approved promoted expression through its
frozen exit plan: opening submission, holding, time-exit, deterministic close,
and broker-confirmed flatness. It reuses the risk ledger and Trade Passport,
keeps actual PAPER mutation blocked behind the later approval gate, and makes
zero real MCP/broker calls in tests. The immediate-close demonstration and the
earlier fixed 60-minute hold are not preserved as production policy.
"""

from ringdown_market.lifecycle.broker import (
    AccountTruth,
    BrokerOrderAck,
    BrokerOrderState,
    BrokerOutage,
    FakePaperBroker,
    PaperBroker,
    PositionTruth,
    ensure_no_residue,
)
from ringdown_market.lifecycle.clocks import (
    LIFECYCLE_CLOCKS_SCHEMA,
    LIFECYCLE_CLOCKS_SCHEMA_VERSION,
    LifecycleClocks,
    lifecycle_clocks_bytes,
    lifecycle_clocks_payload,
    lifecycle_clocks_sha256,
    parse_lifecycle_clocks,
)
from ringdown_market.lifecycle.correlation import (
    CorrelationIdentity,
    correlation_payload,
    correlation_sha256,
)
from ringdown_market.lifecycle.reasons import (
    LifecycleReason,
    LifecycleRejected,
    LifecycleState,
)
from ringdown_market.lifecycle.reducer import (
    opening_exposure_bearing,
    positions_flat,
    reduce_close_order,
    reduce_open_order,
)
from ringdown_market.lifecycle.states import (
    LifecycleTrigger,
    close_permitted,
    entry_permitted,
    is_terminal,
    next_lifecycle_state,
    require_transition,
)
from ringdown_market.lifecycle.worker import (
    ClosedMutationGate,
    LifecycleResult,
    MonitoredPaperLifecycle,
    MutationGate,
    issue_close_permit,
)

__all__ = [
    "LIFECYCLE_CLOCKS_SCHEMA",
    "LIFECYCLE_CLOCKS_SCHEMA_VERSION",
    "AccountTruth",
    "BrokerOrderAck",
    "BrokerOrderState",
    "BrokerOutage",
    "ClosedMutationGate",
    "CorrelationIdentity",
    "FakePaperBroker",
    "LifecycleClocks",
    "LifecycleReason",
    "LifecycleRejected",
    "LifecycleResult",
    "LifecycleState",
    "LifecycleTrigger",
    "MonitoredPaperLifecycle",
    "MutationGate",
    "PaperBroker",
    "PositionTruth",
    "close_permitted",
    "correlation_payload",
    "correlation_sha256",
    "ensure_no_residue",
    "entry_permitted",
    "is_terminal",
    "issue_close_permit",
    "lifecycle_clocks_bytes",
    "lifecycle_clocks_payload",
    "lifecycle_clocks_sha256",
    "next_lifecycle_state",
    "opening_exposure_bearing",
    "parse_lifecycle_clocks",
    "positions_flat",
    "reduce_close_order",
    "reduce_open_order",
    "require_transition",
]
