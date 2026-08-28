"""Command-line entry point for deterministic offline evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .alpha.baselines import build_frozen_baselines
from .alpha.evaluation import evaluate_event
from .alpha.models import DecisionSnapshot, Direction, EventCase, MarketPath, PricePoint
from .alpha.qfast import (
    CANDIDATE_METHOD,
    PanelRow,
    QFastReport,
    evaluate_latency_gate,
    run_qfast,
)

ALLOWED_DATA_CLASSES = {
    "SYNTHETIC_CONTRACT_FIXTURE",
    "POINT_IN_TIME_EVENT_PANEL",
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO-8601 timestamp") from exc


def _parse_event(raw: Mapping[str, Any]) -> EventCase:
    decision_raw = raw.get("decision")
    path_raw = raw.get("path")
    if not isinstance(decision_raw, Mapping) or not isinstance(path_raw, list):
        raise ValueError("each event requires decision and path objects")

    decision = DecisionSnapshot(
        event_id=str(decision_raw["event_id"]),
        issuer=str(decision_raw["issuer"]),
        decision_cutoff=_parse_datetime(decision_raw["decision_cutoff"], "decision_cutoff"),
        latest_evidence_at=_parse_datetime(
            decision_raw["latest_evidence_at"], "latest_evidence_at"
        ),
        feature_snapshot_at=_parse_datetime(
            decision_raw["feature_snapshot_at"], "feature_snapshot_at"
        ),
        opening_return=float(decision_raw["opening_return"]),
        market_opening_return=float(decision_raw["market_opening_return"]),
        sector_opening_return=float(decision_raw["sector_opening_return"]),
        market_beta=float(decision_raw["market_beta"]),
        sector_beta=float(decision_raw["sector_beta"]),
        price_only_score=float(decision_raw["price_only_score"]),
        fundamental_score=float(decision_raw["fundamental_score"]),
        numeric_score=float(decision_raw["numeric_score"]),
        candidate_signal=Direction(str(decision_raw["candidate_signal"])),
    )
    points = tuple(
        PricePoint(
            at=_parse_datetime(point["at"], "price point at"),
            stock=float(point["stock"]),
            market=float(point["market"]),
            sector=float(point["sector"]),
        )
        for point in path_raw
    )
    return EventCase(decision=decision, path=MarketPath(points))


def _serialize_qfast(report: QFastReport) -> dict[str, object]:
    return {
        "status": report.status.value,
        "claim": report.claim,
        "event_count": report.event_count,
        "metrics": {method: asdict(metrics) for method, metrics in sorted(report.metrics.items())},
        "strongest_baseline": report.strongest_baseline,
        "candidate_advantage": report.candidate_advantage,
        "leave_best_out_mean": report.leave_best_out_mean,
        "reject_reasons": list(report.reject_reasons),
    }


def _evaluate_profile(
    events: Sequence[EventCase],
    *,
    latency_ms: int,
    hold_seconds: int,
    minimum_events: int,
) -> tuple[QFastReport, list[int]]:
    rows: list[PanelRow] = []
    actual_latencies: list[int] = []
    for event in events:
        methods = {CANDIDATE_METHOD: event.decision.candidate_signal}
        methods.update(
            {name.value: signal for name, signal in build_frozen_baselines(event.decision).items()}
        )
        evaluations = {
            method: evaluate_event(
                event,
                signal,
                latency_ms=latency_ms,
                hold_seconds=hold_seconds,
            )
            for method, signal in methods.items()
        }
        actual_latencies.append(evaluations[CANDIDATE_METHOD].actual_latency_ms)
        rows.append(
            PanelRow(
                event_id=event.decision.event_id,
                signed_returns={
                    method: evaluation.signed_residual for method, evaluation in evaluations.items()
                },
                admitted={
                    method: evaluation.admitted for method, evaluation in evaluations.items()
                },
            )
        )
    return run_qfast(rows, minimum_events=minimum_events), actual_latencies


def build_report(raw_bytes: bytes) -> dict[str, object]:
    """Build a deterministic offline report from one frozen input payload."""

    payload = json.loads(raw_bytes)
    if not isinstance(payload, dict):
        raise ValueError("input root must be an object")
    spec = payload.get("spec")
    events_raw = payload.get("events")
    if not isinstance(spec, dict) or not isinstance(events_raw, list):
        raise ValueError("input requires spec and events")

    data_class = payload.get("fixture_class")
    if data_class not in ALLOWED_DATA_CLASSES:
        raise ValueError("input requires an explicit supported data class")

    hold_seconds = int(spec["hold_seconds"])
    minimum_events = int(spec["minimum_events"])
    if data_class == "POINT_IN_TIME_EVENT_PANEL" and minimum_events < 20:
        raise ValueError("point-in-time panels require at least 20 events")
    required_profile = str(spec["required_latency_profile"])
    latency_raw = spec["latency_profiles"]
    if not isinstance(latency_raw, dict) or not latency_raw:
        raise ValueError("latency_profiles must be a non-empty object")
    latency_profiles = {str(name): int(value) for name, value in latency_raw.items()}
    events = [_parse_event(event) for event in events_raw]

    qfast_profiles: dict[str, QFastReport] = {}
    profile_payloads: dict[str, object] = {}
    for name, latency_ms in sorted(latency_profiles.items()):
        qfast, actual_latencies = _evaluate_profile(
            events,
            latency_ms=latency_ms,
            hold_seconds=hold_seconds,
            minimum_events=minimum_events,
        )
        qfast_profiles[name] = qfast
        profile_payloads[name] = {
            "requested_latency_ms": latency_ms,
            "actual_latency_ms": {
                "minimum": min(actual_latencies) if actual_latencies else None,
                "maximum": max(actual_latencies) if actual_latencies else None,
            },
            "qfast": _serialize_qfast(qfast),
        }

    latency_gate = evaluate_latency_gate(
        qfast_profiles,
        required_profile=required_profile,
    )
    limitations = payload.get("limitations", [])
    if not isinstance(limitations, list) or not all(
        isinstance(value, str) for value in limitations
    ):
        raise ValueError("limitations must be a list of strings")

    return {
        "schema_version": 1,
        "project": "Ringdown",
        "mode": "OFFLINE_RESEARCH",
        "data_class": data_class,
        "claims": ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"],
        "limitations": sorted(limitations),
        "input_sha256": _sha256(raw_bytes),
        "protocol_sha256": _sha256(_canonical_json(spec)),
        "event_count": len(events),
        "latency_profiles": profile_payloads,
        "latency_gate": {
            "status": latency_gate.status.value,
            "required_profile": latency_gate.required_profile,
            "qfast_status": latency_gate.qfast_status.value,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ringdown")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate", help="evaluate a frozen event panel")
    evaluate.add_argument("--input", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command != "evaluate":
        raise AssertionError("argparse accepted an unknown command")
    raw_bytes = args.input.read_bytes()
    report = build_report(raw_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return 0
