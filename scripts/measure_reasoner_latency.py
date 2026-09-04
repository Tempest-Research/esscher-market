"""Host measurement harness for the #91 latency gate (owner-run, live provider).

Drives the packaged direct-provider adapter for the CURRENT approved reasoner
route (V4: FurryGatewayReasonerRoute via the furry.vg gateway; V3 MiniMax-M3
remains supported as a dormant alternate) against the frozen fixture decision
prompt and records per-call wall latency, adapter status, and strict schema
validity.  Writes one redacted JSON report: no credential, no account data,
and no response text - only latencies, statuses, decisions, and hashes.

Usage (host environment, key outside the repository):
    uv run python scripts/measure_reasoner_latency.py \
        --env-file "<path>/furry.env" --samples 22 --cold 2 \
        --out artifacts/measure/furry_gateway_latency_report.json

The first --cold observations are cold-start samples: excluded from the p95
statistic and reported separately, per the frozen profile warm/cold policy.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from ringdown_market.contracts.reasoner_route import (  # noqa: E402
    load_current_approved_reasoner_route,
)
from ringdown_market.strategy.contracts import parse_reasoner_decision  # noqa: E402
from ringdown_market.strategy.host_route import (  # noqa: E402
    ENV_FURRY_API_KEY,
    ENV_MINIMAX_API_KEY,
    FurryGatewayReasonerRoute,
    MinimaxM3ReasonerRoute,
)
from ringdown_market.strategy.reasoner import ReasonerRouteRequest, RouteIdentity  # noqa: E402

_DIRECT_ADAPTERS = {
    "furry_vg_gateway": (FurryGatewayReasonerRoute, ENV_FURRY_API_KEY),
    "minimax_direct": (MinimaxM3ReasonerRoute, ENV_MINIMAX_API_KEY),
}


def _load_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _http_transport_factory(api_key: str, timeout_seconds: float):
    def transport(endpoint: str, payload: dict[str, object]) -> bytes:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if isinstance(error, OSError) and "timed out" in str(error).lower():
                raise TimeoutError("provider call exceeded the frozen hard timeout") from None
            raise RuntimeError(type(error).__name__) from None

    return transport


def _nearest_rank_p95(values: list[int]) -> int:
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=22)
    parser.add_argument("--cold", type=int, default=2)
    parser.add_argument(
        "--transport-timeout",
        type=float,
        default=8.0,
        help=(
            "transport wall timeout; the frozen call policy is 8s, larger values "
            "are diagnostic-only and recorded in the report"
        ),
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.samples - args.cold < 20:
        print("need at least 20 warm samples for the frozen minimum", file=sys.stderr)
        return 2

    _load_env_file(args.env_file)
    route = load_current_approved_reasoner_route()
    try:
        adapter_class, env_name = _DIRECT_ADAPTERS[route.provider]
    except KeyError:
        print(
            f"current route provider {route.provider!r} has no direct measurement adapter",
            file=sys.stderr,
        )
        return 2
    api_key = os.environ.get(env_name, "").strip()
    if not api_key:
        print(f"missing {env_name} in the host environment file", file=sys.stderr)
        return 2

    # Fixture-driven strategy input: the identical frozen decision prompt the
    # production composition uses, joined from the packaged synthetic capture.
    from ringdown_market.runtime.host_composition import rehearsal_timeline
    from test_paper_mcp_composition import _joined_input

    joined = _joined_input()
    # The production decision window: started_at is the timeline's decision
    # start (cutoff - 10s), so the frozen deadline min(started+8s, cutoff)
    # gives the provider its full hard-timeout window.
    started_at = rehearsal_timeline(joined).started_at

    timeout_seconds = args.transport_timeout
    adapter = adapter_class(
        route=route,
        api_key=api_key,
        identity=RouteIdentity(provider=route.provider, model=route.model),
        transport=_http_transport_factory(api_key, timeout_seconds),
    )

    samples: list[dict[str, object]] = []
    for index in range(args.samples):
        request = ReasonerRouteRequest(strategy_input=joined, started_at=started_at)
        wall_start = time.monotonic()
        result = adapter(request)
        latency_ms = int((time.monotonic() - wall_start) * 1000)
        exchange = result.exchange
        schema_valid = False
        decision_value: str | None = None
        parse_error: str | None = None
        content_head: str | None = None
        if result.raw_response_bytes is not None:
            content_head = result.raw_response_bytes.decode("utf-8", "replace")[:200]
            try:
                parsed = parse_reasoner_decision(result.raw_response_bytes)
                schema_valid = True
                decision_value = parsed.decision.value
            except Exception as error:
                schema_valid = False
                parse_error = f"{type(error).__name__}: {error}"[:300]
        samples.append(
            {
                "index": index,
                "cold_start": index < args.cold,
                "latency_ms": latency_ms,
                "status": exchange.status.value,
                "error_code": exchange.error_code,
                "schema_valid": schema_valid,
                "decision": decision_value,
                "parse_error": parse_error,
                "content_head": content_head,
                "prompt_sha256": exchange.prompt_sha256,
                "request_sha256": exchange.request_sha256,
                "raw_response_sha256": exchange.raw_response_sha256,
                "observed_at": datetime.now(UTC)
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            }
        )
        print(
            f"sample {index:02d}: {latency_ms} ms status={exchange.status.value} "
            f"schema_valid={schema_valid} decision={decision_value}"
        )
    warm = [sample for sample in samples if not sample["cold_start"]]
    cold = [sample for sample in samples if sample["cold_start"]]
    warm_latencies = [int(sample["latency_ms"]) for sample in warm]
    completed = [sample for sample in warm if sample["status"] == "COMPLETED"]
    report = {
        "schema": "esscher.host_latency_measurement_report",
        "schema_version": 1,
        "claims": ["NO_CREDENTIALS", "PAPER_ONLY", "SOURCE_GROUNDED"],
        "route_id": route.route_id,
        "route_sha256": route.route_sha256,
        "model_config_sha256": route.model_config_sha256,
        "provider": route.provider,
        "model": route.model,
        "prompt_sha256": samples[0]["prompt_sha256"] if samples else None,
        "frozen_hard_timeout_seconds": 8,
        "transport_timeout_seconds": timeout_seconds,
        "total_samples": len(samples),
        "cold_start_samples": len(cold),
        "cold_start_latencies_ms": [int(sample["latency_ms"]) for sample in cold],
        "warm_samples": len(warm),
        "warm_completed": len(completed),
        "warm_schema_valid": sum(1 for sample in warm if sample["schema_valid"]),
        "warm_p50_ms": sorted(warm_latencies)[len(warm_latencies) // 2] if warm_latencies else None,
        "warm_p95_ms_nearest_rank": _nearest_rank_p95(warm_latencies) if warm_latencies else None,
        "warm_max_ms": max(warm_latencies) if warm_latencies else None,
        "payload_note": (
            "Every observation repeats the identical frozen fixture decision "
            "prompt (canonical bytes); provider-side caching effects are "
            "possible and disclosed."
        ),
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"report written: {args.out}")
    print(
        f"warm={len(warm)} completed={len(completed)} schema_valid={report['warm_schema_valid']} "
        f"p50={report['warm_p50_ms']}ms p95={report['warm_p95_ms_nearest_rank']}ms "
        f"max={report['warm_max_ms']}ms"
    )
    if len(completed) < 20 or report["warm_schema_valid"] < 20:
        print("INSUFFICIENT valid warm samples for the frozen gate", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
