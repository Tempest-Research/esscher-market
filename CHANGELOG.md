# Changelog

## [0.4.0] - Unreleased

### Added

- Corrected the frozen host-managed reasoner route contract (`esscher.reasoner_route/v1`) and approval receipt (`esscher.reasoner_route_approval/v1`) from obsolete DashScope assumptions to the owner-selected direct `moonshot_direct` / `https://api.moonshot.ai/v1` / `kimi-k3` boundary. The owner approval is recorded separately from operational readiness: V1 rejects route construction with `FROZEN_POLICY_DECODING_INCOMPATIBLE` because its frozen caller decoding cannot truthfully represent K3's provider-fixed effective sampling.
- Frozen p95 execution-latency profile contract (`esscher.latency_profile/v1`) with a `PREREGISTERED` 30,000 ms nearest-rank profile; synthetic placeholders fail evaluation and promotion, and stale or hash-mismatched profiles fail closed.
- Pure direct-Kimi payload and fake-transport boundary (`strategy/host_route.py`) that binds canonical snapshot/evidence/feature data, strict six-field JSON Schema bytes, request identity, one-call/no-retry error mapping, and an 8-second route policy without network access.
- Optional latency-profile binding on `panel.assembler.assemble_panel_report`; real panel manifests now accept `HOST_MEASURED` or `PREREGISTERED` p95 measurement kinds.
- Owner-approved PAPER autonomy policy, typed untrusted-news evidence, a cutoff-safe episodic ledger, and deterministic liquid-universe selection.
- V2 strategy and risk policies, account-relative allocation, an autonomous session coordinator, and typed decision/allocation seams.
- Canonical `StrategyRelease` records with append-only promotion, exact-load, supersession, and revocation behavior, plus a minimal `ArmRecord` handoff for #66.
- Deterministic option assignment, exercise, expiry, sell-out, contract-adjustment, and buying-power-rejection reconciliation with canonical activity coverage, exposure receipts, and an atomic account-global SQLite journal for the focused #66 runtime child.
- Deadline-aware autonomous application service (`runtime/autonomous_application_service.py`) whose nine content-addressed stage receipts bind each prerequisite SHA-256 from the armed session arm through capture, decision, expression, risk, lifecycle open, monitored execution, option-event reconciliation, and terminal binding, with derived p95 stage budgets (`runtime/stage_budgets.py`), canonical operational health receipts (`runtime/health_receipts.py`), durable claim-based duplicate-run suppression, fail-closed deadline exhaustion that retains only the bounded close authority, and deterministic exposure recomputation after assignment/exercise/expiry reconciliation.
- P0 panel evidence over the frozen untouched 23-event / 7-sector Q-FAST universe: a hash-chained append-only prospective signal ledger (`alpha/prospective_ledger.py`, `esscher.prospective_ledger_entry/v1`) that freezes every event before outcome access and rejects late, duplicate, added, removed, or relabeled entries; a source-health gate over all 23 frozen evidence manifests that rejects the panel with validator codes on any non-healthy clock, hash, or lineage gap; canonical panel evidence reports (`alpha/qfast_panel_reports.py`, `esscher.qfast_panel_report/v1` and `esscher.qfast_source_health_gate/v1`) with zero and p95 arms over the identical denominator, abstentions retained at zero signed return, frozen baselines and perturbation-stability controls, four separately reported PnL conventions (outcome-derived signal accuracy, theoretical residual, platform-convention with explicit fee/slippage constants, and fake-execution from linked #66 service fills or explicit `NOT_AVAILABLE`), and a promotion recommendation that binds exactly one content-addressed strategy release sha or rejects with reasons (synthetic decisions plus the preregistered profile always reject); and a full-stack shadow comparison (`alpha/fullstack_shadow_comparison.py`, `esscher.qfast_fullstack_comparison/v1`) sha-linking every panel row to the #66 stage, health, and terminal receipts with explicit divergence findings — all deterministic, offline, synthetic/fake-only, and permanently `NOT_ALPHA_EVIDENCE`.

### Changed

- The release train version advances from `0.3.1` to `0.4.0`.

### Fixed

- Sealed `LifecycleMcpPaperBroker` construction and lifecycle state inside the preflighted host-MCP capability path, so raw or independently guarded sessions, copies, and state retargeting cannot reach a tool call.
- Reworked the Windows capture-output pin to retain replacement-race protection without holding incompatible directory `DELETE` access, restoring atomic publication and repeat capture on Windows.
- Rebound the guarded Alpaca MCP protocol to the published `alpaca-mcp-server` 2.3.1 wheel, source archive, upstream commit, FastMCP runtime, tool inventory, and exact six-schema allowlist instead of relying on repeated in-code constants.
- Canonical artifact hashes now normalize set-like list fields recursively, so order-only changes remain semantically and cryptographically equivalent while raw-byte hashes still expose representation changes.

### Safety

- This internal provenance hardening creates no provider, credential, account, broker, PAPER-mutation, or live-execution capability.
- The reasoner route and latency gate authorize no broker call, PAPER order, provider purchase, probe, deployment, or live call. The host reads only `KIMI_API_KEY`; credentials, account/broker/order data, exception strings, and forbidden sampling fields never enter request payloads, application arguments, or exchange receipts.
- No provider completion, token, measured p95, or successful-inference claim is made. The currently stored host key is an external HTTP 401 `invalid_authentication_error` blocker, and V1 compatibility remains closed even if that credential is replaced.
- The autonomous coordinator and release records are not armed by this change; tests use fakes and no provider, account, broker, or PAPER mutation is performed.
- Option-event receipts consume only synthetic or explicitly host-normalized inputs and permanently disclaim broker-connectivity and alpha evidence; this slice adds no account-activity client, order, cancel, exercise, or PAPER mutation capability.
- Release-candidate verification is automated and synthetic; it does not prove broker readiness, fills, account flatness, executable P&L, or predictive alpha.

## [0.3.0] - Unreleased

### Added

- A deterministic offline chain from point-in-time source-rights and CIK-rooted security lineage through feature receipts, bounded decisions, Gate D expression selection, PAPER risk controls, and monitored lifecycle state reduction.
- Security-lineage receipts that bind issuer, security, listing, corporate-action, and option-adjustment evidence to the decision cutoff; current-ticker fallback, conflicting lineage, and unresolved option adjustments fail closed.
- A feature-receipt contract that binds preregistered features, data health, public-timestamp maximum, evidence IDs, and lineage identity without a provider, model, account, or broker surface.
- A read-only expression tournament plus deterministic compiled-expression boundary, an isolated SQLite WAL risk ledger and hash-linked Trade Passport, and a persisted monitored PAPER lifecycle contract.
- `docs/INTEGRATION_MANIFEST.json`, mapping every reviewed source head in the release train to its final integration relationship.

### Changed

- The canonical repository is `Tempest-Research/esscher-market`; the `ringdown-market` distribution, `ringdown_market` import package, and `ringdown` CLI remain compatibility interfaces.
- The release train version advances from `0.2.0` to `0.3.0` in the canonical `pyproject.toml` source.

### Safety

- Gate A remains `UNVERIFIED`; policy, expression, risk, and lifecycle contracts do not grant data-provider, account, broker, or PAPER-mutation authority.
- All capture, risk, and lifecycle tests use explicit fixtures or fake brokers. The monitored lifecycle's mutation gate remains closed, so this release is not evidence of a live PAPER fill, executable historical option pricing, alpha, or real-money capability.
- Missing, stale, post-cutoff, contradictory, partial, or unresolved evidence and broker truth fail closed to rejection, close-only, or manual reconciliation rather than creating a fallback path.

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

## [0.1.0]

- Initial point-in-time scheduled-earnings evaluation harness.
