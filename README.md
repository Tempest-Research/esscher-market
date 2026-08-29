# Esscher

**Measure the move after the first reaction.**

Esscher is a permanently paper-only scheduled-earnings research and controlled-execution system. It combines an offline alpha and latency evaluation harness with one bounded official Alpaca MCP adapter. The package does not load credentials or start an MCP server. Its host injects one normalized session plus a non-secret PAPER environment attestation.

## Name and compatibility

Esscher is the public, human-facing product name. This release deliberately keeps the existing machine interfaces:

- Python distribution: `ringdown-market`;
- Python import package: `ringdown_market`;
- CLI command and installed version prefix: `ringdown`;
- configuration keys, report schema keys, and receipt identifiers: unchanged;
- report display: keep legacy `project: "Ringdown"` for compatibility and use additive `product_name: "Esscher"` for new displays;
- GitHub repository before the approved external cutover: `Tempest-Research/ringdown-market`.

The current repository URL and its issue, pull-request, and source links remain canonical until the external rename. This change does not claim trademark clearance or ownership of any package-registry name.

## Current slice

Given a point-in-time event fixture and frozen latency profile, the harness produces a deterministic Q-FAST report that:

- rejects evidence published after the decision cutoff;
- selects the first eligible path observation after modeled latency, not an imaginary zero-latency fill;
- keeps abstentions in the eligible-event panel as zero returns;
- compares the candidate signal with frozen baselines under a common unit residual-return convention, not equal capital or live portfolio risk;
- reports `INSUFFICIENT_DATA`, `REJECTED`, or `NOT_REJECTED_SMALL_SAMPLE` without claiming alpha.

Synthetic fixtures are contract tests only. They are never financial evidence.

Given exact frozen decision, point-in-time evidence-manifest, and feature-input bytes, the pure contract bridge:

- rejects duplicate, missing, unknown, mutable, or unsupported schema values;
- verifies exact decision, evidence, input, protocol, and policy identities;
- rejects post-cutoff or dependency-open evidence before any execution session exists;
- rejects abstention, ineligible, Q-FAST-rejected, and Q-LATENCY-failed decisions;
- maps the already-chosen supported debit vertical without rescoring or changing direction;
- issues deterministic immutable `PAPER` permit bytes under the frozen paper-risk policy.

See [the research-to-permit contract](docs/contracts/research-to-permit.md) for every copied, derived, and rejected field.

Given an immutable opening permit and a separately authorized closing permit, the paper adapter:

- compiles exact `place_option_order` multi-leg requests against Alpaca MCP `2.3.0`;
- uses deterministic client-order IDs and request hashes;
- submits once, then reconciles ambiguous transport outcomes by broker readback;
- cancels an unfilled opening order or closes a filled vertical atomically as one reversed multi-leg order;
- refuses automatic sequential-leg repair after a partial fill;
- emits a terminal receipt only after broker position truth contains neither event leg.

Before exposing that session to the adapter, the host boundary checks the six required tools from the pinned official surface and reads sanitized account status through `get_account_info`. Missing tools, malformed responses, blocked accounts, and any environment other than PAPER fail closed before a mutation. Runtime calls are limited to the adapter's five official order and position tools; secret-like application arguments are rejected before reaching the host.

The MCP boundary is implemented and contract-tested with injected fake sessions. No real broker call is part of the test suite. This is not evidence of a real paper fill, strategy profitability, or executable historical option pricing.

The inert paper-demo runner adds a separate short-lived approval bound to the exact permit and host capability proof, durable submit-once markers for restart recovery, exact broker fill-economics classification, and a deterministic sanitized terminal bundle. See [the PAPER demonstration runbook](docs/PAPER_DEMO_RUNBOOK.md). No demonstration has been run by the test suite or by this repository state.

The one-shot scheduled runner accepts one strict approved event manifest, validates the exact PAPER permit/capability identity and half-open due window, serializes active events through an OS-backed lock, atomically persists a hash-bound restart cursor, reconciles deterministic broker order truth, and exits terminal or stopped for manual reconciliation. Dry run performs no local or broker mutation; terminal repeats are no-ops. See [the scheduled-event runbook](docs/SCHEDULED_EVENT_RUNBOOK.md). Tests use injected fake sessions and clocks only.

## Boundaries

- `PAPER_ONLY`
- `FROZEN_DECISION_ONLY`
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
- [Point-in-time evidence gate](docs/research/point-in-time-evidence-gate.md) — timing, provenance, residualization, denominator, and options-data contract.
- [Research-to-permit contract](docs/contracts/research-to-permit.md) — exact schemas, identity mapping, rejection reasons, and frozen policy.
- [PAPER demonstration runbook](docs/PAPER_DEMO_RUNBOOK.md) — read-only preflight, exact approval, bounded mutation envelope, recovery, and receipt interpretation.
- [Scheduled-event runbook](docs/SCHEDULED_EVENT_RUNBOOK.md) — one-shot manifest, dry run, armed invocation, restart reconciliation, and stop conditions.
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

## Repository rename cutover (after merge and approval)

Do not execute this checklist from the branding pull request. The repository owner performs it only after the pull request merges, the public wording and understanding gate are approved, and current-head CI passes:

1. Rename `Tempest-Research/ringdown-market` to `Tempest-Research/esscher-market` in GitHub settings.
2. Verify the old repository URL redirects and existing issue, pull-request, commit, and source links still resolve.
3. Update local Git remotes, repository badges, and canonical source links in a separate cutover change.
4. Re-run repository hygiene, tests, wheel installation, and the non-destructive CLI smoke check after link updates.
5. Keep `ringdown-market`, `ringdown_market`, `ringdown`, configuration keys, and receipt/schema identifiers compatible.
6. Do not publish a renamed registry package or imply trademark clearance as part of the repository rename.
