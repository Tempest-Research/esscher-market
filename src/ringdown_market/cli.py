"""Command-line entry point for deterministic research and one-shot PAPER runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import version
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
from .demo.judge_trace import load_packaged_trace_inputs, render_judge_trace
from .runtime.scheduled import (
    ScheduledEventManifest,
    ScheduledEventOverlap,
    ScheduledManifestRejected,
    ScheduledManualReconciliationRequired,
    ScheduledRunError,
    run_scheduled_event_command,
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
        "product_name": "Esscher",
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
    parser = argparse.ArgumentParser(
        prog="ringdown",
        description=(
            "Esscher's permanently paper-only scheduled-earnings research command. "
            "The ringdown command name remains a compatibility interface."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('ringdown-market')}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate", help="evaluate a frozen event panel")
    evaluate.add_argument("--input", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    panel = subparsers.add_parser(
        "assemble-panel",
        help="compile one frozen Q-FAST point-in-time panel report",
    )
    panel.add_argument("--manifest", type=Path, required=True)
    panel.add_argument("--selection-rule", type=Path, required=True)
    panel.add_argument("--bundle", type=Path, required=True)
    panel.add_argument("--output", type=Path, required=True)
    scheduled = subparsers.add_parser(
        "run-scheduled-event",
        help="run at most one approved scheduled PAPER event, resume from durable state, then exit",
        description=(
            "Run at most one approved scheduled PAPER event. Preserve the exact manifest, "
            "host plan, and durable state directory with its contents unchanged to resume "
            "the same event after an interruption, including from another environment."
        ),
    )
    scheduled.add_argument("--manifest", type=Path, required=True)
    scheduled.add_argument(
        "--state-dir",
        type=Path,
        required=True,
        help=(
            "durable state directory whose contents must be preserved unchanged across "
            "restarts or environment handoff"
        ),
    )
    scheduled.add_argument(
        "--host-plan",
        required=True,
        help="host-owned module:function returning PaperDemoPlan",
    )
    scheduled.add_argument("--dry-run", action="store_true")
    trace = subparsers.add_parser(
        "render-judge-trace",
        help="render the packaged offline read-only evidence-to-receipt walkthrough",
    )
    trace.add_argument("--output", type=Path, required=True)
    return parser


def _load_plan_factory(reference: str) -> Callable[[], object]:
    if reference.count(":") != 1:
        raise ScheduledManifestRejected("scheduled host-plan selector is invalid")
    module_name, attribute = reference.split(":", 1)
    if (
        not module_name
        or not attribute
        or module_name != module_name.strip()
        or attribute != attribute.strip()
    ):
        raise ScheduledManifestRejected("scheduled host-plan selector is invalid")
    try:
        factory = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError):
        raise ScheduledManifestRejected("scheduled host-plan selector is unavailable") from None
    if not callable(factory):
        raise ScheduledManifestRejected("scheduled host-plan target is not callable")
    return factory


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "assemble-panel":
        from .panel.assembler import PanelRejected, assemble_panel_report

        try:
            report_bytes = assemble_panel_report(
                args.manifest.read_bytes(),
                args.selection_rule.read_bytes(),
                args.bundle.read_bytes(),
            )
        except PanelRejected as error:
            print(str(error), file=sys.stderr)
            return 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(report_bytes)
        return 0
    if args.command == "evaluate":
        raw_bytes = args.input.read_bytes()
        report = build_report(raw_bytes)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return 0
    if args.command == "render-judge-trace":
        rendered = render_judge_trace(load_packaged_trace_inputs())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
        return 0
    if args.command == "run-scheduled-event":
        runtime_clock = (lambda: datetime.now(UTC)) if clock is None else clock
        manifest_bytes = args.manifest.read_bytes()
        try:
            result = asyncio.run(
                run_scheduled_event_command(
                    manifest_bytes=manifest_bytes,
                    state_dir=args.state_dir,
                    plan_factory=lambda: _load_plan_factory(args.host_plan)(),
                    dry_run=args.dry_run,
                    clock=runtime_clock,
                )
            )
        except ScheduledManifestRejected:
            error = ScheduledRunError(
                event_run_id=None,
                manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                disposition="REJECTED_BEFORE_MUTATION",
                lifecycle="REJECTED",
                error_code="MANIFEST_OR_STATE_REJECTED",
                broker_mutation="NOT_ATTEMPTED",
                observed_at=runtime_clock(),
            )
            print(error.to_json_bytes().decode("utf-8"))
            return 2
        except (ScheduledManualReconciliationRequired, ScheduledEventOverlap) as caught:
            manifest = ScheduledEventManifest.from_json_bytes(manifest_bytes)
            is_manual = isinstance(caught, ScheduledManualReconciliationRequired)
            error = ScheduledRunError(
                event_run_id=manifest.event_run_id,
                manifest_sha256=manifest.manifest_sha256,
                disposition=(
                    "MANUAL_RECONCILIATION_REQUIRED" if is_manual else "OVERLAPPING_EVENT_REJECTED"
                ),
                lifecycle="MANUAL_RECONCILIATION" if is_manual else "REJECTED",
                error_code=(caught.error_code if is_manual else "OVERLAPPING_ACTIVE_EVENT"),
                broker_mutation="NO_FURTHER_MUTATION" if is_manual else "NOT_ATTEMPTED",
                observed_at=runtime_clock(),
            )
            print(error.to_json_bytes().decode("utf-8"))
            return 3 if is_manual else 4
        print(result.to_json_bytes().decode("utf-8"))
        return 0
    raise AssertionError("argparse accepted an unknown command")
