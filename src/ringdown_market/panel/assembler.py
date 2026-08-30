"""Deterministic fail-closed compiler for Q-FAST point-in-time panel reports."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime

from ..alpha.baselines import BaselineName
from ..alpha.evaluation import MissingPricePoint
from ..alpha.models import (
    DecisionSnapshot,
    Direction,
    EventCase,
    MarketPath,
    PointInTimeViolation,
    PricePoint,
)
from ..alpha.qfast import CANDIDATE_METHOD
from ..cli import build_report
from .manifest import (
    PanelRejected,
    PanelRejectionReason,
    ValidatedPanelManifest,
    _decode,
    _reject,
    _sha256,
    _strict_object,
    _text,
    _text_list,
    validate_panel_manifest,
)

PANEL_BUNDLE_SCHEMA = "ringdown.qfast_panel_bundle"
PANEL_REPORT_SCHEMA = "ringdown.qfast_panel_report"
PANEL_PROTOCOL_SCHEMA = "ringdown.qfast_panel_protocol"

_BUNDLE_FIELDS = frozenset(
    {
        "schema",
        "schema_version",
        "fixture_class",
        "limitations",
        "panel_manifest_sha256",
        "events",
    }
)
_EVENT_FIELDS = frozenset({"decision", "path"})
_DECISION_FIELDS = frozenset(
    {
        "event_id",
        "issuer",
        "decision_cutoff",
        "latest_evidence_at",
        "feature_snapshot_at",
        "opening_return",
        "market_opening_return",
        "sector_opening_return",
        "market_beta",
        "sector_beta",
        "price_only_score",
        "fundamental_score",
        "numeric_score",
        "candidate_signal",
    }
)
_PATH_POINT_FIELDS = frozenset({"at", "stock", "market", "sector"})


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_datetime(value: object, *, path: str) -> datetime:
    if not isinstance(value, str):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, "must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        _reject(
            PanelRejectionReason.INVALID_DOCUMENT,
            path,
            "must be a valid ISO-8601 timestamp",
        )
        raise AssertionError("unreachable") from error


def _parse_event_case(raw: object, *, path: str) -> EventCase:
    event = _strict_object(raw, path=path, fields=_EVENT_FIELDS)
    decision = _strict_object(event["decision"], path=f"{path}.decision", fields=_DECISION_FIELDS)
    path_raw = event["path"]
    if not isinstance(path_raw, list) or not path_raw:
        _reject(
            PanelRejectionReason.INVALID_DOCUMENT,
            f"{path}.path",
            "must be a non-empty list of synchronized price points",
        )
    try:
        snapshot = DecisionSnapshot(
            event_id=_text(decision["event_id"], path=f"{path}.decision.event_id"),
            issuer=_text(decision["issuer"], path=f"{path}.decision.issuer"),
            decision_cutoff=_parse_datetime(
                decision["decision_cutoff"], path=f"{path}.decision.decision_cutoff"
            ),
            latest_evidence_at=_parse_datetime(
                decision["latest_evidence_at"], path=f"{path}.decision.latest_evidence_at"
            ),
            feature_snapshot_at=_parse_datetime(
                decision["feature_snapshot_at"], path=f"{path}.decision.feature_snapshot_at"
            ),
            opening_return=float(decision["opening_return"]),
            market_opening_return=float(decision["market_opening_return"]),
            sector_opening_return=float(decision["sector_opening_return"]),
            market_beta=float(decision["market_beta"]),
            sector_beta=float(decision["sector_beta"]),
            price_only_score=float(decision["price_only_score"]),
            fundamental_score=float(decision["fundamental_score"]),
            numeric_score=float(decision["numeric_score"]),
            candidate_signal=Direction(str(decision["candidate_signal"])),
        )
        points = []
        for index, point_raw in enumerate(path_raw):
            point = _strict_object(
                point_raw, path=f"{path}.path[{index}]", fields=_PATH_POINT_FIELDS
            )
            points.append(
                PricePoint(
                    at=_parse_datetime(point["at"], path=f"{path}.path[{index}].at"),
                    stock=float(point["stock"]),
                    market=float(point["market"]),
                    sector=float(point["sector"]),
                )
            )
        return EventCase(decision=snapshot, path=MarketPath(tuple(points)))
    except PointInTimeViolation as error:
        _reject(
            PanelRejectionReason.POINT_IN_TIME_VIOLATION,
            path,
            f"decision uses information after its cutoff: {error}",
        )
    except (KeyError, TypeError, ValueError) as error:
        _reject(PanelRejectionReason.INVALID_DOCUMENT, path, str(error))
    raise AssertionError("unreachable")


def _validate_bundle(
    bundle_bytes: bytes,
    manifest: ValidatedPanelManifest,
) -> tuple[Mapping[str, object], tuple[EventCase, ...]]:
    bundle = _strict_object(
        _decode(bundle_bytes, path="panel_bundle"),
        path="panel_bundle",
        fields=_BUNDLE_FIELDS,
    )
    if bundle["schema"] != PANEL_BUNDLE_SCHEMA or bundle["schema_version"] != 1:
        _reject(
            PanelRejectionReason.UNSUPPORTED_SCHEMA,
            "panel_bundle",
            "unsupported panel bundle schema or version",
        )
    if bundle["fixture_class"] != manifest.data_class:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            "panel_bundle.fixture_class",
            "bundle data class differs from the frozen panel manifest",
        )
    limitations = _text_list(bundle["limitations"], path="panel_bundle.limitations")
    if limitations != manifest.limitations:
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            "panel_bundle.limitations",
            "bundle limitations differ from the frozen panel manifest",
        )
    bound_sha = _sha256(bundle["panel_manifest_sha256"], path="panel_bundle.panel_manifest_sha256")
    if bound_sha != manifest.panel_manifest_sha256:
        _reject(
            PanelRejectionReason.HASH_MISMATCH,
            "panel_bundle.panel_manifest_sha256",
            "bundle is not bound to the supplied panel-manifest bytes",
        )
    events_raw = bundle["events"]
    if not isinstance(events_raw, list):
        _reject(PanelRejectionReason.INVALID_DOCUMENT, "panel_bundle.events", "must be a list")
    if len(events_raw) != len(manifest.eligible_event_ids):
        _reject(
            PanelRejectionReason.IDENTITY_MISMATCH,
            "panel_bundle.events",
            "bundle events must match the frozen eligible universe one-for-one",
        )
    cases: list[EventCase] = []
    for index, raw_event in enumerate(events_raw):
        case = _parse_event_case(raw_event, path=f"panel_bundle.events[{index}]")
        expected_id = manifest.eligible_event_ids[index]
        if case.decision.event_id != expected_id:
            _reject(
                PanelRejectionReason.IDENTITY_MISMATCH,
                f"panel_bundle.events[{index}].decision.event_id",
                f"bundle order differs from the frozen universe (expected {expected_id})",
            )
        cases.append(case)
    return bundle, tuple(cases)


def assemble_panel_report(
    manifest_bytes: bytes,
    selection_rule_bytes: bytes,
    bundle_bytes: bytes,
) -> bytes:
    """Compile one deterministic panel report from exact frozen input bytes."""

    manifest = validate_panel_manifest(manifest_bytes, selection_rule_bytes)
    bundle, cases = _validate_bundle(bundle_bytes, manifest)

    payload = {
        "fixture_class": manifest.data_class,
        "limitations": list(manifest.limitations),
        "spec": {
            "hold_seconds": manifest.hold_seconds,
            "minimum_events": manifest.minimum_events,
            "required_latency_profile": manifest.required_latency_profile,
            "latency_profiles": dict(manifest.latency_profiles),
        },
        "events": list(bundle["events"]),
    }
    try:
        evaluation_report = build_report(_canonical_json(payload))
    except PointInTimeViolation as error:
        _reject(
            PanelRejectionReason.POINT_IN_TIME_VIOLATION,
            "panel_bundle.events",
            str(error),
        )
    except MissingPricePoint as error:
        _reject(
            PanelRejectionReason.MISSING_PRICE_POINT,
            "panel_bundle.events",
            str(error),
        )
    except ValueError as error:
        _reject(PanelRejectionReason.INVALID_DOCUMENT, "panel_bundle", str(error))

    protocol = {
        "schema": PANEL_PROTOCOL_SCHEMA,
        "schema_version": 1,
        "hold_seconds": manifest.hold_seconds,
        "required_latency_profile": manifest.required_latency_profile,
        "latency_profiles": dict(manifest.latency_profiles),
        "minimum_events": manifest.minimum_events,
        "candidate_method": CANDIDATE_METHOD,
        "baselines": sorted(name.value for name in BaselineName),
    }
    abstained_events = sum(
        1 for case in cases if case.decision.candidate_signal is Direction.UNCERTAIN
    )
    report = {
        "schema": PANEL_REPORT_SCHEMA,
        "schema_version": 1,
        "product_name": "Esscher",
        "mode": "OFFLINE_RESEARCH",
        "data_class": manifest.data_class,
        "claims": ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"],
        "limitations": sorted(manifest.limitations),
        "panel_id": manifest.panel_id,
        "panel_manifest_sha256": manifest.panel_manifest_sha256,
        "selection_rule_sha256": manifest.selection_rule_sha256,
        "strategy_policy_sha256": manifest.strategy_policy_sha256,
        "snapshot_protocol_sha256": manifest.snapshot_protocol_sha256,
        "decision_protocol_sha256": manifest.decision_protocol_sha256,
        "input_sha256": _sha256_bytes(bundle_bytes),
        "protocol_sha256": _sha256_bytes(_canonical_json(protocol)),
        "eligible_event_count": len(cases),
        "abstained_events": abstained_events,
        "excluded_events": [asdict(excluded) for excluded in manifest.excluded_events],
        "evaluation_report": evaluation_report,
    }
    return (
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")


__all__ = [
    "PANEL_BUNDLE_SCHEMA",
    "PANEL_PROTOCOL_SCHEMA",
    "PANEL_REPORT_SCHEMA",
    "PanelRejected",
    "assemble_panel_report",
]
