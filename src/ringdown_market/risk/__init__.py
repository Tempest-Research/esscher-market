"""PAPER account risk kernel: evidence-backed budgets with durable reservations.

The risk kernel authorizes nothing by default. Strategy and expression
compilation cannot bypass it: a permit is issued only after the kernel approves
an expression against verified account, portfolio, clock, quote, policy, and
lifecycle truth. It is permanently Alpaca PAPER-only and never places orders,
touches real money, or promotes policy.
"""

from ringdown_market.risk.controls import (
    ControlTrigger,
    close_allowed,
    entry_allowed,
    next_control_state,
)
from ringdown_market.risk.exposure import (
    aggregate_exposure,
    concentration_exposure,
    expression_exposure,
)
from ringdown_market.risk.kernel import (
    RiskAbstentionV2,
    RiskAllocationPreviewV2,
    RiskApproval,
    RiskApprovalV2,
    RiskKernel,
)
from ringdown_market.risk.ledger import SCHEMA_VERSION, RiskLedger, V2ReservationReceipt
from ringdown_market.risk.passport import (
    GENESIS_SHA256,
    PassportEventType,
    passport_event_sha256,
    verify_passport,
)
from ringdown_market.risk.policy import (
    RISK_POLICY_SCHEMA,
    RISK_POLICY_SCHEMA_VERSION,
    RISK_POLICY_V2_RESOURCE_NAME,
    RISK_POLICY_V2_SCHEMA,
    RISK_POLICY_V2_SCHEMA_VERSION,
    RiskPolicy,
    RiskPolicyV2,
    load_risk_policy_v2,
    parse_risk_policy,
    parse_risk_policy_v2,
    risk_policy_bytes,
    risk_policy_payload,
    risk_policy_sha256,
    risk_policy_v2_bytes,
    risk_policy_v2_payload,
    risk_policy_v2_sha256,
)
from ringdown_market.risk.reasons import (
    ControlState,
    RiskReason,
    RiskRejected,
)
from ringdown_market.risk.snapshots import (
    AccountSnapshot,
    AccountTruthSource,
    OrderSnapshot,
    PositionSnapshot,
    validate_account_freshness,
    validate_orders_freshness,
    validate_positions_freshness,
)

__all__ = [
    "GENESIS_SHA256",
    "RISK_POLICY_SCHEMA",
    "RISK_POLICY_SCHEMA_VERSION",
    "RISK_POLICY_V2_RESOURCE_NAME",
    "RISK_POLICY_V2_SCHEMA",
    "RISK_POLICY_V2_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "AccountSnapshot",
    "AccountTruthSource",
    "ControlState",
    "ControlTrigger",
    "OrderSnapshot",
    "PassportEventType",
    "PositionSnapshot",
    "RiskAbstentionV2",
    "RiskAllocationPreviewV2",
    "RiskApproval",
    "RiskApprovalV2",
    "RiskKernel",
    "RiskLedger",
    "RiskPolicy",
    "RiskPolicyV2",
    "RiskReason",
    "RiskRejected",
    "V2ReservationReceipt",
    "aggregate_exposure",
    "close_allowed",
    "concentration_exposure",
    "entry_allowed",
    "expression_exposure",
    "load_risk_policy_v2",
    "next_control_state",
    "parse_risk_policy",
    "parse_risk_policy_v2",
    "passport_event_sha256",
    "risk_policy_bytes",
    "risk_policy_payload",
    "risk_policy_sha256",
    "risk_policy_v2_bytes",
    "risk_policy_v2_payload",
    "risk_policy_v2_sha256",
    "validate_account_freshness",
    "validate_orders_freshness",
    "validate_positions_freshness",
    "verify_passport",
]
