# Architecture

This document separates implemented behavior from remaining integration work. A roadmap is not a runtime receipt.

## Implemented in the `0.2.0` draft

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
- `contracts/execution_policy.py`: the immutable paper-risk and official MCP protocol registry;
- `contracts/gate_a.py`: strict organizer-fact and sanitized account-capability contracts plus the
        pure fail-closed Gate A evaluator;
=======
- `contracts/gate_a.py`: strict organizer-fact and sanitized account-capability contracts plus the
        pure fail-closed Gate A evaluator;
- `contracts/source_matrix.py`: strict source-rights and point-in-time feasibility matrix deciding
        every required source category before collector implementation;
- `contracts/security_lineage.py`: strict CIK-rooted point-in-time security master and
        corporate-action lineage with fail-closed identity resolution;
- `strategy/contracts.py` feature receipt: strict `esscher.feature_receipt/v1` binding snapshot,
        policy, build identity, cutoffs, evidence IDs, maximum public timestamp, and health;
- `sourcedata/lineage_gate.py`: the capture-boundary gate failing closed on missing, delisted,
        reused, or conflicted lineage;
- `sourcedata/rights_gate.py`: the capture-boundary gate failing closed on blocked, drifted, or
        condition-unmet source classes;
>>>>>>> e4e1b64 (data: decide source rights and point-in-time feasibility (#41))
- `contracts/research_to_permit.py`: the pure frozen-decision, provenance, and feature-dependency bridge;
- `execution/models.py`: immutable opening and closing permits for one debit vertical;
- `execution/host_mcp.py`: the host identity, startup capability/account preflight, sanitized observation, bounded runtime allowlist, and typed transport failures;
- `execution/mcp.py`: the single Alpaca MCP request, readback, cancellation, atomic-close, and event-flat reconciliation boundary;
- `execution/paper_demo.py`: exact approval, durable submit-once recovery, fill-economics classification, and sanitized terminal receipt bundle;
- `runtime/scheduled.py`: strict one-event manifest and due-window validation, cross-process overlap lock, atomic hash-bound restart state, terminal no-op, and manual-reconciliation boundary;
- `demo/judge_trace.py`: deterministic self-contained HTML projection over byte-identical frozen evidence and scheduled lifecycle artifacts, with strict PAPER/claim validation and inert source attribution;
- `cli.py`: labeled research input parsing, one-shot scheduled runtime command, offline trace rendering, deterministic output, and package-version output.

The execution boundary is pinned to Alpaca MCP `2.3.0` at commit `872abbf28dab6cdde7d341fc13ac139b8002d1d9`. The package does not load credentials or instantiate an MCP server. A host must inject one normalized session and attest its PAPER environment from host-owned MCP configuration. The factory verifies the six required tools from the official surface, reads only sanitized account eligibility, then exposes only the adapter's five runtime tools. Its prepared-session object constructs the existing `McpPaperBroker`; no alternate production broker path is introduced. A timed-out mutation is typed as ambiguous so the adapter reads back its deterministic client-order ID instead of submitting again.

The adapter, host boundary, inert paper-demo runner, and one-shot scheduled runtime are contract-tested with injected fake sessions and clocks. The runners cannot mutate without current approval bound to the exact permit and capability observation. Durable deterministic attempt markers force readback rather than resubmission after restart, while the scheduled runtime adds one atomic integrity-checked state record per event and rejects overlapping active events. Terminal repeats trust only a verified sanitized receipt and perform no host-plan or broker call. The repository does not yet contain a sanitized real paper-account receipt, executable historical option prices, or evidence of alpha or profitability.

## Product and machine-interface naming

Esscher is the human-facing product name. The `0.2.0` draft deliberately keeps the `ringdown-market` distribution, `ringdown_market` import package, `ringdown` command and version prefix, configuration keys, report schema keys, and receipt identifiers. The repository remains `Tempest-Research/ringdown-market` until the separately approved post-merge cutover.

The deterministic report keeps the legacy `project: "Ringdown"` value and adds `product_name: "Esscher"` for public display. This is an additive alias; existing schema keys and values remain available.

## Remaining vertical integration

The intended complete path is:

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

The real point-in-time event collection is frozen, and the static proof renderer now projects one v2 evidence manifest plus three separate scheduled lifecycle contract fixtures. No merged artifact causally joins that v2 event to a decision, permit, or terminal receipt, so the page renders those links as missing instead of splicing unrelated values. A future real causally joined public trace remains a separate reviewed artifact. The implemented bridge accepts only exact frozen artifacts that already passed the registered research gates; it does not create evidence, rescore a signal, or open an execution session. The implemented host boundary constructs only a preflighted PAPER session for the same official adapter. Missing or ambiguous information fails closed to rejection or reconciliation; it never selects another adapter.

The strategy policy and its new contracts are implemented, but the real candidate collector,
provider-backed reasoner call, expression compiler, and account reservation ledger are not. The
new validated-decision contract ends at `UP`, `DOWN`, or `UNCERTAIN`. The older
`ringdown.frozen_research_decision/v1` bridge still embeds a synthetic debit-vertical package and
is retained only as inert compatibility infrastructure; it is not the production interface for the
accepted strategy.

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
17. Gate A organizer facts and account capabilities are separate hash-bound artifacts; unknown
        entry-relevant truth yields `ENTRY_DISABLED`, and committed receipts contain no raw account ID.
=======
<<<<<<< HEAD
19. Gate A organizer facts and account capabilities are separate hash-bound artifacts; unknown
    entry-relevant truth yields `ENTRY_DISABLED`, and committed receipts contain no raw account ID.
20. Source rights ambiguity always yields `BLOCKED`; no paid plan is selected without a recorded
    human approval; a capture proceeds only when every required source class maps to a non-blocked
    matrix source whose conditions are declared satisfied.
<<<<<<< HEAD
>>>>>>> d0134c6
21. Security identity is CIK-rooted and never ticker-rooted; a capture proceeds only when the
    event chain resolves as-of the cutoff against an active listing, and missing, reused, or
    conflicted lineage fails closed with a stable reason code.
22. Feature receipts compile only preregistered features, never exceed the decision cutoff,
    and carry no execution-authority field; identical input, policy, and compiler bytes always
    produce byte-identical receipts.

Historical panel admission additionally requires per-feature source dependencies, typed publication timestamps, entitlement metadata, and a common outcome path. The aggregate `DecisionSnapshot` alone does not establish those dependencies; the bridge therefore also requires and validates the exact feature-input and evidence-manifest bytes before issuing a permit.

## Path ownership

- `src/ringdown_market/alpha/`, evidence manifests, and replay fixtures: evidence lane.
- `src/ringdown_market/strategy/`: strategy-contract lane.
- `src/ringdown_market/contracts/`: shared frozen decision, policy, and protocol boundary.
- `src/ringdown_market/execution/`: runtime/integration lane.
- `web/` and public presentation assets: proof/submission lane.
- `.github/`, packaging, shared contracts, and final integration: Ben.

See [CODEOWNERS](../.github/CODEOWNERS) for path ownership and review routing. Branch protection requires pull requests, strict CI, current branches, resolved conversations, and linear history, but does not require a blanket approval count. Independent review remains mandatory when an issue, ownership boundary, or risk level explicitly requires it.
