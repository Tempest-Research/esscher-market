# Architecture

This document separates implemented behavior from remaining live-authority gates. A roadmap is not a runtime receipt.

## Implemented in the `0.3.0` integration draft

Esscher has two connected, permanently paper-only planes.

### Research plane

```text
labeled event fixture
        |
        v
point-in-time contracts
        |
        +--> frozen baselines
        |
        v
latency-adjusted residual evaluation
        |
        v
Q-FAST reject-only gate
        |
        v
deterministic JSON report + hashes
```

### Research-to-permit contract

```text
frozen decision + exact evidence/input bytes
        |
        v
strict hash, cutoff, provenance, gate, claim, shape, and risk validation
        |
        +--> invalid or ineligible: deterministic rejection
        |
        v
immutable PAPER debit-vertical permit
        |
        v
existing paper execution plane
```

### Paper execution plane

```text
immutable opening permit
        |
        v
host PAPER identity + capability/account preflight
        |
        v
exact Alpaca MCP multi-leg request
        |
        v
submit once + read back by deterministic client ID
        |
        +--> unfilled: cancel order
        |
        +--> filled: atomic reversed multi-leg close
        |
        +--> partial or unknown: stop for manual reconciliation
        |
        v
get_all_positions broker truth
        |
        v
CANCELED_FLAT or CLOSED_FLAT receipt
```

Current modules:

- `alpha/models.py`: immutable decision and market-path contracts;
- `alpha/evaluation.py`: eligible-path-observation and fill-relative evaluation;
- `alpha/baselines.py`: deterministic frozen comparators, including abstention;
- `alpha/qfast.py`: small-sample rejection and latency gates;
- `strategy/`: the canonical accepted research policy plus strict candidate-manifest, snapshot,
  feature, reasoner-exchange, and direction-only decision contracts;
- `contracts/source_matrix.py` and `sourcedata/rights_gate.py`: the authenticated source-rights
  matrix, upstream binding checks, and candidate-specific capture preflight;
- `contracts/security_lineage.py` and `sourcedata/lineage_gate.py`: CIK-rooted issuer, security,
  listing, and corporate-action lineage at the frozen decision cutoff;
- `sourcedata/capture.py`: an explicit-fixture, offline-only snapshot command with no alternate
  matrix or lineage path, live-provider, account, broker, MCP, or trading path;
- `execution/expression/`: a read-only Gate D tournament and deterministic promoted-expression
  compiler with no account, order, position, or policy-promotion authority;
- `risk/`: an isolated SQLite WAL reservation ledger, fail-closed PAPER risk kernel, and
  append-only hash-linked Trade Passport;
- `lifecycle/`: deterministic monitored PAPER lifecycle state reduction and persisted
  request-intent/reconciliation boundary; its mutation gate remains closed;
- `contracts/execution_policy.py`: the immutable paper-risk and official MCP protocol registry;
- `contracts/research_to_permit.py`: the pure frozen-decision, provenance, and feature-dependency bridge;
- `execution/models.py`: immutable opening and closing permits for one debit vertical;
- `execution/host_mcp.py`: the host identity, startup capability/account preflight, sanitized observation, bounded runtime allowlist, and typed transport failures;
- `execution/mcp.py`: the single Alpaca MCP request, readback, cancellation, atomic-close, and event-flat reconciliation boundary;
- `execution/paper_demo.py`: exact approval, durable submit-once recovery, fill-economics classification, and sanitized terminal receipt bundle;
- `runtime/scheduled.py`: strict one-event manifest and due-window validation, cross-process overlap lock, atomic hash-bound restart state, terminal no-op, and manual-reconciliation boundary;
- `demo/judge_trace.py`: deterministic self-contained HTML projection over byte-identical frozen evidence and scheduled lifecycle artifacts, with strict PAPER/claim validation and inert source attribution;
- `cli.py`: labeled research input parsing, one-shot scheduled runtime command, offline trace rendering, deterministic output, and package-version output.

The execution boundary is pinned to Alpaca MCP `2.3.0` at commit `872abbf28dab6cdde7d341fc13ac139b8002d1d9`. The package does not load credentials or instantiate an MCP server. A host must inject one normalized session and attest its PAPER environment from host-owned MCP configuration. The factory verifies the six required tools from the official surface, reads only sanitized account eligibility, then exposes the legacy frozen-decision broker and the monitored-lifecycle adapter through that same guarded MCP door. No second provider or execution path is introduced. A timed-out mutation is typed as ambiguous so the adapter reads back its deterministic client-order ID instead of submitting again.

The adapter, host boundary, inert paper-demo runner, and one-shot scheduled runtime are contract-tested with injected fake sessions and clocks. The runners cannot mutate without current approval bound to the exact permit and capability observation. Durable deterministic attempt markers force readback rather than resubmission after restart, while the scheduled runtime adds one atomic integrity-checked state record per event and rejects overlapping active events. Terminal repeats trust only a verified sanitized receipt and perform no host-plan or broker call. The repository does not yet contain a sanitized real paper-account receipt, executable historical option prices, or evidence of alpha or profitability.

## Product and machine-interface naming

Esscher is the human-facing product name. The `0.3.0` integration draft deliberately keeps the `ringdown-market` distribution, `ringdown_market` import package, `ringdown` command and version prefix, configuration keys, report schema keys, and receipt identifiers. The canonical repository is `Tempest-Research/esscher-market`.

The deterministic report keeps the legacy `project: "Ringdown"` value and adds `product_name: "Esscher"` for public display. This is an additive alias; existing schema keys and values remain available.

## Vertical integration boundary

The explicit application-service path is:

```text
scheduled event manifest
        |
        v
timestamped issuer/SEC evidence
        |
        v
frozen decision snapshot
        |
        v
residual signal or abstention
        |
        v
risk and execution permit
        |
        v
implemented paper execution plane
        |
        v
sanitized static public trace
```

The application service performs the non-mutating source-capture, bounded-decision, Gate-D,
canonical-permit, risk-authorization, and lifecycle-plan portion of that chain in one causally
bound call. Opening remains a separately invoked operation and defaults to the closed mutation
gate. The real point-in-time event collection is frozen, and the static proof renderer now projects
one v2 evidence manifest plus three separate scheduled lifecycle contract fixtures. No merged
artifact causally joins that v2 event to a decision, permit, or terminal receipt, so the page renders
those links as missing instead of splicing unrelated values. A future real causally joined public
trace remains a separate reviewed artifact. The implemented bridge accepts only exact frozen
artifacts that already passed the registered research gates; it does not create evidence, rescore a
signal, or open an execution session. The implemented host boundary constructs only a preflighted
PAPER session for the same official adapter. Missing or ambiguous information fails closed to
rejection or reconciliation; it never selects another adapter.

The integration now connects the policy-controlled offline collector to CIK-rooted security
lineage, deterministic feature receipts, the Gate D expression boundary, a fail-closed PAPER risk
kernel, and a monitored lifecycle reducer. Each step carries exact hashes and explicit UTC clocks;
missing, stale, contradictory, or post-cutoff truth rejects rather than guessing. The validated
decision remains direction-only, Gate A remains `UNVERIFIED`, and the lifecycle mutation gate is
closed: this repository state has no live provider, account, broker, MCP, or PAPER-mutation
authority. The older `ringdown.frozen_research_decision/v1` bridge remains inert compatibility
infrastructure rather than the accepted strategy's production authority path.

## Core evaluation

For event `i`, Esscher evaluates:

```text
g_i = signal_i * residual_return_i
```

The residual return removes frozen market and sector components measured over the same eligible path-observation window. `UNCERTAIN` maps to zero but remains in the panel. This prevents the system from deleting difficult events after seeing their outcomes.

## Non-negotiable invariants

1. No evidence or feature timestamp may exceed the decision cutoff.
2. Entry starts at the first eligible path observation at or after the modeled latency; the current synthetic path is not proof of an executable fill.
3. Exit is measured relative to the achieved entry, not an idealized cutoff.
4. Candidate and baselines use the same panel, timing, and risk assumptions.
5. Synthetic fixtures cannot be relabeled as historical evidence.
6. Only exact frozen decision, evidence-manifest, feature-input, protocol, and policy identities can produce an opening permit.
7. `ABSTAIN`, `UNCERTAIN`, ineligible, Q-FAST-rejected, and Q-LATENCY-failed decisions cannot produce a permit.
8. A permit ID binds every lineage, timing, risk, mode, instrument, and leg term; post-mapping mutation invalidates it.
9. Execution is permanently paper-account only and uses one pinned official Alpaca MCP adapter.
10. Direct REST, CLI, and second-adapter fallbacks are prohibited.
11. An ambiguous order submission is reconciled by deterministic client ID, never blindly retried.
12. A filled spread closes as one reversed multi-leg order; partial fills never trigger sequential-leg repair.
13. A terminal flat receipt requires broker position truth to contain neither event leg.
14. Public artifacts are static, sanitized, and incapable of mutation.
15. Each scheduled invocation handles one exact `event_run_id`; non-terminal restart reconciles deterministic broker identity, terminal repeats are no-ops, and a second active event is rejected.
16. Local scheduled state is an atomic integrity-checked restart cursor; broker order and position readback remains authority, and ambiguous or partial truth stops for manual reconciliation.
17. Every strategy snapshot binds exact candidate-manifest bytes, and its event, issuer, security,
    ticker, cohort, eligibility, and freeze must match one retained manifest record.
18. A validated strategy decision has direction-only authority; contract, quantity, price, risk,
    account, permit, order, and exit fields are not part of its schema.
19. Capture identity binds one authenticated source matrix and one CIK-rooted security-lineage
    receipt at the exact cutoff; current tickers and caller-selected alternate paths are not trusted.
20. Feature receipts bind the preregistered registry, public-timestamp maximum, decision cutoff,
    evidence IDs, data-health state, and lineage digest; model prose cannot supply arithmetic.
21. Gate D emits a compiled expression or `NO_PACKAGE` only; it has no account, order, position,
    broker, or policy-promotion surface.
22. Risk starts `ENTRY_DISABLED`, accepts only fresh reconciled PAPER truth, and records every
    reservation and permit in an append-only hash-linked passport.
23. Lifecycle intent is persisted before a possible mutation and broker truth is required before
    fill or flat claims; the repository's mutation gate remains closed.

Historical panel admission additionally requires per-feature source dependencies, typed publication timestamps, entitlement metadata, and a common outcome path. The aggregate `DecisionSnapshot` alone does not establish those dependencies; the bridge therefore also requires and validates the exact feature-input and evidence-manifest bytes before issuing a permit.

## Source-rights capture boundary

```text
explicit synthetic fixture + explicit UTC capture clock + explicit host authorization
        |
        v
exact policy candidate selection
        |
        v
packaged source matrix SHA + policy/Gate A rebinding
        |
        +--> unknown, blocked, or unmet source condition: deterministic rejection
        |
        v
offline fixture adapters
        |
        v
canonical snapshot, source/lineage receipts, feature receipt, and feasibility manifest
```

The matrix is a single packaged resource with SHA-256
`888447640aa705510bc0594abc9a78f22c988e961282ff82a6f44337181d04ca`, bound to
accepted-policy SHA-256
`afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca` and Gate A
programme-contract SHA-256
`40c2e780c684bdde671b028dbdd8c9b13268e659c24e98a2d452ff7c8692f955`. The
rights gate evaluates the source classes of the selected candidate, not a
hard-coded earnings lane. Its timestamp parser accepts only explicit
zero-offset UTC values; its paid-plan records require a valid `APPROVED`
decision, a stable identity, and non-inverted approval chronology.

No alternative source-matrix path is exposed by the capture command. The
installed wheel requires an explicit fixture supplied by the caller, and that
loaded fixture is passed into the adapters. `--live` remains an explicit
failure; this module provides no provider session, direct data route, account,
broker, MCP, order, or trading authority.

## Path ownership

- `src/ringdown_market/alpha/`, evidence manifests, and replay fixtures: evidence lane.
- `src/ringdown_market/strategy/`: strategy-contract lane.
- `src/ringdown_market/contracts/`: shared frozen decision, policy, and protocol boundary.
- `src/ringdown_market/execution/`: runtime/integration lane.
- `web/` and public presentation assets: proof/submission lane.
- `.github/`, packaging, shared contracts, and final integration: Ben.

See [CODEOWNERS](../.github/CODEOWNERS) for path ownership and review routing. Branch protection requires pull requests, strict CI, current branches, resolved conversations, and linear history, but does not require a blanket approval count. Independent review remains mandatory when an issue, ownership boundary, or risk level explicitly requires it.
