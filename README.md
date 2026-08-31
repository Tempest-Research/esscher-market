# Esscher

**Measure the move after the first reaction.**

Esscher is a permanently paper-only scheduled-event research and controlled-PAPER-lifecycle system. It binds point-in-time evidence to frozen strategy, expression, risk, and lifecycle contracts without loading credentials, starting an MCP server, or opening a data-provider path. The package can be exercised entirely with deterministic local fixtures; the production mutation gate remains closed.

## Name and compatibility

Esscher is the public, human-facing product name. The `0.3.0` integration draft keeps the existing machine interfaces:

- Python distribution: `ringdown-market`;
- Python import package: `ringdown_market`;
- CLI command and installed version prefix: `ringdown`;
- configuration keys, report schema keys, and receipt identifiers: unchanged;
- report display: keep legacy `project: "Ringdown"` for compatibility and use additive `product_name: "Esscher"` for new displays;
- GitHub repository: `Tempest-Research/esscher-market`.

This release does not claim trademark clearance or ownership of any package-registry name.

## Current slice

Given a point-in-time event fixture and frozen latency profile, the harness produces a deterministic Q-FAST report that:

- rejects evidence published after the decision cutoff;
- selects the first eligible path observation after modeled latency, not an imaginary zero-latency fill;
- keeps abstentions in the eligible-event panel as zero returns;
- compares the candidate signal with frozen baselines under a common unit residual-return convention, not equal capital or live portfolio risk;
- reports `INSUFFICIENT_DATA`, `REJECTED`, or `NOT_REJECTED_SMALL_SAMPLE` without claiming alpha.

Synthetic fixtures are contract tests only. They are never financial evidence.

The accepted event-strategy research policy freezes the systematic earnings-primary and
macro-challenger candidates, complete-denominator universe rules, separate cohort clocks,
permitted source classes, deterministic features and confirmation vetoes, reasoner schema,
baselines, chronological partitions, and promotion thresholds. Exact canonical policy bytes and
typed candidate-manifest, snapshot, feature-receipt, reasoner-exchange, and direction-only
decision contracts live under `ringdown_market.strategy`. Gate A facts remain explicitly
`UNVERIFIED`; neither the policy, expression compiler, risk kernel, nor lifecycle worker can
turn that into data collection, account, broker, or PAPER-mutation authority.

### Source-rights capture boundary

The offline snapshot collector is constrained by the authenticated
`esscher.source_matrix/v1` resource (SHA-256
`888447640aa705510bc0594abc9a78f22c988e961282ff82a6f44337181d04ca`). Every
capture rebinds that matrix to the accepted policy
`afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca` and the
Gate A programme contract
`40c2e780c684bdde671b028dbdd8c9b13268e659c24e98a2d452ff7c8692f955`. A change
to any of those bytes fails closed before a snapshot exists.

Capture first selects the exact earnings or macro candidate, checks that candidate's required
source classes, and resolves a CIK-rooted security/listing lineage at the same decision cutoff.
It accepts only an explicit synthetic fixture, an explicit zero-offset UTC clock, explicit host
authorization, and declared rights conditions. The resulting source-matrix and lineage receipts
are hash-bound into the capture identity and feature receipt. There is no alternate matrix or
lineage option, default repository-fixture fallback, direct data-provider path, live capture,
account, broker, or trading authority. See the [source-rights contract](docs/contracts/source-matrix.md)
and [security-lineage contract](docs/contracts/security-lineage.md).

### Integrated offline decision and PAPER-control contracts

The integration adds a deterministic, fail-closed chain after capture:

1. The feature compiler binds exact evidence IDs, public timestamps, frozen cohort clocks, and
   the lineage-receipt digest; malformed, post-cutoff, or incomplete inputs are rejected.
2. Gate D compares cash, shares, one long option, and a defined-risk debit vertical under one
   frozen policy. It emits a compiled expression or `NO_PACKAGE`; it never places an order or
   promotes a policy.
3. The SQLite-backed risk kernel starts `ENTRY_DISABLED`, requires fresh reconciled PAPER truth,
   then records immutable candidates, reservations, one-use permit authority, and a hash-linked
   Trade Passport. It has no network or broker-mutation interface.
4. The monitored lifecycle persists canonical request intent before any possible mutation and
   verifies broker/account/position truth before a fill or flat result. Its `ClosedMutationGate`
   remains closed in this repository state, and every test uses a fake broker.

`PaperStrategyApplication` is the explicit composition boundary for that chain. Its non-mutating
`prepare` route compiles the captured input, calls the injected bounded-reasoner route, compiles
Gate D, builds the canonical debit-vertical permit, records risk authorization, and returns a
correlated lifecycle plan. Its optional `open_host` route accepts only a preflighted
host-managed MCP session whose PAPER identity matches the permit's frozen official MCP protocol;
the session's capability receipt remains a separate current host attestation. Without an explicit
mutation gate it remains closed. The repository exercises that route only with fake host sessions
and fake reasoner routes.

These contracts are proof of deterministic boundary behavior, not proof of alpha, executable
historical pricing, a live PAPER fill, or any real-money capability.

Given an exact frozen decision, point-in-time evidence-manifest, and feature-input bytes, the pure contract bridge:

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

Before exposing that session to the adapter, the host boundary checks the six required tools from the pinned official surface and reads sanitized account status through `get_account_info`. Missing tools, malformed responses, blocked accounts, and any environment other than PAPER fail closed before a mutation. Runtime calls remain limited to that pinned order, position, and sanitized account-truth surface; secret-like application arguments are rejected before reaching the host.

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

## Offline evidence-to-receipt trace

Render the self-contained read-only walkthrough from the packaged frozen inputs:

```bash
uv run ringdown render-judge-trace --output build/esscher-evidence-trace.html
```

Open `build/esscher-evidence-trace.html` in any modern browser. An installed wheel exposes the same command without `uv run`:

```bash
ringdown render-judge-trace --output esscher-evidence-trace.html
```

The page requires no server, JavaScript, network access, credential, or broker session. It shows one issue #2 point-in-time evidence manifest, then leaves its absent research decision and permit visibly missing. Separately labeled issue #13 synthetic fixtures show terminal-flat, rejected-before-mutation, and manual-reconciliation lifecycle outcomes. Every rendered factual value carries its source JSON pointer; the packaged copies are tested byte-for-byte against the committed source artifacts.

`PAPER`, `INDICATIVE_DATA`, `SYNTHETIC_CONTRACT_FIXTURE`, and `NO_BROKER_EXECUTION` remain visible. A synthetic PAPER P&L example is operational contract evidence only—not alpha, an executable historical fill, or expected profitability.

## Project map

- [Architecture](docs/ARCHITECTURE.md) — implemented behavior, planned boundaries, and invariants.
- [Team onboarding](docs/TEAM_ONBOARDING.md) — required research and lane ownership.
- [Source and claim policy](docs/SOURCE_AND_CLAIM_POLICY.md) — evidence metadata and permitted claims.
- [Point-in-time evidence gate](docs/research/point-in-time-evidence-gate.md) — timing, provenance, residualization, denominator, and options-data contract.
- [Accepted strategy contract](docs/STRATEGY_V1.md) — exact candidates, clocks, features, baselines, thresholds, authority, and unresolved gates.
- [Source-rights contract](docs/contracts/source-matrix.md) — packaged matrix identity, evidence limits, candidate-specific preflight, and offline capture boundary.
- [Security-lineage contract](docs/contracts/security-lineage.md) — CIK-rooted identity, corporate-action evidence, and cutoff-time resolution.
- [Feature-receipt contract](docs/contracts/feature-receipt.md) — deterministic arithmetic, feature registry, clocks, and receipt bindings.
- [Expression tournament](docs/EXPRESSION_TOURNAMENT.md), [risk kernel](docs/RISK_KERNEL.md), and [monitored lifecycle](docs/MONITORED_PAPER_LIFECYCLE.md) — isolated PAPER-control contracts and their fail-closed boundaries.
- [Research-to-permit contract](docs/contracts/research-to-permit.md) — exact schemas, identity mapping, rejection reasons, and frozen policy.
- [PAPER demonstration runbook](docs/PAPER_DEMO_RUNBOOK.md) — read-only preflight, exact approval, bounded mutation envelope, recovery, and receipt interpretation.
- [Scheduled-event runbook](docs/SCHEDULED_EVENT_RUNBOOK.md) — one-shot manifest, dry run, armed invocation, restart reconciliation, and stop conditions.
- [Contributing](CONTRIBUTING.md) — branch, test, review, and safety gates.
- [Changelog](CHANGELOG.md) — versioned behavior changes and release state.
- [Integration manifest](docs/INTEGRATION_MANIFEST.json) — exact reviewed source heads and their final release-train relationship.

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

## Repository status

The canonical repository is `Tempest-Research/esscher-market`. Compatibility interfaces remain
`ringdown-market`, `ringdown_market`, and `ringdown`; this integration neither publishes a
registry package nor enables a provider, broker, or account action.
