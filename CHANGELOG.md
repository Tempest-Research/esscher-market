# Changelog

## [Unreleased]

### Added

- A canonical accepted event-strategy V1 policy with deterministic bytes/hash, strict parsing,
	separate earnings and macro cohorts, frozen features, baselines, partitions, and promotion gates.
- Immutable candidate-manifest, strategy-snapshot, feature-receipt, reasoner-exchange, and
	direction-only decision contracts with a labeled synthetic development bundle.
- Four pre-outcome scheduled-earnings replay manifests bound to one frozen event list and selection rule.
- A strict data-only evidence-manifest v2 validator with event-context provenance and point-in-time gates.
- Versioned, strict frozen-research-decision, point-in-time evidence-manifest, and feature-input contracts.
- Pure deterministic mapping from one eligible research decision to one immutable PAPER debit-vertical permit.
- Exact decision, evidence, input, protocol, and policy lineage in serialized permits and downstream MCP identity checks.
- Inert paper-demo orchestration with exact permit/capability approval, durable submit-once recovery markers, and a bounded official-MCP mutation envelope.
- Sanitized deterministic terminal receipt bundles with broker-observed `ZERO_NO_FILL`, exact gross `PAPER_REALIZED_PNL`, or explicit `PAPER_PNL_UNAVAILABLE` classification.
- One-shot scheduled `PAPER` event execution with strict manifest/due-window identity, atomic restart state, deterministic broker reconciliation, terminal no-ops, overlap rejection, and mutation-free dry run.
- Synthetic contract fixtures for terminal-flat, rejected-before-mutation, and manual-reconciliation scheduled outcomes.
- A deterministic self-contained offline evidence-to-receipt trace with visible JSON-pointer attribution, terminal, rejected, and manual-reconciliation contract views.
- A read-only point-in-time strategy snapshot collector that compiles canonical snapshots and
	feature receipts from permitted primary evidence and synchronized equity market data, bound to
	the accepted event-policy hash and joined through the frozen strategy-input contract.
- Deterministic fake evidence and market-data adapters with one frozen synthetic fixture,
	canonical source and corporate-action receipts, a frozen decimal beta estimator, and an inert
	capture command that requires explicit host authorization and never carries credentials.
- A strategy data-pipeline document fixing exact source classes, clocks, adjustment, entitlement,
	and recovery rules for the collector lane.
- A macro-challenger snapshot lane compiling all twenty frozen macro features from official BLS
	releases, first-vintage fields, explicit revision vectors, and synchronized SPY windows, with
	distinct BLS_JOLTS and BLS_EMPLOYMENT_SITUATION clocks.
- Candidate-specific Gate B data-feasibility manifests with fail-closed FEASIBLE/INFEASIBLE
	verdicts, bound sample-receipt hashes, and a no-trade-authorization macro fallback for an
	infeasible earnings contract.
- Explicit pagination, partial-retrieval, and duplicate-source-record handling in the evidence
  packet with stable fail-closed reason codes, plus distinct BMO and AMC reaction-session selection.
- A frozen source-rights matrix with exact policy and Gate A bindings, development-only evidence
  bundles, candidate-specific rights preflight, and an explicit-fixture offline capture boundary.

### Safety

- Candidate snapshots must match an exact retained manifest record; unknown sources, clock drift,
  post-cutoff evidence, missing conditional features, and reasoner-supplied execution fields fail
  closed before downstream trade construction.
- Gate A remains `UNVERIFIED`, and the accepted strategy policy grants no expression, exit, risk,
  permit, order, account, or broker authority.
- Unknown fields/states, hash or provenance mismatch, post-cutoff dependencies, abstention, failed research gates, unsupported strategy shape, and excess paper risk fail closed before any broker session exists.
- Evidence-manifest v2 remains ineligible for permit compilation and carries no post-cutoff path or outcome value.
- Partial or contradictory package fills stop for reconciliation; missing fees remain explicit and raw broker/account identities never enter the receipt bundle.
- Scheduled unknown, ambiguous, partial, overlapping, or integrity-invalid state fails closed without sequential-leg repair, guessed P&L, or another mutation.
- The packaged trace is static and no-network; it rejects weakened labels or malformed PAPER receipt boundaries, escapes untrusted text, and leaves unsupported decision, permit, receipt, and P&L fields visibly missing.
- Source rights are fixed to one authenticated packaged matrix. Candidate-specific preflight, upstream
  binding checks, strict UTC clocks, and structured paid-plan approval records fail closed before
  any offline snapshot is written; the capture command has no alternate-matrix or live-data path.
- Version impact is `minor`; the package stays at `0.2.0` while issue #11 consolidates the release-train bump to `0.3.0`.

## [0.2.0] - Unreleased

### Added

- Immutable paper-only debit-vertical opening and closing permits.
- Official Alpaca MCP request compilation with deterministic client-order identity.
- Submit-once readback reconciliation for ambiguous MCP responses.
- Cancel-or-close lifecycle handling with broker-backed event-position flatness receipts.

### Safety

- Paper execution is the only supported account mode.
- Filled spreads close as one reversed multi-leg order; partial fills never trigger sequential leg repair.
- Synthetic and indicative data remain ineligible for profitability or executable-fill claims.

### Changed

- Adopted Esscher as the public product name while retaining the `ringdown-market` distribution, `ringdown_market` import package, and `ringdown` CLI for compatibility.
- Retained the legacy report `project: "Ringdown"` value and added `product_name: "Esscher"` as an additive display alias.
- Documented the post-merge repository rename checklist and added a deterministic stale-public-brand check.

## [0.1.0]

- Initial point-in-time scheduled-earnings evaluation harness.