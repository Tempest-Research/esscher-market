"""Attributable Trade Passport: append-only hash-linked lifecycle trace."""

from ringdown_market.passport.chain import (
    GENESIS_PREV_SHA256,
    PASSPORT_SCHEMA,
    PASSPORT_SCHEMA_VERSION,
    PassportChainError,
    PassportEntry,
    PassportStage,
    TradePassport,
    compute_entry_sha256,
    parse_passport_bytes,
)
from ringdown_market.passport.slice import (
    SLICE_NOW,
    SLICE_ROUTE,
    SliceInputs,
    SliceRejected,
    build_offline_causal_slice,
    slice_payload_json,
)
from ringdown_market.passport.verifier import (
    FULL_TRACE_STAGES,
    VerdictReason,
    VerificationResult,
    verify_passport,
)

__all__ = [
    "FULL_TRACE_STAGES",
    "GENESIS_PREV_SHA256",
    "PASSPORT_SCHEMA",
    "PASSPORT_SCHEMA_VERSION",
    "SLICE_NOW",
    "SLICE_ROUTE",
    "PassportChainError",
    "PassportEntry",
    "PassportStage",
    "SliceInputs",
    "SliceRejected",
    "TradePassport",
    "VerdictReason",
    "VerificationResult",
    "build_offline_causal_slice",
    "compute_entry_sha256",
    "parse_passport_bytes",
    "slice_payload_json",
    "verify_passport",
]
