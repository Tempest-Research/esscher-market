# Ringdown

**Measure the move after the first reaction.**

Ringdown is a paper-only scheduled-earnings research and controlled-execution system. It combines an offline alpha and latency evaluation harness with one bounded official Alpaca MCP adapter. The package does not load credentials or start an MCP session; its host must inject a normalized paper-account session.

## Current slice

Given a point-in-time event fixture and frozen latency profile, the harness produces a deterministic Q-FAST report that:

- rejects evidence published after the decision cutoff;
- prices the event from an achievable delayed entry, not an imaginary zero-latency fill;
- keeps abstentions in the eligible-event panel as zero returns;
- compares the candidate signal with frozen baselines at equal risk;
- reports `INSUFFICIENT_DATA`, `REJECTED`, or `NOT_REJECTED_SMALL_SAMPLE` without claiming alpha.

Synthetic fixtures are contract tests only. They are never financial evidence.

Given an immutable opening permit and a separately authorized closing permit, the paper adapter:

- compiles exact `place_option_order` multi-leg requests against Alpaca MCP `2.3.0`;
- uses deterministic client-order IDs and request hashes;
- submits once, then reconciles ambiguous transport outcomes by broker readback;
- cancels an unfilled opening order or closes a filled vertical atomically as one reversed multi-leg order;
- refuses automatic sequential-leg repair after a partial fill;
- emits a terminal receipt only after broker position truth contains neither event leg.

The MCP boundary is implemented and contract-tested with injected sessions. It is not evidence of a real paper fill, strategy profitability, or executable historical option pricing.

## Boundaries

- `PAPER_ONLY`
- `INDICATIVE_DATA`
- `OFFICIAL_ALPACA_MCP_ONLY`
- `NO_CREDENTIALS`
- `NO_DIRECT_REST_OR_CLI_FALLBACK`
- `NO_ALPHA_CLAIM`
- `NO_EXECUTABLE_OPTIONS_CLAIM`

## Project map

- [Architecture](docs/ARCHITECTURE.md) — implemented behavior, planned boundaries, and invariants.
- [Team onboarding](docs/TEAM_ONBOARDING.md) — required research and lane ownership.
- [Source and claim policy](docs/SOURCE_AND_CLAIM_POLICY.md) — evidence metadata and permitted claims.
- [Contributing](CONTRIBUTING.md) — branch, test, review, and safety gates.
- [Changelog](CHANGELOG.md) — versioned behavior changes and release state.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
uv run ringdown --version
```

Run the labeled synthetic contract fixture:

```bash
uv run ringdown evaluate \
  --input tests/fixtures/synthetic_contract_panel.json \
  --output build/synthetic-contract-report.json
```

The output is deterministic: identical input bytes and protocol settings produce identical report bytes. The included fixture deliberately uses only synthetic prices and carries `NOT_HISTORICAL_DATA`, `NOT_ALPHA_EVIDENCE`, and `NO_BROKER_EXECUTION` labels.

## Repository workflow

Use feature branches and draft pull requests. Keep reviewer requests empty until the diff and public wording are ready. See [CONTRIBUTING.md](CONTRIBUTING.md).
