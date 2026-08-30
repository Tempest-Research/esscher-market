# Changelog

## [Unreleased]

### Added

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
- Frozen Esscher v1 residual-earnings strategy policy (`docs/STRATEGY_V1.md`, `configs/strategy_v1.json`) with a deterministic byte-bound policy hash, strict fail-closed parser, and preregistered evidence thresholds.
- Typed strategy decision contract under `src/ringdown_market/strategy/` with stable abstention codes, code-derived reaction relation, evidence citations, strongest-falsifier record, and lineage hashes.
- Synthetic reasoner-output development fixtures labelled as supplied test inputs, never strategy output.
- Read-only point-in-time strategy snapshot collector (`esscher.strategy_snapshot/v1`) with provenance contracts, synchronized return/beta feature construction, corporate-action receipts, deterministic fake adapters, and an inert credential-rejecting capture command.
- `docs/STRATEGY_DATA_PIPELINE.md` specifying exact source, timing, adjustment, entitlement, and recovery rules for snapshot collection.
- Pure residual decision engine generating source-attributable `UP`/`DOWN`/`UNCERTAIN` decisions from frozen snapshots through one injected reasoner route, with deadline fencing, duplicate-call protection, evidence-bound citations, code-derived reaction relation, and snapshot-baseline construction.
- Structured reasoner output contract (`esscher.reasoner_output/v1`), fake reasoner, route identity, and an inert route-smoke harness recording latency and schema outcomes without broker authority.
- Deterministic quote-safe debit-vertical option package compiler (`esscher.option_chain_snapshot/v1`) emitting one bounded package compatible with the frozen permit boundary or an explicit `NO_PACKAGE` with stable reason codes.
- Account-level PAPER risk kernel with an immutable frozen limit policy, Decimal-only exposure math, a standard-library SQLite WAL reservation ledger, one-use permit bindings, broker-observed truth freshness gates, entry-disable/close-only control states, and idempotent migration of prior event identity.
- Durable monitored PAPER lifecycle runtime driving one event through `APPROVED -> OPEN_SUBMITTED -> OPEN_PARTIAL|OPEN_FILLED|OPEN_CANCELED -> HOLDING -> CLOSE_DUE -> CLOSE_SUBMITTED -> CLOSED_FLAT|MANUAL_REQUIRED` with a 60-minute fill-relative hold, deterministic close, bounded repricing, restart-safe recovery, and sanitized terminal receipts.
- A frozen ex-ante Q-FAST panel selection rule with fixed 20/30 eligible-event bounds, required P0 exclusions, and the frozen claim boundary.
- A strict panel-manifest validator and deterministic fail-closed panel assembler behind the inert `ringdown assemble-panel` command, hash-bound to the strategy-policy, snapshot, and research-decision protocols.
- Synthetic Q-FAST panel fixtures and mutation-negative tests proving leak, exclusion, size, latency, and byte-determinism gates.
- A frozen untouched Q-FAST panel universe: 23 historical BMO/AMC earnings events with EDGAR primary-source provenance, preserved exclusions, and hash-bound synchronized issuer/SPY/sector one-minute window records.
- Frozen confirmation-panel selection rule and honest empty panel manifest bound to the selection-rule and strategy-policy hashes, with a strict panel contract and deterministic zero-latency/p95 Q-FAST report builder (superseded as the historical panel by the 23-event universe above; retained for the prospective confirmation view).
- `docs/research/qfast-confirmation-panel.md` recording the preregistered panel rules and the exact issue-#3 stop conditions that keep the panel at `COLLECTION_INCOMPLETE` with `INSUFFICIENT_DATA`.
- Attributable prospective shadow ledger and read-only orchestrator running the exact frozen strategy, option compiler, and risk kernel end to end without order authority, retaining every abstention, rejection, `NO_PACKAGE`, risk result, and shadow hold in immutable records with deterministic hash-bound reports.

### Safety

- Unknown fields/states, hash or provenance mismatch, post-cutoff dependencies, abstention, failed research gates, unsupported strategy shape, and excess paper risk fail closed before any broker session exists.
- The strategy policy is inert, hash-bound, and frozen: post-freeze mutation, unfrozen documents, unknown fields, duplicate keys, non-finite values, and future timestamps are rejected.
- Strategy decisions cannot carry order, permit, account, or contract fields; every abstention becomes `UNCERTAIN` with stable reason codes and no fallback signal exists.
- Self-reported confidence never authorizes a trade and prose never controls market arithmetic.
- The snapshot collector is read-only and credential-free: post-cutoff evidence, stale observations, unsynchronized windows, missing inputs, non-finite values, and redistribution violations fail closed with stable reason codes, and identical sources plus policy produce byte-identical snapshots.
- The decision engine abstains instead of falling back: policy/snapshot drift, ineligible snapshots, late or hostile reasoner output, unbounded citations, and duplicate calls yield stable `UNCERTAIN` abstentions; reasoner prose never sets contracts, sizing, entry, or exit, and the strategy package imports no execution or runtime surface.
- The option compiler is quote-safe and order-free: stale or skewed quotes, wide spreads, crossed or zero-size quotes, non-positive or oversized debits, ineligible expiries, and malformed or ambiguous chains fail closed to `NO_PACKAGE`; it carries no account, position, mutation, or model authority, and indicative quotes never become executable-fill evidence.
- The risk kernel reserves durable capacity before any mutation: duplicate events or packages, market-opening orders, naked short exposure, stale or missing truth, unknown exposure, budget or entry-limit breaches, and drawdown transitions fail closed with stable reason codes; concurrent reservations cannot double-reserve, permits bind once, and restart resumes from ledger truth.
- The lifecycle runtime persists every transition before side effects, never closes a filled package early, treats broker acknowledgement as non-proof of fills, bounds outages, repricing, and retries, and yields `MANUAL_REQUIRED` rather than a fabricated terminal receipt when flatness cannot be proven.
- The confirmation panel admits no synthetic or post-hoc events: development events are excluded by identity, outcome fields and post-cutoff paths are forbidden at freeze, and a panel below the 20-event floor must declare `COLLECTION_INCOMPLETE` and report `INSUFFICIENT_DATA`.
- The shadow ledger cannot reach broker mutation surfaces, keeps development/confirmation/prospective samples mechanically separated, records abstentions and failures in the denominator, and reports the preregistered evidence threshold as `NOT_MET` rather than inferring success while the confirmation panel stays empty.
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