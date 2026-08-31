# Changelog

## [Unreleased]

### Added

- Deterministic offline evidence-bundle diff reports for frozen JSON artifacts and directory bundles, exposed through `ringdown_market.audit` and `python -m ringdown_market.audit.bundle_diff` without rerunning research or opening a broker session.
- A monitored PAPER lifecycle worker driving one risk-approved expression through its frozen exit
	plan, with a fail-closed exit-plan clock schema, a durable state machine, a deterministic
	close-permit issuer, an exposure-aware reducer preserving unknown/partial states, restart recovery
	from broker/ledger truth, duplicate-tick prevention, stale-truth and clock-jump fail-closed checks,
	and a flattening-deadline guard. Actual PAPER mutation stays blocked behind the later approval gate;
	the immediate-close demo and fixed 60-minute hold are not preserved.
- A PAPER account risk kernel with an immutable risk-policy schema, Decimal-only exposure
	calculations, a standard-library SQLite WAL reservation ledger, broker-observed truth snapshot
	interfaces, startup and pre-permit reconciliation gates, entry-disabled/close-only/kill/manual
	controls, and an append-only Trade Passport with a deterministic verifier. The kernel enforces
	duplicate-event, aggregate-exposure, per-event-budget, buying-power, concentration, drawdown,
	daily-loss, entry-count, open-expression, close-only-threshold, and stale-clock controls against
	broker-observed truth, with broker PAPER PnL and shadow PnL kept as separate fields.
- A deterministic Gate D expression tournament comparing cash/no-trade, shares, one long option, and
	a defined-risk debit vertical for the same frozen directional decisions, with failures kept in the
	denominator and a canonical `NO_EXPRESSION` outcome when no expression meets the preregistered
	after-cost objective and evidence threshold.
- A production expression compiler consuming one validated UP/DOWN decision, one immutable market
	snapshot, and one frozen promoted-expression policy; it emits one deterministic compiled expression
	or stable `NO_PACKAGE`, never reaches compilation from `UNCERTAIN`, and carries no account, order,
	position, mutation, model, or policy-promotion authority.
- Strict share, option-chain, package, and borrow/locate observation schemas with pinned feed
	identities, quote freshness/skew bounds, crossed/size/spread checks, and indicative-data isolation.
- Read-only adapter documentation with no live capture in unit tests.

- A canonical accepted event-strategy V1 policy with deterministic bytes/hash, strict parsing,
	separate earnings and macro cohorts, frozen features, baselines, partitions, and promotion gates.
- Immutable candidate-manifest, strategy-snapshot, feature-receipt, reasoner-exchange, and
	direction-only decision contracts with a labeled synthetic development bundle.
- A strict, source-grounded Gate A organizer contract and separate sanitized account-capability
	receipt covering competition rules, PAPER identity, account freshness, entitlements, option
	level, multi-leg support, reset state, and required MCP tools.
=======
- A strict, source-grounded Gate A organizer contract and separate sanitized account-capability
	receipt covering competition rules, PAPER identity, account freshness, entitlements, option
	level, multi-leg support, reset state, and required MCP tools.
- A frozen source-rights and point-in-time feasibility matrix deciding all nine required source
	categories before collector implementation, with per-source endpoint, clock, precision,
	revision, depth, adjustment, completeness, entitlement, retention/redistribution, and
	rate-limit records bound to the accepted event policy and Gate A contract digests.
- Five development-only golden bundles proving reproducible capture of EDGAR earnings bytes, a
	BLS JOLTS release with schedule and revision evidence, the official NYSE session calendar, and
	hash-only Alpaca equity and option probe receipts, without consuming the untouched panel.
- A fail-closed capture-boundary rights gate requiring every required earnings source class to map
	to a non-blocked matrix source with all conditions declared satisfied before any snapshot.
>>>>>>> d0134c6
- A CIK-rooted point-in-time security master and corporate-action lineage contract covering issuer,
	security, listing, and event identities, symbol changes, mergers, spinoffs, splits, dividends,
	and OCC-anchored option adjustments, with four byte-pinned development-partition evidence
	receipts and an OCC access-boundary receipt.
- A fail-closed capture-boundary lineage gate rejecting missing, delisted, reused, or conflicted
	lineage with stable reason codes and binding the lineage digest into the capture identity.
- An extended deterministic feature-receipt boundary binding decision cutoff, maximum public
	timestamp, data health, evidence IDs, and the issue #42 lineage receipt digest, with exact
	preregistered-feature registry enforcement, BMO/AMC/macro clock separation, and negative
	network/LLM/broker capability proofs.
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
- A frozen ex-ante Q-FAST panel selection rule with fixed 20/30 eligible-event bounds, required P0 exclusions, and the frozen claim boundary.
- A strict panel-manifest validator and deterministic fail-closed panel assembler behind the inert `ringdown assemble-panel` command, hash-bound to the strategy-policy, snapshot, and research-decision protocols.
- Synthetic Q-FAST panel fixtures and mutation-negative tests proving leak, exclusion, size, latency, and byte-determinism gates.
- A frozen untouched Q-FAST panel universe: 23 historical BMO/AMC earnings events with EDGAR primary-source provenance, preserved exclusions, and hash-bound synchronized issuer/SPY/sector one-minute window records.
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

### Safety

- Candidate snapshots must match an exact retained manifest record; unknown sources, clock drift,
  post-cutoff evidence, missing conditional features, and reasoner-supplied execution fields fail
  closed before downstream trade construction.
- Gate A remains `UNVERIFIED`, and the accepted strategy policy grants no expression, exit, risk,
  permit, order, account, or broker authority.
- Unknown or stale mark, cost, leverage, flattening, entitlement, endpoint, option-level,
  multi-leg, account-freshness, reset, balance, or MCP-tool truth yields `ENTRY_DISABLED`; no raw
  account identifier or credential is accepted.
=======
<<<<<<< HEAD
- Unknown or stale mark, cost, leverage, flattening, entitlement, endpoint, option-level,
  multi-leg, account-freshness, reset, balance, or MCP-tool truth yields `ENTRY_DISABLED`; no raw
  account identifier or credential is accepted.
- Ambiguous source rights always yield `BLOCKED`; paid plans without recorded human approval stay
  blocked; missing, drifted, or condition-unmet source matrices fail closed before any snapshot.
- Licensed market-data probe bytes are never committed; Alpaca golden bundles retain only
  sanitized endpoint paths, statuses, byte counts, and SHA-256 receipts.
<<<<<<< HEAD
>>>>>>> d0134c6
- Security identity is never ticker-rooted; reused symbols, delisted listings, conflicting
  lineage, and splits without OCC option adjustments fail closed before any snapshot.
- Feature receipts compile only preregistered features, never exceed the decision cutoff, carry
  no execution-authority field, and admit no network, LLM, account, order, position, or broker
  capability.
- Unknown fields/states, hash or provenance mismatch, post-cutoff dependencies, abstention, failed research gates, unsupported strategy shape, and excess paper risk fail closed before any broker session exists.
- Source-health audits return deterministic findings for malformed `redistribution_status` values and incomplete `published_at_interval` objects instead of raising.
- Evidence-manifest v2 remains ineligible for permit compilation and carries no post-cutoff path or outcome value.
- Partial or contradictory package fills stop for reconciliation; missing fees remain explicit and raw broker/account identities never enter the receipt bundle.
- Scheduled unknown, ambiguous, partial, overlapping, or integrity-invalid state fails closed without sequential-leg repair, guessed P&L, or another mutation.
- The packaged trace is static and no-network; it rejects weakened labels or malformed PAPER receipt boundaries, escapes untrusted text, and leaves unsupported decision, permit, receipt, and P&L fields visibly missing.
- Real Q-FAST panel assembly fails closed with `UPSTREAM_CONTRACT_MISSING` while issues #26, #27, and #28 remain unmerged and unregistered.
- P0 contract-development events, sub-20 or above-30 event panels, unmeasured p95 latency profiles, and post-cutoff or post-freeze timestamps fail closed with stable reason codes before any evaluation runs.
- Historical panel evidence is admitted only through EDGAR-accessioned preservation anchors with every publication bound preceding the decision cutoff; raw licensed market bars stay host-side and only metadata and SHA-256 bindings are redistributed.
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