# One approved PAPER open-to-flat proof

This runbook is for one reviewed Esscher demonstration. It never authorizes live trading, a second adapter, direct REST/SDK/CLI order calls, or repeated submissions.

## Mutation envelope

The repository runner accepts only a host-built `PaperDemoPlan`. The host plan must create `PreparedHostMcpSession` through `HostMcpPaperSessionFactory` for the pinned official Alpaca MCP `2.3.0` session. It must map exact frozen decision, evidence, and input bytes through `map_frozen_decision_to_permit`; it must not construct a permit directly.

Before approval, the only allowed external operations are:

1. official MCP capability listing;
2. `get_account_info` PAPER eligibility preflight;
3. frozen local artifact reads.

After exact approval, the runner's complete mutation allowlist is:

- at most one `place_option_order` for the deterministic opening client-order ID;
- zero or one `cancel_order_by_id` for an unfilled opening order;
- at most one `place_option_order` for the deterministic atomic closing client-order ID.

All other lifecycle operations are read-only: `get_order_by_client_id`, `get_order_by_id`, and `get_all_positions`. There is no per-leg close, market-order fallback, blind retry, account-wide close/cancel, portfolio-history P&L, direct REST/SDK/CLI route, or live-mode value.

## Phase 1: read-only preflight

The operator-owned module named below must return a `PaperDemoPlan`. It owns the normalized MCP transport and frozen artifact locations; it does not expose credentials.

```bash
uv run python scripts/run_paper_demo.py \
  --host-plan operator_paper_plan:build_plan \
  --preflight-output .local/paper-demo-approval.json
```

This performs host capability/account preflight and writes a new, non-overwriting approval template containing the exact permit ID, PAPER environment, and capability hash. It does not create an attempt marker and cannot invoke an order or cancellation tool.

Stop if the host is not provably PAPER, any required official tool is absent, the frozen artifacts fail validation, either permit is inactive, the approval template already exists, or any credential/account identifier appears in local artifacts.

## Human approval gate

After the implementation PR and exact head are independently reviewed, the named operator may replace only `approved_at` and `expires_at` in the template. Use timezone-aware ISO-8601 values and a short expiry that covers the bounded proof. The permit ID, capability hash, environment, schema, and version remain unchanged.

Approval is for this exact permit and capability observation only. A changed permit or new preflight requires a new approval. Do not approve until the issue owner explicitly authorizes the paper mutation.

## Phase 2: one bounded PAPER proof

```bash
uv run python scripts/run_paper_demo.py \
  --host-plan operator_paper_plan:build_plan \
  --execute-paper \
  --approval-file .local/paper-demo-approval.json \
  --attempt-store .local/paper-demo-attempts \
  --receipt-output .local/paper-demo-receipt.json
```

The attempt store is durable and must be preserved across restarts. Each deterministic client-order ID is atomically marked before submission. If a marker already exists, the runner performs readback only and never submits that client ID again. The receipt path also refuses overwrite.

The runner stops for manual reconciliation on ambiguous/unreadable mutation outcomes, partial fills, unknown states, identity mismatch, malformed order truth, a non-filled atomic close, any remaining event leg, or missing final-flat proof. Never delete attempt markers to make a retry possible.

## Receipt interpretation

A terminal receipt is valid only after `get_all_positions` contains neither event leg.

- `ZERO_NO_FILL`: terminal canceled/expired/rejected opening order with broker-observed filled quantity exactly zero.
- `PAPER_REALIZED_PNL`: both package orders are filled for the exact quantity, opening debit and closing credit signs agree, and both broker fill prices/timestamps are present. The bundle reports gross PAPER P&L. Net P&L remains null unless broker-reported fees are present.
- `PAPER_PNL_UNAVAILABLE`: final-flat proof exists but fill economics are missing, partial, contradictory, unmatched, non-finite, or otherwise insufficient. No limit price, account portfolio history, or guessed fee replaces them.

The deterministic JSON contains hashes of raw broker order IDs, never the IDs themselves. It excludes account IDs, credentials, raw MCP responses, and secret-like fields. The receipt is an operational PAPER observation, not evidence of alpha, executable fill quality, expected profitability, or live performance.

## Local verification before any approval

```bash
uv run python scripts/check_repo_hygiene.py
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Do not attach a receipt until its `receipt_sha256` recomputes over the canonical payload with that field removed, the exact PR head has passed CI, and the final issue comment separately identifies request, acknowledgement, paper fill, close/cancel, final-flat proof, P&L classification, and limitations.
