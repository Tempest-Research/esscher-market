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
- Production `PAPER_MCP` execution class for issue #90: an explicit second `HostExecutionClass` admitted only through package-owned production plans whose four backends share one content-addressed production binding (`runtime/autonomous_host.py`), a cryptographically separated production broker-truth schema (`esscher.paper_mcp_broker_truth`), and class-specific receipt claims (`PAPER_OPERATIONAL_RESULT` / `PRODUCTION_COMPOSITION` / `PAPER_MCP_HOST_OBSERVATION`); mixed or unbound plans fail closed in both directions and the frozen `SYNTHETIC_FAKE` runner is unchanged.
- Read-only host-operations extension over the identical pinned `alpaca-mcp-server` 2.3.1 artifact: `get_orders` and `get_account_activities` join the guarded session under a separately hashed selection receipt (`esscher.alpaca_mcp_readonly_extension`, re-derived from the pinned wheel through FastMCP `list_tools`), a mutation-impossible `readonly_call` door, and an explicit flatten-authority `risk_reducing_cancel` door; the frozen V2 six-tool mutation protocol and every dependent permit, receipt, and demo artifact remain byte-identical.
- Broker-shaped account-activity acquisition and typed option-event mapping (`sourcedata/alpaca_option_events.py`): ascending `page_token` pagination with cycle and budget fail-closed guards, an fsynced hash-chained cursor journal making restarts replay-safe, byte-identical deduplication with contradictory-duplicate manual routing, and a content-addressed source-versioned mapping contract (`esscher.alpaca_activity_mapping/v1`) over the pinned activity-type vocabulary that routes unknown, unmappable, malformed, or out-of-window records to explicit manual reconciliation instead of guessing; raw position/order payloads normalize into the existing typed option-event and risk-truth vocabularies.
- Production PAPER_MCP composition (`runtime/paper_mcp_composition.py`): host-owned narrow doors (prepared MCP session, owner-approved direct-Kimi route binding, captured feed/source/expression doors, promoted expression policy, wall clock, ledger, close economics, mutation authorization) behind a delayed plan factory that refuses to exist unless the approved route hashes match the armed session, the observed account is the PAPER class, and the account fingerprint matches the authority; broker-truth-first recovery (candidate mutation refused before a STARTUP read-only observation), orphaned working orders resolved only risk-reducing before truth attestation, activity-ingested terminal flat proofs (`esscher.paper_mcp_terminal_flat_proof`), and one durable hash-chained blocked-state journal with a bounded retry budget escalating to `BLOCKED_RETRY_BUDGET_EXHAUSTED` manual reconciliation.
- Read-only broker preflight and its redacted content-addressed receipt (`runtime/paper_preflight.py`, `esscher.broker_preflight_receipt/v1`): PAPER endpoint/account identity bound only as digests, options capability, starting-equity contract, pinned MCP schema/provenance identity, paginated read-only account/order/position/activity queries, flat-start requirement, approved route-config and packaged latency-profile hash binding, stable reason-coded rejections, and an explicit `NO_BROKER_MUTATION` claim enforced by construction through the read-only door.
- Production wall-clock session scheduler (`runtime/paper_scheduler.py`) and CLI: `ringdown paper-run` (explicit `--release`, `--arm`, `--state-dir`, `--ledger`, `--output-dir` paths, byte-cross-checked host invocation, timeline derived from the armed windows plus hard-flat, exactly one injected sleep per observation point, deterministic manual/terminal stops, receipt written to `<output-dir>/paper-run-receipt.json`) and `ringdown paper-preflight` (host-owned session selector, artifact receipt output, exit 0/2/3); a `mutation_permitted=False` composition runs the identical production path as the #91 no-mutation rehearsal, emitting the would-be permit through a `MUTATION_GATE_CLOSED` outcome without any order tool call.

- Owner-approved direct MiniMax-M3 reasoner route V3 (`esscher.reasoner_route` schema_version 3, `minimax_direct` @ `https://api.minimax.chat/v1`, model `MiniMax-M3`) with its owner approval receipt (`reasoner_route_approval_v3.json`, approver MS-Mesh, 2026-09-04) after the Kimi K3 entitlement was withdrawn: probe-verified deterministic wire pins (thinking disabled, `temperature=0`, `top_p=1.0`, `max_tokens` from the frozen policy, `tool_choice=none`, `response_format=json_object` because the provider markdown-fences `json_schema` output), the unchanged frozen six-field cited-decision contract, the unchanged 8s one-call/no-retry policy, and a separate `esscher.direct_minimax_reasoner_model_config` hash domain. Kimi V1/V2 packages remain loadable and dormant as alternates; `load_current_approved_reasoner_route()` is the single owner-switchable current-route accessor consumed by arms, releases, the production composition, and preflight.
- `MinimaxM3ReasonerRoute` host adapter (`strategy/host_route.py`): the first direct-provider adapter wired for the assembled `BoundedDecisionEngine` - exchanges carry the frozen policy-registry route/prompt/output-schema identities and the configured `RouteIdentity` model-config hash, the request identity binds the real V3 artifact and exact provider payload, the provider envelope is unwrapped to the strict decision JSON (markdown-fenced, reasoning-leaking, non-zero `base_resp`, and malformed envelopes are typed `PROVIDER_ERROR`; timeouts are typed `REASONER_TIMEOUT`; never a retry, never a fallback), `MINIMAX_API_KEY` is env-only and discarded at construction, and text ablation fails closed instead of silently misreporting. The production composition factory now enforces `reasoner is approved_route` - no synthetic or drifted double can front the PAPER_MCP engine door.
- Repository hygiene scanner: credential-env assignments with values (`MINIMAX_API_KEY`/`KIMI_API_KEY`/`APCA_API_*`) and Alpaca key identifiers now fail hygiene alongside the existing `sk-*` provider-secret and private-key patterns, with false-positive guards verified for hashes, masked account ids, and documentation name mentions.

### Changed

- The release train version advances from `0.3.1` to `0.4.0`.
- Owner-approved refreeze of the autonomous release policy (`hackathon_autonomous_v1.json` 1.0.0 -> 1.1.0, MS-Mesh, issue #91 governance): the reasoner block pivots to `MINIMAX_DIRECT` / `MiniMax-M3` / `https://api.minimax.chat/v1` / `MINIMAX_API_KEY`; every other owner boundary (PAPER-only, no broker authority, risk caps, memory mode, session clocks) is byte-identical. The V2 risk policy's `owner_policy_sha256`/`constants_source_sha256` bindings were re-pointed at the new accepted policy digest in the same owner action.
- Owner-approved aggressive sizing refreeze (`hackathon_autonomous_v1.json` 1.1.0 -> 1.2.0, MS-Mesh, issue #68 directive): `max_loss_tiers` reordered to `["0.10", "0.05", "0.20"]` so the first (operative) tier sizes one position up to 10% of current equity - a $10,000 max-loss position at the $100k starting equity - with the approved tier value set unchanged and every capacity cap still binding (20% per underlying, 50% aggregate open debit, unborrowed cash, 50% drawdown freeze). The V2 risk policy tiers and owner-sha bindings were re-pointed in the same action; `derived_risk_tier` now yields `TEN_PERCENT`.

### Fixed

- Latent provider-contract defect discovered by the first live direct-provider measurement run (#91): `parse_reasoner_decision` required identifier arrays to arrive sorted, but the frozen system prompt and output schema communicate only uniqueness and cardinality - an uncommunicated rule no real model can satisfy (MiniMax-M3 live samples were rejected for unsorted `evidence_ids` while fully compliant otherwise). Owner-approved clarification (MS-Mesh): provider-output arrays (`evidence_ids`, `unknowns`, contradiction evidence ids) are now canonicalized by sorting - hash-stable, duplicates still hard-rejected, wrong field names/malformed JSON still fail closed, and durable internal artifacts still must arrive pre-sorted (entire existing suite pins the strict paths).
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
- The #90 production `PAPER_MCP` host, activity acquisition, preflight, and scheduler were built and verified entirely offline against fake sessions and synthetic broker-shaped fixtures: no credential was read, no provider, account, broker, or MCP process was contacted, and no order, cancel, exercise, or PAPER mutation occurred. The `paper-preflight` and `paper-run` commands create no authority by themselves: mutation remains impossible without an explicitly armed session under the owner gates in #91 and the separate exact-session authorization in #68, and a green production probe proves repository readiness only — never broker connectivity, fills, flatness-at-close, deployment, or judged P&L.

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
