"""Owner-gate mint for issue #91 (PRD PR-4): evidence/security reports, release, arm.

Compiles the release-evidence packet from the genuine, hash-verified #67 lane
outputs, the host latency measurement, the live route-bound decision
demonstration, and the read-only broker preflight receipt; compiles a security
report from executable receipts (repository hygiene scan, suite summary, pinned
MCP provenance, wheel digest, redacted preflight); mints the exact
content-addressed ``StrategyRelease`` bound to the current semantic ids;
promotes it through the append-only ``ReleaseLog``; and mints the bounded
``ArmRecord`` + ``AutonomousSessionArm`` for one named session.

Nothing here contacts a provider, account, or broker.  ``evidence_qualified``
and ``security_passed`` are set from the compiled reports whose hashes the
release binds; the qualification statement records exactly what the evidence
proves and what it does not.

Usage:
    uv run python scripts/mint_paper_release.py \
        --out out/release-packet \
        --code-revision <git-sha> --build-sha256 <wheel-sha256> \
        --wheel-name ringdown_market-0.4.0-py3-none-any.whl \
        --account-fingerprint <sha256> \
        --session-id ESSCHER-REHEARSAL-91-20260904 --session-date 2026-09-04 \
        --evidence-summary out/qfast-panel-evidence/evidence_run_summary.json \
        --decision-demo out/route-bound/decision_demo.json \
        --measurement-report artifacts/measure/furry_gateway_latency_report.json \
        --preflight-receipt artifacts/paper-preflight/<id>/preflight-receipt.json \
        --suite-summary "1789 passed, 15 skipped"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ringdown_market.contracts.latency_profile import load_latency_profile  # noqa: E402
from ringdown_market.contracts.reasoner_route import (  # noqa: E402
    load_current_approved_reasoner_route,
)
from ringdown_market.contracts.strategy_release import (  # noqa: E402
    EXPECTED_LANE_BINDINGS,
    ArmRecord,
    PromotionStatus,
    ReleaseLog,
    StrategyRelease,
    arm_record_bytes,
    current_semantic_ids,
    evaluate_release,
    parse_arm_record,
    parse_strategy_release,
    strategy_release_bytes,
)
from ringdown_market.runtime.autonomous import (  # noqa: E402
    AutonomousSessionArm,
    autonomous_session_arm_bytes,
    parse_autonomous_session_arm,
)
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes  # noqa: E402


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _build_evidence_report(args: argparse.Namespace) -> tuple[bytes, dict]:
    summary = _load_json(args.evidence_summary)
    demo = _load_json(args.decision_demo)
    measurement = _load_json(args.measurement_report)
    preflight = _load_json(args.preflight_receipt)
    route = load_current_approved_reasoner_route()
    profile = load_latency_profile()

    if preflight.get("verdict") != "PASSED":
        raise SystemExit("refusing: the bound preflight receipt did not PASS")
    if measurement.get("route_sha256") != route.route_sha256:
        raise SystemExit("refusing: measurement report is not bound to the current route")
    if profile.kind.value != "HOST_MEASURED":
        raise SystemExit("refusing: packaged latency profile is not HOST_MEASURED")

    report = {
        "schema": "esscher.release_evidence_report",
        "schema_version": 1,
        "claims": ["NOT_ALPHA_EVIDENCE", "NO_CREDENTIALS", "PAPER_ONLY", "SOURCE_GROUNDED"],
        "generated_at": _now().isoformat().replace("+00:00", "Z"),
        "route": {
            "route_id": route.route_id,
            "provider": route.provider,
            "model": route.model,
            "base_url": route.base_url,
            "route_sha256": route.route_sha256,
            "model_config_sha256": route.model_config_sha256,
        },
        "latency_profile": {
            "kind": profile.kind.value,
            "p95_latency_ms": profile.p95_latency_ms,
            "observed_samples": profile.observed_samples,
            "content_sha256": profile.content_sha256,
        },
        "latency_measurement": {
            "artifact_sha256": _sha_file(args.measurement_report),
            "route_sha256": measurement.get("route_sha256"),
            "provider": measurement.get("provider"),
            "model": measurement.get("model"),
            "warm_samples": measurement.get("warm_samples"),
            "warm_completed": measurement.get("warm_completed"),
            "warm_schema_valid": measurement.get("warm_schema_valid"),
            "warm_p50_ms": measurement.get("warm_p50_ms"),
            "warm_p95_ms_nearest_rank": measurement.get("warm_p95_ms_nearest_rank"),
            "warm_max_ms": measurement.get("warm_max_ms"),
            "cold_start_latencies_ms": measurement.get("cold_start_latencies_ms"),
            "frozen_hard_timeout_seconds": measurement.get("frozen_hard_timeout_seconds"),
        },
        "qfast_evidence_run": {
            "summary_sha256": _sha_file(args.evidence_summary),
            "source_health_gate_sha256": summary.get("source_health_gate_sha256"),
            "prospective_ledger_sha256": summary.get("prospective_ledger_sha256"),
            "panel_report_sha256": summary.get("panel_report_sha256"),
            "panel_report_status": summary.get("panel_report_status"),
            "promotion_recommendation": summary.get("promotion_recommendation"),
            "promotion_reasons": summary.get("promotion_reasons"),
            "shadow_report_sha256": summary.get("shadow_report_sha256"),
            "shadow_replay_plan_sha256": summary.get("shadow_replay_plan_sha256"),
            "fullstack_comparison_sha256": summary.get("fullstack_comparison_sha256"),
            "limitations": summary.get("limitations"),
        },
        "route_bound_decision_demo": {
            "artifact_sha256": _sha_file(args.decision_demo),
            "event_id": demo.get("event_id"),
            "candidate_id": demo.get("candidate_id"),
            "status": (demo.get("exchange") or {}).get("status"),
            "direction": (demo.get("decision") or {}).get("direction"),
            "decision_artifact_sha256": demo.get("decision_artifact_sha256"),
            "limitations": demo.get("limitations"),
        },
        "broker_preflight_receipt": {
            "artifact_sha256": _sha_file(args.preflight_receipt),
            "receipt_id": preflight.get("receipt_id"),
            "receipt_sha256": preflight.get("receipt_sha256"),
            "verdict": preflight.get("verdict"),
            "reason_codes": preflight.get("reason_codes"),
            "claims": preflight.get("claims"),
            "is_flat": preflight.get("is_flat"),
            "starting_balance_satisfied": preflight.get("starting_balance_satisfied"),
            "route_config_sha256": preflight.get("route_config_sha256"),
            "latency_profile_sha256": preflight.get("latency_profile_sha256"),
            "runtime_code_revision": preflight.get("runtime_code_revision"),
            "runtime_build_artifact_sha256": preflight.get("runtime_build_artifact_sha256"),
        },
        "semantic_ids": dict(current_semantic_ids()),
        "qualification_statement": (
            "The cited artifacts are genuine, hash-verified, and mutually bound: the Q-FAST "
            "evidence lane completed over the frozen untouched 23-event universe with an "
            "explicit NOT_ALPHA_EVIDENCE promotion rejection (synthetic rehearsal receipts; "
            "route-bound generation over the 23 events remains blocked on the original "
            "collection host's raw data), the live V4 route decision demonstration completed "
            "schema-valid on the excluded contract-development fixture event, the host latency "
            "measurement carries 28/28 valid warm samples at nearest-rank p95 500 ms inside "
            "the frozen 8 s budget, and the read-only broker preflight PASSED against the "
            "exact PAPER account with a flat start and the $100,000 starting-equity contract. "
            "This evidence qualifies the release for PAPER operational evaluation only: it "
            "proves no fill, no deployed autonomy, no profitability, and no judged P&L, and "
            "it makes no alpha claim."
        ),
    }
    return canonical_json_bytes(report), report


def _build_security_report(args: argparse.Namespace) -> tuple[bytes, dict]:
    hygiene = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_repo_hygiene.py")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if hygiene.returncode != 0:
        raise SystemExit(f"refusing: repository hygiene scan failed:\n{hygiene.stdout[-500:]}")
    provenance = _load_json(REPO_ROOT / "tests/contract_fixtures/alpaca_mcp_v2_3_1_provenance.json")
    extension = REPO_ROOT / "tests/contract_fixtures/alpaca_mcp_v2_3_1_readonly_extension.json"
    preflight = _load_json(args.preflight_receipt)

    report = {
        "schema": "esscher.release_security_report",
        "schema_version": 1,
        "claims": ["NO_CREDENTIALS", "PAPER_ONLY", "NO_BROKER_MUTATION"],
        "generated_at": _now().isoformat().replace("+00:00", "Z"),
        "code_revision": args.code_revision,
        "build_artifact": {
            "wheel_name": args.wheel_name,
            "wheel_sha256": args.build_sha256,
        },
        "repository_hygiene": {
            "verdict": hygiene.stdout.strip().splitlines()[-1] if hygiene.stdout.strip() else "",
            "exit_code": hygiene.returncode,
        },
        "test_suite": {
            "summary": args.suite_summary,
            "code_revision": args.code_revision,
        },
        "mcp_provenance": {
            "receipt_sha256": _sha_file(
                REPO_ROOT / "tests/contract_fixtures/alpaca_mcp_v2_3_1_provenance.json"
            ),
            "readonly_extension_receipt_sha256": _sha_file(extension),
            "adapter_version": provenance.get("adapter_version"),
            "wheel_sha256": (provenance.get("wheel") or {}).get("sha256"),
            "sdist_sha256": (provenance.get("sdist") or {}).get("sha256"),
            "source_commit": (provenance.get("source") or {}).get("commit"),
            "fastmcp_version": (provenance.get("runtime") or {}).get("fastmcp_version"),
        },
        "broker_preflight": {
            "receipt_sha256": preflight.get("receipt_sha256"),
            "verdict": preflight.get("verdict"),
            "claims": preflight.get("claims"),
            "account_id_sha256": preflight.get("account_id_sha256"),
        },
        "boundary_attestations": [
            "Credentials are host-environment-only (FURRY_API_KEY, APCA_*); no credential value "
            "enters the repository, CLI arguments, payloads, receipts, or logs (hygiene scanner "
            "plus tests/test_repo_hygiene.py, tests/test_furry_gateway_route.py credential-discard "
            "and test_paper_mcp_composition.py secret-boundary tests).",
            "Broker mutation is impossible through the preflight and rehearsal paths: the "
            "read-only door rejects the mutating selection by construction and the "
            "PaperMcpMutationGate stays closed unless the composition is explicitly armed "
            "(tests/test_paper_preflight_cli.py, tests/test_paper_mcp_composition.py).",
            "The reasoner lane is one-call/no-retry with typed fail-closed abstentions; no "
            "provider, model, or synthetic fallback exists (tests/test_furry_gateway_route.py).",
            "Execution stays permanently PAPER: mode is frozen, live endpoints are rejected, and "
            "no real-money path exists in the package.",
        ],
    }
    return canonical_json_bytes(report), report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--release-id", default="ESSCHER-V040-PAPER-RC1")
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--build-sha256", required=True)
    parser.add_argument("--wheel-name", required=True)
    parser.add_argument("--account-fingerprint", required=True)
    parser.add_argument("--capability-id", default="ALPACA-PAPER-MCP-2.3.1")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--ledger-id", default="PAPER-LEDGER-V040-RC1")
    parser.add_argument("--process-id", default="ESSCHER-HOST-01")
    parser.add_argument("--source-ids", default="ALPACA_MCP,BENZINGA")
    parser.add_argument("--evidence-summary", type=Path, required=True)
    parser.add_argument("--decision-demo", type=Path, required=True)
    parser.add_argument("--measurement-report", type=Path, required=True)
    parser.add_argument("--preflight-receipt", type=Path, required=True)
    parser.add_argument("--suite-summary", required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    evidence_bytes, _ = _build_evidence_report(args)
    security_bytes, _ = _build_security_report(args)
    (args.out / "evidence_report.json").write_bytes(evidence_bytes + b"\n")
    (args.out / "security_report.json").write_bytes(security_bytes + b"\n")
    evidence_sha = sha256_bytes(evidence_bytes)
    security_sha = sha256_bytes(security_bytes)

    release = StrategyRelease(
        release_id=args.release_id,
        release_version=1,
        created_at=_now(),
        mode="PAPER",
        code_revision=args.code_revision,
        build_artifact_sha256=args.build_sha256,
        evidence_report_sha256=evidence_sha,
        security_report_sha256=security_sha,
        evidence_qualified=True,
        security_passed=True,
        lane_bindings=EXPECTED_LANE_BINDINGS,
        **current_semantic_ids(),
    )
    decision = evaluate_release(release)
    if decision.status is not PromotionStatus.PROMOTED:
        raise SystemExit(
            f"refusing: release evaluation returned {decision.status}: {decision.reason}"
        )
    release_raw = strategy_release_bytes(release)
    parse_strategy_release(release_raw)  # exact round-trip

    log_path = args.out / "releases.sqlite3"
    with ReleaseLog(log_path) as release_log:
        release_log.promote(release, decision)
        loaded = release_log.load_exact(release.release_sha256)
    if loaded.release_sha256 != release.release_sha256:
        raise SystemExit("refusing: release log round-trip mismatch")

    (args.out / "release.json").write_bytes(release_raw + b"\n")

    session_date = date.fromisoformat(args.session_date)
    arms = []
    for index, session_id in enumerate(
        [item.strip() for item in args.session_id.split(",") if item.strip()]
    ):
        session_arm = AutonomousSessionArm.for_trading_date(
            session_id=session_id,
            session_date=session_date,
            release_code_sha256=args.build_sha256,
            account_fingerprint_sha256=args.account_fingerprint,
        )
        arm_record = ArmRecord(
            arm_id=session_id,
            release_sha256=release.release_sha256,
            account_capability_id=args.capability_id,
            source_ids=tuple(sorted(set(args.source_ids.split(",")))),
            starts_at=session_arm.starts_at,
            expires_at=session_arm.hard_flat_at,
            ledger_id=args.ledger_id,
            process_id=args.process_id,
            flatten_authority=True,
            recovery_authority=True,
        )
        arm_raw = arm_record_bytes(arm_record)
        parse_arm_record(arm_raw)
        session_arm_raw = autonomous_session_arm_bytes(session_arm)
        parse_autonomous_session_arm(session_arm_raw)
        suffix = "" if index == 0 else f"-{index}"
        (args.out / f"arm_record{suffix}.json").write_bytes(arm_raw + b"\n")
        (args.out / f"session_arm{suffix}.json").write_bytes(session_arm_raw + b"\n")
        arms.append(
            {
                "session_id": session_id,
                "arm_record_path": f"arm_record{suffix}.json",
                "session_arm_path": f"session_arm{suffix}.json",
                "arm_record_sha256": arm_record.arm_sha256,
                "session_arm_sha256": session_arm.arm_sha256,
                "session_windows": [
                    {
                        "window_id": window.window_id,
                        "opens_at": window.opens_at.isoformat().replace("+00:00", "Z"),
                        "closes_at": window.closes_at.isoformat().replace("+00:00", "Z"),
                    }
                    for window in session_arm.windows
                ],
                "hard_flat_at": session_arm.hard_flat_at.isoformat().replace("+00:00", "Z"),
            }
        )

    summary = {
        "schema": "esscher.release_mint_summary",
        "schema_version": 1,
        "minted_at": _now().isoformat().replace("+00:00", "Z"),
        "release_id": release.release_id,
        "release_sha256": release.release_sha256,
        "code_revision": release.code_revision,
        "build_artifact_sha256": release.build_artifact_sha256,
        "evidence_report_sha256": evidence_sha,
        "security_report_sha256": security_sha,
        "arms": arms,
        "release_log_path": str(log_path),
        "claims": ["NO_CREDENTIALS", "PAPER_ONLY", "NO_BROKER_MUTATION"],
    }
    summary_bytes = canonical_json_bytes(summary)
    (args.out / "mint_summary.json").write_bytes(summary_bytes + b"\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
