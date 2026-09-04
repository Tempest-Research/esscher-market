"""Publish the redacted paper-session state for the website live tracker.

Reads live PAPER account truth directly from the Alpaca REST API (read-only
endpoints: /v2/account, /v2/positions, /v2/orders?status=open), combines it
with the content-addressed gate artifacts (preflight receipt, release/mint
summary, rehearsal audits, route measurement, last decision demo), and
publishes `session-state.json` to the `live-data` branch of the esscher-market
repo via the GitHub contents API. The website fetches it through the GitHub
contents API / raw.githubusercontent.com (cache-busted, auto-refreshed), so no
redeploy is needed per update.

Safety:
- PAPER-only: refuses any non paper-api base URL.
- Read-only by construction: only GET endpoints are called.
- Redaction: no credentials, no raw account id (sha256 digest only), no order
  or position payloads beyond symbol/qty/side/value/price/pl fields.
- Lane labels (DELAYED_EXECUTION_DEMO etc.) always carried so the website can
  never present demo-lane activity as the validated lane.

Usage:
  # one-shot
  python scripts/publish_session_state.py --repo . --env-file ../ringdown-market/.env \
      --mint-dir out/release-packet-rc2 --preflight out/preflight/receipt.json \
      --rehearsal out/rehearsals/r1 --measurement out/route-bound/measurement_report.json \
      --lane PRE_SESSION
  # watch mode beside paper-run: republishes whenever account state changes
  python scripts/publish_session_state.py ... --watch --interval 60

Token: GITHUB_TOKEN env var, else `git credential fill` for github.com.
Never printed, never written to disk.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_API = "https://api.github.com/repos/Tempest-Research/esscher-market"
BRANCH = "live-data"
FILE_PATH = "session-state.json"
STARTING_EQUITY = 100000.0
DEMO_LABELS = ["DELAYED_EXECUTION_DEMO", "NOT_THE_VALIDATED_LANE", "INDICATIVE_OPTION_PRICING"]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


PAPER_API_HOST = "paper-api.alpaca.markets"


def _assert_paper_base(base: str) -> None:
    host = base.split("//", 1)[-1].split("/", 1)[0].lower()
    if host != PAPER_API_HOST:
        raise SystemExit(f"REFUSING: base URL host {host!r} is not {PAPER_API_HOST!r}")


def _alpaca_get(base: str, path: str, key_id: str, secret: str) -> object:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _account_state(get: object) -> dict:
    account = get("/v2/account")
    orders = get("/v2/orders?status=open&limit=100&direction=asc")
    positions = get("/v2/positions")
    assert isinstance(account, dict)
    account_id = str(account.get("id", ""))
    equity = float(account.get("equity") or 0)
    rows = []
    unrealized = 0.0
    for position in positions if isinstance(positions, list) else []:
        pl = float(position.get("unrealized_pl") or 0)
        unrealized += pl
        rows.append(
            {
                "symbol": str(position.get("symbol", "")),
                "qty": str(position.get("qty", "")),
                "side": str(position.get("side", "")),
                "asset_class": str(position.get("asset_class", "")),
                "market_value": str(position.get("market_value", "")),
                "avg_entry_price": str(position.get("avg_entry_price", "")),
                "current_price": str(position.get("current_price", "")),
                "unrealized_pl": str(position.get("unrealized_pl", "")),
                "unrealized_plpc": str(position.get("unrealized_plpc", "")),
            }
        )
    return {
        "account_id_sha256": hashlib.sha256(account_id.encode("utf-8")).hexdigest(),
        "account_class": str(account.get("account_class", "")),
        "status": str(account.get("status", "")),
        "equity": str(account.get("equity", "")),
        "cash": str(account.get("cash", "")),
        "buying_power": str(account.get("buying_power", "")),
        "equity_vs_start": round(equity - STARTING_EQUITY, 2),
        "equity_pct_vs_start": round((equity - STARTING_EQUITY) / STARTING_EQUITY * 100, 4),
        "open_order_count": len(orders) if isinstance(orders, list) else 0,
        "positions": rows,
        "positions_unrealized_pl": round(unrealized, 2),
        "observed_at": _now(),
    }


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError):
        return None


def _short(value: object, keep: int = 16) -> str | None:
    return value[:keep] if isinstance(value, str) else None


def _gate_state(
    mint_dir: Path,
    preflight: Path,
    rehearsals: list[Path],
    measurement: Path,
    decision_demo: Path | None,
) -> dict:
    receipt = _load_json(preflight) or {}
    mint = _load_json(mint_dir / "mint_summary.json") or {}
    rehearsal_rows = []
    for directory in rehearsals:
        audit = _load_json(directory / "rehearsal-audit.json") or {}
        rehearsal_rows.append(
            {
                "session_id": audit.get("session_id"),
                "mode": audit.get("mode"),
                "disposition": audit.get("disposition"),
                "terminal_flat_proven": audit.get("terminal_flat_proven"),
                "mutating_tool_calls": audit.get("mutating_tool_calls"),
                "receipt_sha256": _short(audit.get("receipt_sha256")),
            }
        )
    measure = _load_json(measurement) or {}
    demo = _load_json(decision_demo) if decision_demo else None
    demo = demo or {}
    last_decision = None
    if demo:
        last_decision = {
            "direction": (demo.get("decision") or {}).get("direction"),
            "status": (demo.get("exchange") or {}).get("status"),
            "produced_at": demo.get("produced_at"),
            "artifact_sha256": _short(demo.get("decision_artifact_sha256")),
        }
    return {
        "preflight": {
            "receipt_id": receipt.get("receipt_id"),
            "verdict": receipt.get("verdict"),
            "receipt_sha256": _short(receipt.get("receipt_sha256")),
            "is_flat": receipt.get("is_flat"),
            "starting_balance_satisfied": receipt.get("starting_balance_satisfied"),
            "environment": receipt.get("environment"),
            "observed_at": receipt.get("observed_at"),
        },
        "release": {
            "release_id": mint.get("release_id"),
            "release_sha256": _short(mint.get("release_sha256")),
            "code_revision": _short(mint.get("code_revision"), 12),
            "build_sha256": _short(mint.get("build_artifact_sha256")),
        },
        "rehearsals": rehearsal_rows,
        "reasoner": {
            "route": f"{measure.get('provider')} / {measure.get('model')}" if measure else None,
            "route_sha256": _short(measure.get("route_sha256")),
            "warm_completed": measure.get("warm_completed"),
            "warm_schema_valid": measure.get("warm_schema_valid"),
            "p50_ms": measure.get("warm_p50_ms"),
            "p95_ms": measure.get("warm_p95_ms_nearest_rank"),
            "budget_seconds": measure.get("frozen_hard_timeout_seconds"),
            "last_live_decision": last_decision,
        },
    }


def _build_payload(args: argparse.Namespace) -> dict:
    base = os.environ.get("APCA_API_BASE_URL", "")
    key_id = os.environ.get("APCA_API_KEY_ID", "")
    secret = os.environ.get("APCA_API_SECRET_KEY", "")
    if not (base and key_id and secret):
        raise SystemExit(
            "APCA_API_BASE_URL / APCA_API_KEY_ID / APCA_API_SECRET_KEY missing (use --env-file)"
        )
    _assert_paper_base(base)
    getter = lambda path: _alpaca_get(base, path, key_id, secret)  # noqa: E731
    return {
        "schema": "esscher.public_session_state",
        "schema_version": 1,
        "generated_at": _now(),
        "claims": ["PAPER_ONLY", "NO_CREDENTIALS", "NO_RAW_ACCOUNT_ID", "SOURCE_GROUNDED"],
        "lane": {
            "kind": args.lane,
            "labels": DEMO_LABELS if args.lane == "DELAYED_EXECUTION_DEMO" else [],
        },
        "session_status": args.session_status,
        "account": _account_state(getter),
        "gate": _gate_state(
            args.mint_dir, args.preflight, args.rehearsal, args.measurement, args.decision_demo
        ),
    }


def _stable_hash(payload: dict) -> str:
    account = {k: v for k, v in payload["account"].items() if k != "observed_at"}
    material = json.dumps(
        {"account": account, "gate": payload["gate"]}, sort_keys=True, default=str
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    result = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password=") :].strip()
    raise SystemExit("no GITHUB_TOKEN and git credential fill returned no password")


def _publish(payload: dict, token: str) -> str:
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "esscher-session-publisher",
    }
    url = f"{REPO_API}/contents/{FILE_PATH}"
    existing_sha = None
    try:
        request = urllib.request.Request(f"{url}?ref={BRANCH}", headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            existing_sha = json.loads(response.read()).get("sha")
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    lane = payload["lane"]["kind"]
    put: dict = {
        "message": f"chore(live-data): session state {payload['generated_at']} ({lane})",
        "content": base64.b64encode(body).decode("ascii"),
        "branch": BRANCH,
    }
    if existing_sha:
        put["sha"] = existing_sha
    request = urllib.request.Request(
        url, data=json.dumps(put).encode("utf-8"), headers=headers, method="PUT"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return str(json.loads(response.read()).get("commit", {}).get("sha", ""))


def _emit(payload: dict, args: argparse.Namespace) -> None:
    out = args.out or (args.repo / "out/website/session-state.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n")
    token = _github_token()
    commit = _publish(payload, token)
    print(
        f"[{_now()}] published {BRANCH}@{_short(commit, 12)} equity={payload['account']['equity']} "
        f"positions={len(payload['account']['positions'])} local={out}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--mint-dir", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--rehearsal", type=Path, action="append", default=[])
    parser.add_argument("--measurement", type=Path, required=True)
    parser.add_argument("--decision-demo", type=Path, default=None)
    parser.add_argument(
        "--lane",
        choices=("PRE_SESSION", "DELAYED_EXECUTION_DEMO", "VALIDATED"),
        default="PRE_SESSION",
    )
    parser.add_argument("--session-status", default="PRE_SESSION")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=60, help="watch poll seconds")
    parser.add_argument(
        "--heartbeat", type=int, default=900, help="republish at least every N seconds"
    )
    args = parser.parse_args()

    _load_env_file(args.env_file)
    if not args.watch:
        _emit(_build_payload(args), args)
        return 0
    last_hash = ""
    last_publish = 0.0
    while True:
        try:
            payload = _build_payload(args)
            digest = _stable_hash(payload)
            if digest != last_hash or (time.monotonic() - last_publish) >= args.heartbeat:
                changed = digest != last_hash
                _emit(payload, args)
                print(f"  trigger: {'state-change' if changed else 'heartbeat'}")
                last_hash = digest
                last_publish = time.monotonic()
            else:
                print(f"[{_now()}] unchanged (equity={payload['account']['equity']})")
        except KeyboardInterrupt:
            print("watch stopped")
            return 0
        except Exception as error:
            print(
                f"[{_now()}] publish attempt failed: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("watch stopped")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
