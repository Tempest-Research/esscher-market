"""Live route-bound decision demonstration for issue #67 (owner-run, one call).

Drives the assembled ``BoundedDecisionEngine`` over the packaged V4 direct
route adapter (current: deepseek-v4-flash-0731-free via the furry.vg gateway)
against the frozen full-fidelity fixture capture and writes one honest,
redacted demonstration artifact binding the live decision to its complete
route/prompt/model-config/request/response identities.

Why this is a demonstration and not a panel ``DirectionReceipt``:

- The only full-fidelity ``StrategyInput`` available on this host is the frozen
  fixture capture for ``KR-2026Q2-EARNINGS`` - one of the four permanently
  excluded P0 contract-development events, so it can never enter panel
  evidence; the 23 frozen panel events keep their raw evidence bytes and
  82-bar price paths host-side (METADATA_AND_HASH_ONLY) on the original
  collection host and cannot be honestly recompiled here.
- The fixture decision cutoff (2026-09-11) is future-dated relative to this
  run, and the receipt contract requires ``produced_at >= cutoff``; minting a
  ``DirectionReceipt`` now would require falsifying its production instant.
  The artifact records the deferral explicitly.

No credential, account datum, or raw provider text leaves this process; the
artifact carries hashes, identities, the validated decision payload, and the
typed exchange status only.

Usage:
    uv run python scripts/generate_route_bound_decision.py \
        --env-file "<path>/furry.env" --out out/route-bound/decision_demo.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from ringdown_market.contracts.latency_profile import load_latency_profile  # noqa: E402
from ringdown_market.contracts.reasoner_route import (  # noqa: E402
    load_current_approved_reasoner_route,
)
from ringdown_market.runtime.host_composition import rehearsal_timeline  # noqa: E402
from ringdown_market.strategy.contracts import (  # noqa: E402
    canonical_json_bytes,
    reasoner_policy_hashes,
    sha256_bytes,
    strategy_decision_payload,
)
from ringdown_market.strategy.engine import BoundedDecisionEngine  # noqa: E402
from ringdown_market.strategy.host_route import (  # noqa: E402
    ENV_FURRY_API_KEY,
    FurryGatewayReasonerRoute,
)
from ringdown_market.strategy.models import ExchangeStatus  # noqa: E402
from ringdown_market.strategy.reasoner import RouteIdentity  # noqa: E402
from test_paper_mcp_composition import _joined_input  # noqa: E402


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _http_transport(api_key: str, timeout_seconds: float):
    def transport(endpoint: str, payload: dict[str, object]) -> bytes:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--transport-timeout", type=float, default=8.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    for line in args.env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    api_key = os.environ.get(ENV_FURRY_API_KEY, "").strip()
    if not api_key:
        print(f"missing {ENV_FURRY_API_KEY} in the host environment file", file=sys.stderr)
        return 2

    route = load_current_approved_reasoner_route()
    identity = RouteIdentity(provider=route.provider, model=route.model)
    adapter = FurryGatewayReasonerRoute(
        route=route,
        api_key=api_key,
        identity=identity,
        transport=_http_transport(api_key, args.transport_timeout),
    )
    engine = BoundedDecisionEngine(adapter, identity=identity)

    joined = _joined_input()
    timeline = rehearsal_timeline(joined)
    produced_at = datetime.now(UTC)
    outcome = engine.decide(joined, started_at=timeline.started_at)
    exchange = outcome.exchange
    snapshot = joined.snapshot
    route_sha, prompt_sha, schema_sha = reasoner_policy_hashes(snapshot.candidate_id)
    profile = load_latency_profile()

    artifact = {
        "schema": "esscher.route_bound_decision_demonstration",
        "schema_version": 1,
        "claims": ["NO_CREDENTIALS", "PAPER_ONLY", "NOT_ALPHA_EVIDENCE", "SOURCE_GROUNDED"],
        "limitations": [
            "DIRECTION_RECEIPT_MINTING_DEFERRED_FIXTURE_CUTOFF_IS_FUTURE_DATED",
            "EXCLUDED_CONTRACT_DEVELOPMENT_EVENT",
            "FIXTURE_CAPTURE_DATA",
            "NOT_HISTORICAL_DATA",
        ],
        "produced_at": _iso(produced_at),
        "event_id": snapshot.event_id,
        "candidate_id": snapshot.candidate_id,
        "decision_cutoff_at": _iso(snapshot.decision_cutoff_at),
        "decision": strategy_decision_payload(outcome.decision),
        "exchange": {
            "status": exchange.status.value,
            "error_code": exchange.error_code,
            "provider": exchange.provider,
            "model": exchange.model,
            "model_revision": exchange.model_revision,
            "decoding": {
                "max_output_tokens": exchange.decoding.max_output_tokens,
                "seed": exchange.decoding.seed,
                "temperature": str(exchange.decoding.temperature),
                "top_p": str(exchange.decoding.top_p),
            },
            "started_at": _iso(exchange.started_at),
            "responded_at": _iso(exchange.responded_at),
            "deadline_at": _iso(exchange.deadline_at),
            "route_sha256": exchange.route_sha256,
            "prompt_sha256": exchange.prompt_sha256,
            "output_schema_sha256": exchange.output_schema_sha256,
            "model_config_sha256": exchange.model_config_sha256,
            "request_sha256": exchange.request_sha256,
            "raw_response_sha256": exchange.raw_response_sha256,
            "producer_build_sha256": exchange.producer_build_sha256,
            "strategy_snapshot_sha256": exchange.strategy_snapshot_sha256,
            "feature_receipt_sha256": exchange.feature_receipt_sha256,
            "evidence_packet_sha256": exchange.evidence_packet_sha256,
            "policy_sha256": exchange.policy_sha256,
        },
        "route_package": {
            "route_id": route.route_id,
            "route_sha256": route.route_sha256,
            "model_config_sha256": route.model_config_sha256,
            "provider": route.provider,
            "model": route.model,
            "base_url": route.base_url,
        },
        "policy_registry_hashes": {
            "route_sha256": route_sha,
            "prompt_sha256": prompt_sha,
            "output_schema_sha256": schema_sha,
        },
        "latency_profile": {
            "kind": profile.kind.value,
            "p95_latency_ms": profile.p95_latency_ms,
            "content_sha256": profile.content_sha256,
        },
        "decision_artifact_sha256": sha256_bytes(
            canonical_json_bytes(strategy_decision_payload(outcome.decision))
        ),
    }
    raw = canonical_json_bytes(artifact)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(raw + b"\n")
    print(f"status={exchange.status.value} direction={outcome.decision.direction.value}")
    print(f"decision_artifact_sha256={artifact['decision_artifact_sha256']}")
    print(f"artifact_sha256={sha256_bytes(raw)}")
    print(f"written: {args.out}")
    if exchange.status is not ExchangeStatus.COMPLETED:
        print("live decision did not complete; artifact records the typed failure", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
