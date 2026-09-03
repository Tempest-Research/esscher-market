"""Command-line entry point for deterministic research and one-shot PAPER runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
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


def _canonical_cli_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _autonomous_rejection_bytes(
    *,
    error_code: str,
    release_bytes: bytes | None,
    arm_record_bytes: bytes | None,
    pre_host_failure: bool,
) -> bytes:
    """Return one sanitized rejection without overstating post-host broker state."""

    if pre_host_failure:
        broker_mutation = "NOT_ATTEMPTED"
        claims = ["NO_BROKER_EXECUTION", "NO_CREDENTIALS"]
        disposition = "REJECTED"
    else:
        broker_mutation = "UNKNOWN"
        claims = ["FAIL_CLOSED", "OPERATOR_INTERVENTION_REQUIRED"]
        disposition = "MANUAL_RECONCILIATION_REQUIRED"

    unsigned = {
        "arm_record_input_sha256": (
            None if arm_record_bytes is None else _sha256(arm_record_bytes)
        ),
        "broker_mutation": broker_mutation,
        "claims": claims,
        "disposition": disposition,
        "error_code": error_code,
        "release_input_sha256": None if release_bytes is None else _sha256(release_bytes),
        "schema": "esscher.autonomous_host_cli_rejection",
        "schema_version": 1,
    }
    return _canonical_json(
        {
            **unsigned,
            "receipt_sha256": _sha256(_canonical_json(unsigned)),
        }
    )


def _print_autonomous_rejection(
    *,
    error_code: str,
    release_bytes: bytes | None,
    arm_record_bytes: bytes | None,
    pre_host_failure: bool,
) -> int:
    print(
        _autonomous_rejection_bytes(
            error_code=error_code,
            release_bytes=release_bytes,
            arm_record_bytes=arm_record_bytes,
            pre_host_failure=pre_host_failure,
        ).decode("utf-8")
    )
    return 2 if pre_host_failure else 3


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
    autonomous = subparsers.add_parser(
        "run-autonomous-session",
        help="rehearse one exact armed autonomous PAPER session with synthetic host ports",
    )
    autonomous.add_argument("--release", type=Path, required=True)
    autonomous.add_argument("--arm", type=Path, required=True)
    autonomous.add_argument("--state-dir", type=Path, required=True)
    autonomous.add_argument(
        "--host-plan",
        required=True,
        help="host-owned module:function returning an AutonomousHostInvocation",
    )
    preflight = subparsers.add_parser(
        "paper-preflight",
        help="run the read-only PAPER broker preflight and emit a NO_BROKER_MUTATION receipt",
    )
    preflight.add_argument(
        "--host-session",
        required=True,
        help="host-owned module:function returning the raw MCP client session",
    )
    preflight.add_argument("--receipt-id", required=True)
    preflight.add_argument("--expected-account-id", required=True)
    preflight.add_argument("--expected-account-fingerprint", required=True)
    preflight.add_argument("--starting-equity", required=True)
    preflight.add_argument("--account-capability-id", required=True)
    preflight.add_argument("--runtime-code-revision", required=True)
    preflight.add_argument("--runtime-build-sha256", required=True)
    preflight.add_argument("--route-config-sha256", default=None)
    preflight.add_argument("--latency-profile-sha256", default=None)
    preflight.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "receipt path (default: artifacts/paper-preflight/<receipt-id>/preflight-receipt.json)"
        ),
    )
    paper_run = subparsers.add_parser(
        "paper-run",
        help="run one armed production PAPER_MCP session under the wall-clock scheduler",
    )
    paper_run.add_argument("--release", type=Path, required=True)
    paper_run.add_argument("--arm", type=Path, required=True)
    paper_run.add_argument("--state-dir", type=Path, required=True)
    paper_run.add_argument("--ledger", type=Path, required=True)
    paper_run.add_argument("--output-dir", type=Path, required=True)
    paper_run.add_argument(
        "--host-invocation",
        required=True,
        help="host-owned module:function returning a PaperSessionInvocation",
    )
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


def _load_autonomous_invocation_factory(reference: str) -> Callable[..., object]:
    if reference.count(":") != 1:
        raise ValueError("autonomous host-plan selector is invalid")
    module_name, attribute = reference.split(":", 1)
    if (
        not module_name
        or not attribute
        or module_name != module_name.strip()
        or attribute != attribute.strip()
    ):
        raise ValueError("autonomous host-plan selector is invalid")
    try:
        factory = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError):
        raise ValueError("autonomous host-plan selector is unavailable") from None
    if not callable(factory):
        raise ValueError("autonomous host-plan target is not callable")
    return factory


def _normalized_cli_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


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
    if args.command == "run-autonomous-session":
        release_bytes: bytes | None = None
        arm_record_bytes: bytes | None = None
        try:
            from .contracts.strategy_release import (
                parse_arm_record,
                parse_strategy_release,
            )

            release_bytes = args.release.read_bytes()
            arm_record_bytes = args.arm.read_bytes()
            parse_strategy_release(release_bytes)
            parse_arm_record(arm_record_bytes)
        except (OSError, TypeError, ValueError):
            return _print_autonomous_rejection(
                error_code="AUTHORITY_INPUT_REJECTED",
                release_bytes=release_bytes,
                arm_record_bytes=arm_record_bytes,
                pre_host_failure=True,
            )

        try:
            from .runtime.autonomous_host import (
                AutonomousHostBusy,
                AutonomousHostDisposition,
                AutonomousHostInvocation,
                AutonomousHostReceipt,
                AutonomousHostRejected,
                run_autonomous_host_invocation,
            )
        except ImportError:
            return _print_autonomous_rejection(
                error_code="AUTONOMOUS_HOST_UNAVAILABLE",
                release_bytes=release_bytes,
                arm_record_bytes=arm_record_bytes,
                pre_host_failure=True,
            )

        host_error_code: str | None = None
        receipt: AutonomousHostReceipt | None = None
        receipt_bytes: bytes | None = None
        with (
            open(os.devnull, "w", encoding="utf-8") as suppressed_output,
            redirect_stdout(suppressed_output),
            redirect_stderr(suppressed_output),
        ):
            try:
                invocation_factory = _load_autonomous_invocation_factory(args.host_plan)
                invocation = invocation_factory(
                    release_bytes=release_bytes,
                    arm_record_bytes=arm_record_bytes,
                    state_dir=args.state_dir,
                )
                if not isinstance(invocation, AutonomousHostInvocation):
                    raise ValueError("autonomous host plan did not return an invocation")
                if (
                    invocation.authority_input.release_bytes != release_bytes
                    or invocation.authority_input.arm_record_bytes != arm_record_bytes
                    or _normalized_cli_path(invocation.authority_input.state_dir)
                    != _normalized_cli_path(args.state_dir)
                ):
                    raise ValueError("autonomous host invocation substituted CLI authority")
                receipt = run_autonomous_host_invocation(invocation)
                if not isinstance(receipt, AutonomousHostReceipt):
                    raise ValueError("autonomous host runner returned an invalid receipt")
                receipt_bytes = receipt.to_json_bytes()
            except AutonomousHostBusy:
                host_error_code = "AUTONOMOUS_HOST_BUSY"
            except AutonomousHostRejected:
                host_error_code = "AUTONOMOUS_HOST_REJECTED"
            except Exception:
                host_error_code = "HOST_PLAN_REJECTED"

        if host_error_code is not None or receipt is None or receipt_bytes is None:
            return _print_autonomous_rejection(
                error_code=host_error_code or "HOST_PLAN_REJECTED",
                release_bytes=release_bytes,
                arm_record_bytes=arm_record_bytes,
                pre_host_failure=False,
            )

        print(receipt_bytes.decode("utf-8"))
        if receipt.disposition is AutonomousHostDisposition.TERMINAL:
            return 0
        if receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED:
            return 3
        return 4
    if args.command == "paper-preflight":
        runtime_clock = (lambda: datetime.now(UTC)) if clock is None else clock
        from .runtime.paper_preflight import (
            BrokerPreflightExpectations,
            PaperPreflightRejected,
            run_broker_preflight,
        )

        try:
            expectations = BrokerPreflightExpectations(
                account_id=args.expected_account_id,
                account_fingerprint_sha256=args.expected_account_fingerprint,
                starting_equity_contract=Decimal(args.starting_equity),
                account_capability_id=args.account_capability_id,
                runtime_code_revision=args.runtime_code_revision,
                runtime_build_artifact_sha256=args.runtime_build_sha256,
                route_config_sha256=args.route_config_sha256,
                latency_profile_sha256=args.latency_profile_sha256,
            )
        except (PaperPreflightRejected, InvalidOperation, ValueError):
            print(
                _canonical_cli_json(
                    {
                        "schema": "esscher.paper_preflight_cli_rejection",
                        "schema_version": 1,
                        "error_code": "EXPECTATIONS_INVALID",
                        "claims": ["NO_BROKER_MUTATION"],
                    }
                )
            )
            return 2

        preflight_error_code: str | None = None
        preflight_receipt = None
        with (
            open(os.devnull, "w", encoding="utf-8") as suppressed_output,
            redirect_stdout(suppressed_output),
            redirect_stderr(suppressed_output),
        ):
            try:
                from .contracts.broker_preflight import (
                    PreflightVerdict,
                    broker_preflight_receipt_bytes,
                )
                from .execution.host_mcp import (
                    HostMcpEnvironment,
                    HostMcpPaperSessionFactory,
                    HostMcpSessionIdentity,
                )

                session_selector = _load_autonomous_invocation_factory(args.host_session)
                host_session = session_selector()
                prepared = asyncio.run(
                    HostMcpPaperSessionFactory(
                        HostMcpSessionIdentity(environment=HostMcpEnvironment.PAPER),
                        clock=runtime_clock,
                    ).connect(host_session)
                )
                preflight_receipt = asyncio.run(
                    run_broker_preflight(
                        prepared,
                        expectations=expectations,
                        receipt_id=args.receipt_id,
                        clock=runtime_clock,
                    )
                )
            except Exception:
                preflight_error_code = "PREFLIGHT_HOST_REJECTED"

        if preflight_error_code is not None or preflight_receipt is None:
            print(
                _canonical_cli_json(
                    {
                        "schema": "esscher.paper_preflight_cli_rejection",
                        "schema_version": 1,
                        "error_code": preflight_error_code or "PREFLIGHT_HOST_REJECTED",
                        "claims": ["NO_BROKER_MUTATION"],
                    }
                )
            )
            return 3

        from .contracts.broker_preflight import (
            PreflightVerdict,
            broker_preflight_receipt_bytes,
        )
        from .runtime.paper_preflight import preflight_receipt_artifact_path

        output_path = (
            args.output
            if args.output is not None
            else preflight_receipt_artifact_path(
                Path("artifacts") / "paper-preflight", args.receipt_id
            )
        )
        receipt_bytes = broker_preflight_receipt_bytes(preflight_receipt)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(receipt_bytes)
        print(receipt_bytes.decode("utf-8"))
        return 0 if preflight_receipt.verdict is PreflightVerdict.PASSED else 2
    if args.command == "paper-run":
        release_bytes = None
        arm_record_bytes = None
        try:
            from .contracts.strategy_release import parse_arm_record, parse_strategy_release

            release_bytes = args.release.read_bytes()
            arm_record_bytes = args.arm.read_bytes()
            parse_strategy_release(release_bytes)
            parse_arm_record(arm_record_bytes)
        except (OSError, TypeError, ValueError):
            return _print_autonomous_rejection(
                error_code="AUTHORITY_INPUT_REJECTED",
                release_bytes=release_bytes,
                arm_record_bytes=arm_record_bytes,
                pre_host_failure=True,
            )

        run_error_code: str | None = None
        run_receipt = None
        run_receipt_bytes: bytes | None = None
        with (
            open(os.devnull, "w", encoding="utf-8") as suppressed_output,
            redirect_stdout(suppressed_output),
            redirect_stderr(suppressed_output),
        ):
            try:
                import time as time_module

                from .runtime.autonomous_host import (
                    AutonomousHostBusy,
                    AutonomousHostDisposition,
                    validate_autonomous_host_authority,
                )
                from .runtime.paper_mcp_composition import paper_mcp_plan_factory
                from .runtime.paper_scheduler import (
                    PaperSessionInvocation,
                    run_paper_session,
                    session_observation_timeline,
                )

                invocation_factory = _load_autonomous_invocation_factory(args.host_invocation)
                invocation = invocation_factory(
                    release_bytes=release_bytes,
                    arm_record_bytes=arm_record_bytes,
                    state_dir=args.state_dir,
                    ledger_path=args.ledger,
                    output_dir=args.output_dir,
                )
                if not isinstance(invocation, PaperSessionInvocation):
                    raise ValueError("paper-run selector did not return a PaperSessionInvocation")
                if (
                    invocation.authority_input.release_bytes != release_bytes
                    or invocation.authority_input.arm_record_bytes != arm_record_bytes
                    or _normalized_cli_path(invocation.authority_input.state_dir)
                    != _normalized_cli_path(args.state_dir)
                    or _normalized_cli_path(invocation.ledger_path)
                    != _normalized_cli_path(args.ledger)
                ):
                    raise ValueError("paper-run invocation substituted CLI authority")
                authority = validate_autonomous_host_authority(invocation.authority_input)
                timeline = session_observation_timeline(authority.session_arm)
                scheduler_clock = invocation.scheduler_clock or (lambda: datetime.now(UTC))
                scheduler_sleep = invocation.scheduler_sleep or time_module.sleep
                run_receipt = run_paper_session(
                    authority_input=invocation.authority_input,
                    plan_factory=paper_mcp_plan_factory(invocation.doors),
                    timeline=timeline,
                    clock=scheduler_clock,
                    sleep=scheduler_sleep,
                )
                run_receipt_bytes = run_receipt.to_json_bytes()
                args.output_dir.mkdir(parents=True, exist_ok=True)
                (args.output_dir / "paper-run-receipt.json").write_bytes(run_receipt_bytes)
            except AutonomousHostBusy:
                run_error_code = "AUTONOMOUS_HOST_BUSY"
            except Exception:
                run_error_code = "PAPER_RUN_REJECTED"

        if run_error_code is not None or run_receipt is None or run_receipt_bytes is None:
            return _print_autonomous_rejection(
                error_code=run_error_code or "PAPER_RUN_REJECTED",
                release_bytes=release_bytes,
                arm_record_bytes=arm_record_bytes,
                pre_host_failure=False,
            )

        from .runtime.autonomous_host import AutonomousHostDisposition

        print(run_receipt_bytes.decode("utf-8"))
        if run_receipt.disposition is AutonomousHostDisposition.TERMINAL:
            return 0
        if run_receipt.disposition is AutonomousHostDisposition.MANUAL_RECONCILIATION_REQUIRED:
            return 3
        return 4
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
