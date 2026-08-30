"""Deterministic verifier for the append-only Trade Passport chain.

The verifier proves issue #33's acceptance properties without trusting any
single artifact: hash linkage, the head anchor, frozen stage order, causal
bindings between parent and child stages, and the frozen policy identities
bound through the whole trace.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ringdown_market.risk.policy import RISK_POLICY_SHA256
from ringdown_market.strategy.policy import STRATEGY_POLICY_V1_SHA256

from .chain import (
    GENESIS_PREV_SHA256,
    PASSPORT_SCHEMA,
    PassportEntry,
    PassportStage,
    compute_entry_sha256,
    parse_passport_bytes,
)

FULL_TRACE_STAGES = (
    PassportStage.SOURCE_EVIDENCE,
    PassportStage.SNAPSHOT,
    PassportStage.DECISION,
    PassportStage.PACKAGE,
    PassportStage.RISK_RESERVATION,
    PassportStage.PERMIT,
    PassportStage.OPEN_SUBMISSION,
    PassportStage.OPEN_FILL,
    PassportStage.HOLD,
    PassportStage.CLOSE_SUBMISSION,
    PassportStage.CLOSE_FILL,
    PassportStage.FINAL_FLAT_RECONCILIATION,
    PassportStage.RESULT,
)

_REQUIRED_PAYLOAD_FIELDS: dict[PassportStage, tuple[str, ...]] = {
    PassportStage.SOURCE_EVIDENCE: ("evidence_ids", "source_sha256s"),
    PassportStage.SNAPSHOT: ("snapshot_sha256", "strategy_policy_sha256", "event_id"),
    PassportStage.DECISION: (
        "decision_sha256",
        "snapshot_sha256",
        "policy_sha256",
        "reasoner_output_sha256",
        "direction",
    ),
    PassportStage.PACKAGE: ("package_sha256", "decision_sha256"),
    PassportStage.RISK_RESERVATION: (
        "reservation_id",
        "package_sha256",
        "risk_policy_sha256",
        "permit_binding",
    ),
    PassportStage.PERMIT: ("permit_binding", "decision_sha256", "package_sha256"),
    PassportStage.OPEN_SUBMISSION: ("client_order_id", "permit_binding"),
    PassportStage.OPEN_FILL: ("client_order_id", "filled"),
    PassportStage.HOLD: ("opened_at", "close_due_at", "hold_minutes"),
    PassportStage.CLOSE_SUBMISSION: ("client_order_id", "opened_at"),
    PassportStage.CLOSE_FILL: ("client_order_id", "filled"),
    PassportStage.FINAL_FLAT_RECONCILIATION: ("flat_observed", "position_symbols"),
    PassportStage.RESULT: ("classification", "claims"),
}

_VALID_RESULT_CLASSIFICATIONS = ("PAPER_REALIZED_PNL", "PAPER_PNL_UNAVAILABLE")
_REQUIRED_RESULT_CLAIMS = ("PAPER_OPERATIONAL_RESULT", "NOT_ALPHA_EVIDENCE")


class VerdictReason(StrEnum):
    """Stable reasons a passport fails independent verification."""

    EMPTY_PASSPORT = "EMPTY_PASSPORT"
    SEQUENCE_BROKEN = "SEQUENCE_BROKEN"
    GENESIS_BROKEN = "GENESIS_BROKEN"
    LINKAGE_BROKEN = "LINKAGE_BROKEN"
    HEAD_MISMATCH = "HEAD_MISMATCH"
    STAGE_ORDER_BROKEN = "STAGE_ORDER_BROKEN"
    STAGE_DUPLICATED = "STAGE_DUPLICATED"
    MISSING_STAGE = "MISSING_STAGE"
    PAYLOAD_FIELD_MISSING = "PAYLOAD_FIELD_MISSING"
    POLICY_BINDING_BROKEN = "POLICY_BINDING_BROKEN"
    CAUSAL_BINDING_BROKEN = "CAUSAL_BINDING_BROKEN"
    RESULT_CLAIM_BROKEN = "RESULT_CLAIM_BROKEN"
    FLATNESS_UNPROVEN = "FLATNESS_UNPROVEN"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    valid: bool
    reasons: tuple[VerdictReason, ...]
    details: tuple[str, ...]
    verified_stages: tuple[str, ...]


def _payload_of(
    entries: dict[PassportStage, PassportEntry], stage: PassportStage
) -> Mapping[str, object]:
    return entries[stage].payload


def verify_passport(raw: bytes) -> VerificationResult:
    """Independently verify canonical passport bytes; every check is deterministic."""

    try:
        entries = parse_passport_bytes(raw)
    except Exception as error:
        return VerificationResult(
            valid=False,
            reasons=(VerdictReason.EMPTY_PASSPORT,),
            details=(str(error),),
            verified_stages=(),
        )
    if not entries:
        return VerificationResult(
            valid=False,
            reasons=(VerdictReason.EMPTY_PASSPORT,),
            details=("passport carries no entries",),
            verified_stages=(),
        )

    reasons: list[VerdictReason] = []
    details: list[str] = []

    try:
        document = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        document = {}
    declared_head = document.get("head_sha256") if isinstance(document, Mapping) else None
    declared_schema = document.get("schema") if isinstance(document, Mapping) else None
    if declared_schema != PASSPORT_SCHEMA:
        reasons.append(VerdictReason.HEAD_MISMATCH)
        details.append("passport document schema is not supported")

    for index, entry in enumerate(entries):
        if entry.sequence != index:
            reasons.append(VerdictReason.SEQUENCE_BROKEN)
            details.append(f"entry {index} declares sequence {entry.sequence}")
    if entries[0].prev_sha256 != GENESIS_PREV_SHA256:
        reasons.append(VerdictReason.GENESIS_BROKEN)
        details.append("first entry does not reference the genesis hash")
    for index in range(1, len(entries)):
        expected_prev = compute_entry_sha256(
            sequence=entries[index - 1].sequence,
            stage=entries[index - 1].stage,
            at=entries[index - 1].at,
            payload=entries[index - 1].payload,
            prev_sha256=entries[index - 1].prev_sha256,
        )
        if entries[index].prev_sha256 != expected_prev:
            reasons.append(VerdictReason.LINKAGE_BROKEN)
            details.append(f"entry {index} is not linked to entry {index - 1}")
    if declared_head != entries[-1].entry_sha256:
        reasons.append(VerdictReason.HEAD_MISMATCH)
        details.append("the declared head hash does not match the final entry")

    stages = [entry.stage for entry in entries]
    if tuple(stages) != FULL_TRACE_STAGES:
        seen = set(stages)
        expected = set(FULL_TRACE_STAGES)
        if len(seen) != len(stages):
            reasons.append(VerdictReason.STAGE_DUPLICATED)
        if seen - expected:
            reasons.append(VerdictReason.STAGE_ORDER_BROKEN)
            details.append(f"unexpected stages: {sorted(s.value for s in seen - expected)}")
        if expected - seen:
            reasons.append(VerdictReason.MISSING_STAGE)
            details.append(f"missing stages: {sorted(s.value for s in expected - seen)}")
        if seen == expected and len(stages) == len(expected):
            reasons.append(VerdictReason.STAGE_ORDER_BROKEN)
            details.append("stages appear out of frozen causal order")
        return VerificationResult(
            valid=False,
            reasons=tuple(dict.fromkeys(reasons)),
            details=tuple(details),
            verified_stages=tuple(s.value for s in stages),
        )

    by_stage = {entry.stage: entry for entry in entries}

    for stage, fields in _REQUIRED_PAYLOAD_FIELDS.items():
        payload = _payload_of(by_stage, stage)
        for field in fields:
            if field not in payload:
                reasons.append(VerdictReason.PAYLOAD_FIELD_MISSING)
                details.append(f"{stage.value} is missing payload field {field}")

    snapshot = _payload_of(by_stage, PassportStage.SNAPSHOT)
    decision = _payload_of(by_stage, PassportStage.DECISION)
    package = _payload_of(by_stage, PassportStage.PACKAGE)
    risk = _payload_of(by_stage, PassportStage.RISK_RESERVATION)
    permit = _payload_of(by_stage, PassportStage.PERMIT)
    open_submission = _payload_of(by_stage, PassportStage.OPEN_SUBMISSION)
    open_fill = _payload_of(by_stage, PassportStage.OPEN_FILL)
    close_submission = _payload_of(by_stage, PassportStage.CLOSE_SUBMISSION)
    close_fill = _payload_of(by_stage, PassportStage.CLOSE_FILL)
    reconciliation = _payload_of(by_stage, PassportStage.FINAL_FLAT_RECONCILIATION)
    result = _payload_of(by_stage, PassportStage.RESULT)

    if snapshot.get("strategy_policy_sha256") != STRATEGY_POLICY_V1_SHA256:
        reasons.append(VerdictReason.POLICY_BINDING_BROKEN)
        details.append("snapshot is not bound to the frozen strategy policy")
    if decision.get("policy_sha256") != STRATEGY_POLICY_V1_SHA256:
        reasons.append(VerdictReason.POLICY_BINDING_BROKEN)
        details.append("decision is not bound to the frozen strategy policy")
    if risk.get("risk_policy_sha256") != RISK_POLICY_SHA256:
        reasons.append(VerdictReason.POLICY_BINDING_BROKEN)
        details.append("risk reservation is not bound to the frozen risk policy")
    if decision.get("direction") not in ("UP", "DOWN"):
        reasons.append(VerdictReason.CAUSAL_BINDING_BROKEN)
        details.append("a traded passport requires a directional strategy decision")

    bindings = (
        (decision, "snapshot_sha256", snapshot, "snapshot_sha256", "decision->snapshot"),
        (package, "decision_sha256", decision, "decision_sha256", "package->decision"),
        (risk, "package_sha256", package, "package_sha256", "risk->package"),
        (permit, "decision_sha256", decision, "decision_sha256", "permit->decision"),
        (permit, "package_sha256", package, "package_sha256", "permit->package"),
        (permit, "permit_binding", risk, "permit_binding", "permit->reservation"),
        (open_submission, "permit_binding", risk, "permit_binding", "open->reservation"),
        (close_submission, "opened_at", open_fill, "opened_at", "close->open_fill"),
    )
    for left, left_field, right, right_field, label in bindings:
        if left.get(left_field) != right.get(right_field):
            reasons.append(VerdictReason.CAUSAL_BINDING_BROKEN)
            details.append(f"causal binding broken: {label}")

    if open_submission.get("client_order_id") != open_fill.get("client_order_id"):
        reasons.append(VerdictReason.CAUSAL_BINDING_BROKEN)
        details.append("open fill does not match the open submission client order id")
    if not open_fill.get("filled"):
        reasons.append(VerdictReason.CAUSAL_BINDING_BROKEN)
        details.append("open fill entry does not record a fill")
    if close_fill.get("client_order_id") != close_submission.get("client_order_id"):
        reasons.append(VerdictReason.CAUSAL_BINDING_BROKEN)
        details.append("close fill does not match the close submission client order id")
    if not close_fill.get("filled"):
        reasons.append(VerdictReason.CAUSAL_BINDING_BROKEN)
        details.append("close fill entry does not record a fill")

    if reconciliation.get("flat_observed") is not True:
        reasons.append(VerdictReason.FLATNESS_UNPROVEN)
        details.append("final reconciliation does not observe a flat position state")
    position_symbols = reconciliation.get("position_symbols")
    if not isinstance(position_symbols, Sequence) or isinstance(position_symbols, (str, bytes)):
        reasons.append(VerdictReason.FLATNESS_UNPROVEN)
        details.append("final reconciliation must carry observed position symbols")
    elif len(position_symbols) != 0:
        reasons.append(VerdictReason.FLATNESS_UNPROVEN)
        details.append("final position truth still contains symbols")

    classification = result.get("classification")
    if classification not in _VALID_RESULT_CLASSIFICATIONS:
        reasons.append(VerdictReason.RESULT_CLAIM_BROKEN)
        details.append("result classification must be exact fill economics or explicit unavailable")
    claims = result.get("claims")
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        reasons.append(VerdictReason.RESULT_CLAIM_BROKEN)
        details.append("result must carry explicit claims")
    else:
        for required_claim in _REQUIRED_RESULT_CLAIMS:
            if required_claim not in claims:
                reasons.append(VerdictReason.RESULT_CLAIM_BROKEN)
                details.append(f"result is missing required claim {required_claim}")

    deduped_reasons = tuple(dict.fromkeys(reasons))
    return VerificationResult(
        valid=not deduped_reasons,
        reasons=deduped_reasons,
        details=tuple(details),
        verified_stages=tuple(stage.value for stage in FULL_TRACE_STAGES)
        if not deduped_reasons
        else (),
    )
