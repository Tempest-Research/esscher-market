# Independent challenge: Esscher research-to-PAPER architecture

Audit basis: Esscher `main` at `7fca3946f0730e96b6754c463d3aff22b52ccd06`; GitHub issues #26-#33 as retrieved on 2026-08-30; official Alpaca MCP server 2.3.0 at `872abbf28dab6cdde7d341fc13ac139b8002d1d9`.

## Executive verdict

The architectural direction is worth keeping: point-in-time evidence, explicit abstention, deterministic compilation, one-use risk authority, broker reconciliation, PAPER-only operation, and a clean research/production boundary are all strong choices. The proposal nevertheless needs revision before broad implementation because three boundaries are currently conflated:

1. **A directional research decision and an option package are still one contract in the existing bridge.** The current `FrozenResearchDecision` embeds `strategy: FrozenDebitVertical`, and the parser requires underlying, expiry, strikes, quantity, and limit price (`src/ringdown_market/contracts/research_to_permit.py:140-168,333-369`).[4] That directly contradicts the proposed rule that the model has no contract, size, or price authority and the new #29 compiler owns package choice.[8]
2. **Underlying directional evidence and option-trade evidence are different claims.** Current evaluation measures a beta-adjusted underlying return, not option P&L (`src/ringdown_market/alpha/evaluation.py:28-72`).[22] The official MCP surface provides historical stock bars/quotes/trades but only current option quotes/chain snapshots, not a historical option-quote endpoint (`alpaca-mcp/src/alpaca_mcp_server/toolsets.py:65-102`; `README.md:458-489`).[13][14][17] A 20-30-event underlying panel therefore cannot validate the 7-21 DTE vertical's historical economics.
3. **The graph delays the decisive research test until after too much system design.** #3 is the first untouched candidate evaluation, yet it is downstream of #28, while #29-#31 can consume substantial effort before the project learns whether the signal beats simple baselines.[7][8][9][10] A data-feasibility slice and baseline-first confirmation gate should occur earlier.

The safest architecture can still become only a safe broker demo if it lacks a reproducible, sufficiently powered, genuinely held-out research result and a separate option-economics result. Safety engineering is necessary but is not evidence of alpha.

## Source correction

The opening task packet's Alpaca hash `872abbf274c89f25b862a006d2ea836a34dc0d5b` is a transcription error and does not exist in the official repository. The verified 2.3.0/current-main commit is `872abbf28dab6cdde7d341fc13ac139b8002d1d9`, which is also the value already pinned in Esscher (`src/ringdown_market/contracts/execution_policy.py:9-10`; `docs/ARCHITECTURE.md:89`).[1][18] No conclusion below relies on the invalid hash.

## 1. Current-state truth

### What exists

- The repository has a deterministic research harness, not a strategy generator. `DecisionSnapshot` accepts `price_only_score`, `fundamental_score`, `numeric_score`, and a pre-supplied `candidate_signal` (`src/ringdown_market/alpha/models.py:37-75`).[2] The CLI reads that signal from input bytes and evaluates it (`src/ringdown_market/cli.py:88-90,125-129`).
- The research evaluator starts at the first path observation after a modeled latency and computes an underlying log-return residual against frozen market and sector betas (`src/ringdown_market/alpha/evaluation.py:28-62`).[22] It has no bid/ask, quote condition, option IV, option Greeks, option contract, or fill model.
- Q-FAST is explicitly a reject-only heuristic. It reports means, medians, coverage, strongest-baseline difference, and leave-best-event-out mean; at 20+ rows it returns `NOT_REJECTED_SMALL_SAMPLE` when four simple rejection checks do not fire (`src/ringdown_market/alpha/qfast.py:93-170`).[3] It does not calculate sampling uncertainty, a p-value, confidence interval, cluster dependence, multiplicity adjustment, or transaction costs.
- The bridge strongly validates strict JSON, hashes, provenance, cutoff times, feature dependencies, claim labels, Q-FAST/Q-LATENCY status, package geometry, and a fixed $500 debit cap (`src/ringdown_market/contracts/research_to_permit.py:198-242,372-488,948-1005`).[4] This is good integrity work, but the v1 decision contract wrongly includes the option package.
- The existing permit binds decision, evidence, input snapshot, research protocol, execution protocol, and policy hashes, plus exact option legs and price (`src/ringdown_market/execution/models.py:126-204,221-255`).[19] It does **not** yet bind a chain-snapshot hash, compiler-policy hash, package-decision hash, account-state hash, risk-reservation ID/hash, lifecycle-policy hash, or strategy-release approval.
- The current runtime is an immediate open-to-flat demonstration. A host-built plan already contains both opening and closing permits, and after opening readback `run_paper_demo` immediately calls `resolve_to_flat` (`src/ringdown_market/execution/paper_demo.py:129-147,469-509`). #31 correctly says this must not be preserved as the strategy runtime.[10]
- `runtime/scheduled.py` is a one-event restart-safe wrapper around that existing demo, with file-based state and sanitized terminal receipts; the architecture itself says the real 60-minute integrated path and a causally joined real trace remain missing (`docs/ARCHITECTURE.md:91,99-125`).[1]
- The repo already states the honest boundary: no real historical option prices, no alpha/profitability evidence, and no causally joined real evidence-to-receipt trace (`docs/ARCHITECTURE.md:91,125-126`).[1]

### What does not exist

At the audited commit there is no `src/ringdown_market/strategy/`, no collector that creates `esscher.strategy_snapshot/v1`, no generated production-path `UP|DOWN|UNCERTAIN` decision, no deterministic chain compiler, no account-level reservation ledger, no 60-minute lifecycle worker, and no historical/prospective strategy ledger. Those are precisely the deliverables in #27-#32.[6][7][8][9][10][11]

## 2. Architecture and data-flow diagram

```text
RESEARCH / DATA PLANE (no order, account, or position tools)

StrategyPolicyRelease/v1
          |
          v
RawEvidenceEnvelope/v1 ---- MarketObservationBatch/v1
          |                         |
          +------------+------------+
                       v
              StrategySnapshot/v1
                       |
                       v
              FeatureReceipt/v1
                       |
       +---------------+----------------+
       |                                |
       v                                v
Frozen baselines                 ReasonerResponse/v1
       |                                |
       +---------------+----------------+
                       v
               ValidatedDecision/v1
                UP|DOWN|UNCERTAIN
                       |
                       +---------------------------> EvaluationRow/v1
                       |
                       v
               OptionChainSnapshot/v1
                       |
                       v
               PackageDecision/v1
             PACKAGE | NO_PACKAGE
                       |
                       v
               ShadowOutcome/v1

APPROVED RELEASE BOUNDARY

StrategyRelease/v1 = policy + schemas + code/model + data manifests
                   + evaluation reports + explicit approval

PRODUCTION / PAPER PLANE (never trains or changes a release)

Approved StrategyRelease/v1 + current ValidatedDecision/v1
                       + PackageDecision/v1
                       |
                       v
                RiskInputSnapshot/v1
                       |
                       v
                RiskReservation/v1
                       |
                       v
                ExecutionPermit/v2
                       |
                       v
          narrow PAPER-only capability proxy
                       |
                       v
 BrokerCommand/v1 <-> BrokerObservation/v1
                       |
                       v
              LifecycleEvent/v1 ledger
                       |
             60-minute fill-relative clock
                       |
                       v
                TerminalReceipt/v2
             CLOSED_FLAT | MANUAL_REQUIRED
```

The key correction is that `ValidatedDecision/v1` ends at direction/abstention. Contracts, strikes, prices, size, risk, account state, and exits appear only downstream.

## 3. Weakest and contradictory assumptions

### A. Liquidity gate is not yet a preregistered universe rule

#26 defines the universe as supported US-listed optionable common equities priced at least $10, but the challenged architecture adds an “ex-ante liquidity-gated” universe.[5] The gate must specify **when** liquidity is observed, which feed and quote condition counts, whether the gate is based on underlying or options, and whether it is an inclusion rule or merely a `NO_PACKAGE` outcome. If it uses 09:30-09:35 option quotes, it is pre-decision but post-earnings-reaction information; that can select larger, more liquid reactions and change the research estimand. Recommended boundary:

- Freeze the **event universe** from schedule/security/static liquidity information available before the event.
- Record current chain quality after the open as a downstream package-eligibility result.
- Keep every event in the strategy denominator even when it produces `NO_PACKAGE`; report strategy coverage and package coverage separately.

This avoids silently dropping correct/incorrect signals because the option market happened to be poor.

### B. BMO and AMC are not one homogeneous observation problem

#26 puts BMO and AMC events under one 09:30-09:35 observation window.[5] The repository's own evidence policy distinguishes `BEFORE_OPEN`, `AFTER_CLOSE`, `INTRADAY`, and `UNRESOLVED`, and warns that source timing and session boundaries cannot be inferred from a date (`docs/research/point-in-time-evidence-gate.md:152-183`). The final policy must either preregister separate BMO/AMC strata or justify pooling them. Conference-call timing, release-to-open latency, overnight information digestion, and opening-auction behavior differ materially.

### C. Prediction target and traded payoff are mismatched

The hypothesis predicts 60-minute underlying residual direction, while the expression is a 7-21 DTE debit vertical held for 60 minutes.[5] Direction can be right while the spread loses because of IV crush, skew movement, wide spreads, discrete strikes, and crossing costs. Conversely, a wrong underlying sign can still coincide with favorable option repricing. The project needs two separate claims and reports:

1. **Signal claim:** direction/abstention performance on the residual underlying target.
2. **Expression claim:** indicative or PAPER option-package economics under fixed quote/fill rules.

Do not use one as proof of the other.

### D. The existing bridge violates authority separation

The current decision document must contain `strategy`, including exact legs and limit price (`src/ringdown_market/contracts/research_to_permit.py:333-369,372-400`).[4] #29 says the compiler alone chooses the package.[8] This is not a small adapter change; it requires a new decision schema and permit schema. `ringdown.frozen_research_decision/v1` should be retained for old fixtures only and must not be the new production interface.

### E. Q-FAST is an engineering gate, not statistical validation

`NOT_REJECTED_SMALL_SAMPLE` currently means “none of four deterministic rejection conditions fired on at least 20 rows,” not “evidence supports the model” (`src/ringdown_market/alpha/qfast.py:93-170`).[3] That label is honest, but the permit bridge presently requires it per decision (`src/ringdown_market/contracts/research_to_permit.py:948-963`).[4] Panel approval belongs in a versioned `StrategyRelease/v1`; it should not be copied as mutable-looking fields into each event decision.

For 20-30 events, only a simple preregistered rule or fixed external reasoner is defensible as an exploratory/confirmation challenge. Training or tuning a flexible text model, calibrating many thresholds, selecting prompts from outcomes, or comparing many candidate models is statistically unsupported.

### F. Hosted “reasoner route hash” is not a reproducible model release

#28 requires a frozen route/prompt/schema/policy hash, but “same snapshot, policy, and reasoner result” only proves deterministic validation after an external response.[7] It does not prove that rerunning a hosted model tomorrow gives the same response. If a hosted LLM is used, preserve the exact request and response, provider/model identifier, advertised revision, decoding parameters, latency, content hash, and validation result. Treat the raw response as evidence, not as a reproducible model binary. A locally owned trained model release additionally needs exact weights, training code, data split manifests, and environment hashes.

### G. MCP process separation is necessary but incomplete least privilege

The official server supports `ALPACA_PAPER_TRADE=false`, defaults to all toolsets, and offers only toolset-level filtering (`README.md:247-298`).[13] Its `trading` toolset includes order replacement/cancellation, position liquidation, option exercise, and all order overrides (`toolsets.py:21-38`; `server.py:204-210`).[14][15] Esscher's in-process guard narrows runtime calls to five tools after a read-only account preflight (`src/ringdown_market/execution/host_mcp.py:25-37,187-205,226-288`).[20] That is good application-level defense, but the raw broker MCP session still exposes broader mutation authority to its host.

Recommended deployment boundary:

- Data server: launch with only `stock-data,options-data,assets,corporate-actions`; no `account` or `trading` toolset.
- Broker server: launch PAPER-only under a wrapper/proxy that exposes exactly the six preflight/runtime tools Esscher requires, not the full `trading` toolset.
- Bind a sanitized launch-config digest, exact server commit/package hash, tool schemas, base-URL class, and account fingerprint to preflight.
- Reject any config containing live mode; do not merely trust an injected `environment=PAPER` declaration.
- Validate every response locally. The official server constructs OpenAPI tools with `validate_output=False` (`server.py:176-200`).[15]

### H. Historical option data is a hard source constraint

The 2.3.0 server exposes historical stock bars, quotes, and trades with explicit time ranges, adjustments, feed, and `asof` mapping (`market_data_overrides.py:101-155,164-209`).[17] Its option surface lists bars/trades and latest quote/snapshot/chain, but no historical option-quote tool (`toolsets.py:92-102`; `README.md:481-489`).[13][14] Before #32 promises historical package economics, the project must choose one of these honest options:

- license and version a historical OPRA/BBO source through a separate read-only adapter;
- restrict historical claims to underlying residual direction and package availability that can actually be reconstructed; or
- collect prospective option snapshots before outcomes and evaluate only that prospective option sample.

This is a product/research decision for Ben, not an implementation detail.

### I. Fixed policy details need market-mechanics justification

The proposed 2-second quote age/skew, 30% per-leg spread, fixed $2.50/$5 widths, and 7-21 DTE range in #29 are deterministic and safe but currently appear as engineering constants rather than empirically or market-structurally justified choices.[8] Freeze them only after a development-only availability study, with no use of untouched outcomes. Otherwise `NO_PACKAGE` rates may dominate or the policy may select a nonrepresentative subset.

## 4. Exact versioned interface contracts

All contracts should use strict canonical JSON, reject unknown/duplicate/non-finite fields, use UTC timestamps and decimal strings, carry `schema`, `schema_version`, `producer_build_sha256`, `created_at`, and bind parents by raw-byte SHA-256. A schema version changes whenever field meaning or canonicalization changes; policy/model changes receive new release IDs even when the schema does not.

| Contract | Mandatory decision-relevant fields | Producer -> consumer |
|---|---|---|
| `esscher.strategy_policy_release/v1` | `policy_id`, hypothesis, event universe, BMO/AMC strata, cutoff/latency/hold, source classes, feature definitions, label definition, abstention rules, baselines, split manifest hashes, liquidity rule, evidence thresholds, option-expression policy reference, policy hash | #26 -> collector, feature builder, evaluator |
| `esscher.raw_evidence_envelope/v1` | evidence ID, event/issuer/security IDs, source URL/publisher, publication time/type/precision/interval, retrieval/observed times, entitlement/redistribution status, content representation/hash, parser version, field status | source adapters -> snapshot compiler |
| `esscher.market_observation_batch/v1` | instrument ID, venue/feed/entitlement, observation type, event/session/calendar version, timestamps, price/size/condition, adjustment/as-of policy, pagination completeness, raw response hash | data MCP adapter -> snapshot/compiler/evaluator |
| `esscher.strategy_snapshot/v1` | event identity, policy hash, cutoff, event category, raw-evidence hashes, exact observations, explicit unknown/conflict/stale states, corporate-action receipt, eligibility plus reason codes | #27 -> feature builder/#28 |
| `esscher.feature_receipt/v1` | snapshot hash, ordered feature IDs/versions/decimal values, dependency evidence IDs, maximum public time, computed time, parser/normalizer/beta hashes, health state | deterministic feature code -> baselines/reasoner |
| `esscher.reasoner_exchange/v1` | feature/policy/prompt/schema hashes, provider/model/revision, decoding params, request bytes hash, start/end/deadline, raw response hash, cancellation/error state; no market outcome or broker fields | injected reasoner -> validator |
| `esscher.validated_decision/v1` | event/snapshot/feature/policy hashes, `UP|DOWN|UNCERTAIN`, reaction relation, evidence claim refs, strongest falsifier, stable abstention/rejection codes, validator build/hash, decision time; **no package/account/price/size/exit fields** | #28 -> evaluator and #29 |
| `esscher.option_chain_snapshot/v1` | underlying, as-of/capture times, source/feed/entitlement, contract reference metadata, per-contract quote/size/condition/timestamps, completeness/pagination, raw hash, staleness/skew status | read-only options adapter -> #29 |
| `esscher.package_decision/v1` | decision/chain/compiler-policy hashes, `PACKAGE|NO_PACKAGE`, ranked-candidate audit, selected expiry/legs/width/debit/quote timestamps, rejection reason codes, compiler build hash | #29 -> shadow evaluator/risk |
| `esscher.risk_input_snapshot/v1` | account fingerprint hash, observed-at, equity/start equity, cash/buying power, open orders/positions/fills, realized/open/reserved loss, drawdown, data freshness and broker-observation hashes | broker reads + ledger -> #30 risk kernel |
| `esscher.risk_reservation/v1` | reservation ID, event/package/risk-input/policy hashes, maximum loss, daily/period counters before/after, status, created/expires/consumed/released times, transaction/ledger sequence | #30 -> permit compiler |
| `esscher.paper_execution_permit/v2` | release, decision, package, chain, risk input, reservation, risk policy, broker protocol and lifecycle policy hashes; exact one-use order terms; issue/expiry; PAPER mode; nonce/consumption state | risk kernel -> narrow broker adapter |
| `esscher.broker_command/v1` | permit ID, deterministic client order ID, tool name/schema hash, canonical arguments hash, attempt marker persisted-before-side-effect, start/deadline/result classification | lifecycle -> MCP proxy |
| `esscher.broker_observation/v1` | command/client/order correlation, observation time, normalized order/fill/position state, raw-response hash retained privately, sanitization policy hash, ambiguity/staleness codes | MCP proxy -> reconciler/ledger |
| `esscher.lifecycle_event/v1` | monotonic ledger sequence, prior-event hash, state transition, cause, command/observation hashes, fill-relative close due time, operator-control record | #31 state machine -> durable ledger |
| `esscher.terminal_receipt/v2` | full lineage hashes, matched fills/fees or explicit unavailable reason, final order/position observations, final-flat time, lifecycle outcome, limitations/claim labels | reconciler -> audit/#32/#9 |
| `esscher.evaluation_panel/v1` | frozen sample partition, all eligible rows including abstain/no-package/failures, cluster IDs, target and option outcomes separately, baselines, latency profiles, costs, metrics/uncertainty, exclusions, code/data/release hashes | research plane -> release gate |
| `esscher.strategy_release/v1` | policy/schema/code/model/data-split hashes, training receipt if any, historical/prospective reports, route-smoke report, approval identity/time, allowed claim and mode, supersedes/revocation fields | reviewed research -> production loader |

The `StrategyRelease` is the only object production may load. Production must never write a new release, retrain, retune, or change thresholds.

## 5. Recommended sequence and dependency corrections

### Current sequence assessment

#26 first is sound: it preregisters the hypothesis and prevents outcome-driven drift.[5] #27 and #28 can be developed in parallel only after a byte-level golden snapshot contract exists, but #28 should not become mergeable against invented fixtures before the real collector proves the schema is feasible.[6][7] #29 -> #30 -> #31 is logically sound for execution authority.[8][9][10] #32 is overloaded: it combines historical evaluation, prospective collection, and full-stack shadow operation.[11]

### Recommended graph

1. **Split #26 into policy skeleton and final freeze.** First define the hypothesis, labels, partitions, baseline family, authority boundaries, and an amendment rule. Run no outcome evaluation.
2. **Split #27 into `#27A source/data feasibility` and `#27B collector`.** On development-only events, prove issuer timestamp availability, licensing, stock-feed coverage, option-chain coverage, pagination, and BMO/AMC handling. Produce 3-5 byte-frozen golden snapshots. This is feasibility, not tuning.
3. **Finalize #26 after feasibility, before untouched outcomes.** Freeze exact feature/source/liquidity semantics and identify development, untouched confirmation, and prospective event partitions.
4. **Build #27B and #28 against the same golden contract.** Merge collector first; merge decision engine only after byte/schema compatibility against real collector output. Implement frozen no-LLM baselines before any LLM reasoner.
5. **Move #3 immediately after #27B/#28 and make it a research continuation gate.** If data is insufficient or the candidate is clearly uncompetitive, stop or narrow the claim before full runtime investment. #3 remains confirmatory and untouched; feasibility events do not enter it.
6. **Run #29's option-data feasibility subgate in parallel with #3.** Establish whether chain snapshots and package rates are adequate. Do not claim historical option economics without a valid historical quote source.
7. **Proceed #29 -> #30 -> #31 only after Ben chooses the project objective:** (a) research-first quant system requiring a signal gate, or (b) architecture/operations portfolio project that may continue despite inconclusive alpha but says so explicitly.
8. **Split #32:**
   - `#32A prospective signal ledger` starts after #28 release freeze and records every event/abstention immediately, without waiting for broker runtime.
   - `#32B full-stack shadow ledger` starts after #29-#31 and adds package/risk/lifecycle shadow outcomes.
   - Historical confirmation remains #3; do not mix it with prospective records.
9. **Keep #9 last and approval-gated.** Its existing requirement for a generated decision, account reservation, 60-minute hold, and final-flat proof is sound.

This sequence reduces sunk-cost risk without weakening the safety architecture.

## 6. What makes this a quant project rather than a safe broker demo

A good quant project needs all of the following, with failures reported honestly:

- A precise economic hypothesis and target defined before outcomes.
- A reproducible point-in-time dataset with selection/exclusion truth, corporate actions, source entitlement, and no survivorship or timestamp leakage.
- Development, untouched confirmation, and prospective samples mechanically separated by manifest hashes.
- Simple, credible baselines using the same event panel, latency, risk normalization, and costs.
- Cluster-aware uncertainty and sensitivity analysis for repeated issuers, sectors, event dates, and market regimes; effect sizes and intervals, not only pass/fail heuristics.
- Coverage and abstention accounting in the full eligible denominator.
- A clear distinction between underlying-direction evidence and option-expression economics.
- For option claims: timestamped two-sided option observations at entry and exit, quote conditions/sizes, contract identity, IV/skew context, realistic crossing/slippage assumptions, and prospective or broker fill evidence.
- Capacity is not the issue at quantity one, but liquidity, stale quotes, partial fills, and adverse selection still are.
- A frozen release with reproducible code/model/data lineage and an honest claim boundary.

The broker adapter, permits, SQLite ledger, PAPER trade, reconciliation, and polished trace prove engineering and operational discipline. They do not make the strategy quantitatively good.

## 7. AI/ML component triage

### Justified now

- **Deterministic feature construction and validation:** essential.
- **Frozen no-LLM baselines:** essential and should be implemented first.
- **A bounded LLM used as a fixed external classifier/challenger:** potentially justified if its output is fully cited, validated, abstention-heavy, frozen before confirmation, and compared against simpler rules. Call it a fixed reasoner, not a trained Esscher model.
- **Structured text extraction from issuer evidence:** useful if every extracted fact remains linked to exact source bytes and deterministic validation can reject unsupported claims.

### Decorative unless it wins a real gate

- LLM-generated prose explanations that do not change or audit the bounded decision.
- “AI confidence” (already correctly denied authority by #26).[5]
- Agentic self-critique, multiple reasoner routes, or fallback ensembles that add researcher degrees of freedom without held-out evidence.
- A large model wrapper presented as proprietary strategy intelligence.

### Statistically unsupported at the likely sample size

- Training a flexible text model from 20-30 events.
- Tuning prompts, thresholds, feature weights, abstention thresholds, beta policy, and model choice against the same 20-30 events.
- Deep learning, reinforcement learning, online learning, or automated strategy search.
- Reliable calibration by sector/BMO/AMC subgroup on such a small panel.
- Declaring superiority from `NOT_REJECTED_SMALL_SAMPLE` alone.

If Esscher wants an owned learned model, it needs a much larger historically reproducible dataset, grouped train/validation/test splits by issuer and time, fixed preprocessing, frozen model weights, calibration and abstention on development data only, and a genuinely untouched temporal/prospective test. Until then, a preregistered deterministic rule plus a fixed LLM challenger is more credible.

## 8. Minimal vertical slice that proves the architecture

The current repo already proves much of the permit/MCP safety plumbing with fakes. The next slice should prove the missing causal join, not build another demo surface.

### Slice

Use one **development-only** scheduled event and no broker mutation:

1. Capture permitted issuer evidence and stock/SPY/sector observations through the read-only data process.
2. Produce canonical raw envelopes, a real `strategy_snapshot/v1`, and deterministic feature receipt.
3. Run all frozen baselines and one injected fake/frozen reasoner response.
4. Produce a validator-owned `UP|DOWN|UNCERTAIN` decision containing no package fields.
5. Capture one current read-only option chain (or use a byte-frozen, attributable development fixture) and compile `PACKAGE|NO_PACKAGE`.
6. Evaluate the risk kernel against a fake account snapshot and persist a reservation plus `paper_execution_permit/v2`.
7. Drive the existing MCP adapter through a fake broker session and fake clock across the full 60-minute state machine, including restart and final-flat reconciliation.
8. Render one causal trace where every arrow is verified by parent hashes.
9. Repeat from identical source bytes and assert byte-identical deterministic artifacts; separately record the external reasoner exchange rather than pretending it is rerunnable.

### Slice acceptance

- No `candidate_signal`, option package, permit, or close permit is hand-authored downstream of the source snapshot.
- The validated decision has no contract/price/size/account/exit authority.
- A `NO_PACKAGE` or `UNCERTAIN` path remains a successful architectural outcome.
- The data process exposes no account/trading tools; the broker proxy exposes only the exact required allowlist.
- Restart at every state neither duplicates a mutation nor fabricates success.
- The report labels all source/quote/fill limitations.
- One command verifies schema/hash lineage and one test proves no network/broker call in the offline replay.

Only after this slice and the research gate should #9 perform an explicitly approved PAPER mutation.

## 9. Acceptance gates

### Gate A — policy and source feasibility

- Exact event, liquidity, BMO/AMC, cutoff, feature, target, baseline, split, and amendment rules frozen.
- Development-only source matrix demonstrates publication-time precision, entitlement, adjusted stock data, current option-chain availability, and known failure rates.
- No untouched outcome inspected before the final freeze.

### Gate B — contract integrity

- Golden raw -> snapshot -> feature -> decision -> package artifacts validate across #27/#28/#29.
- Schema incompatibility and unknown fields fail closed.
- The old decision-with-package v1 cannot enter the new permit v2 path.
- Every permit binds package, chain, risk reservation, lifecycle policy, and approved strategy release hashes.

### Gate C — research quality

- Candidate, baselines, abstentions, failures, and exclusions share one frozen denominator and latency convention.
- Report includes effect sizes, uncertainty, cluster/sensitivity analysis, and costs.
- Historical underlying and option-expression claims are separate.
- Inconclusive or failed evidence is reported as such and cannot become “validated alpha.”

### Gate D — model/reasoner release

- No outcome-dependent prompt/model/threshold selection on confirmation or prospective data.
- Hosted reasoner request/response and route metadata are frozen; owned model weights/training lineage are complete if applicable.
- Production loads only an approved release and has no training/tuning code path.

### Gate E — MCP and PAPER safety

- Exact official server commit/package/tool schemas pinned.
- Live mode is structurally rejected; PAPER launch config and endpoint class are attested, not merely asserted.
- Read-only and broker processes are isolated; narrow proxy allowlists exact tools.
- Outputs are validated as untrusted; ambiguous mutations reconcile by deterministic client ID.
- SQLite reservation/lifecycle ledger is authoritative; no split-brain file writer remains.

### Gate F — lifecycle truth

- Fill-relative 60-minute timer survives restart/clock jumps.
- Partial/unknown state disables entries but retains close/reconciliation authority.
- `CLOSED_FLAT` requires final broker order and position truth.
- PAPER P&L is emitted only from matched fills and explicit fee availability.

## 10. Contradiction and risk register

| Severity | Risk / contradiction | Evidence | Required resolution or decision |
|---|---|---|---|
| Critical | Decision contract owns option package, violating model authority boundary | `research_to_permit.py:140-168,333-369`[4] | Introduce `validated_decision/v1`, `package_decision/v1`, and permit v2; quarantine legacy v1 |
| Critical | Official MCP can be switched to live; host identity currently attests rather than independently proves PAPER endpoint | Official README `247-267`[13]; `host_mcp.py:210-288`[20] | PAPER-only launcher/proxy, config digest, endpoint/account fingerprint proof, hard reject live config |
| High | Historical underlying target is being connected to option expression without historical option BBO data | `evaluation.py:28-72`[22]; MCP tool surface[13][14] | Separate claims; license historical options data or use prospective-only option evaluation |
| High | First untouched signal result arrives after substantial implementation | #3 dependency graph and #29-#31[8][9][10] | Move #3 research continuation gate immediately after collector/decision engine |
| High | “Ex-ante liquidity universe” is not defined in #26 and may condition sample inclusion on post-event quotes | #26 universe[5], #29 quote rules[8] | Separate event-universe admission from downstream package eligibility |
| High | 20-30 events cannot support flexible ML tuning or robust subgroup claims | Q-FAST design `qfast.py:93-170`[3] | Use fixed simple rules/external challenger; expand data before owned learned model claims |
| High | Existing permit does not bind chain/compiler/risk reservation/lifecycle/release identities | `execution/models.py:126-255`[19] | Permit v2 with full lineage and one-use reservation consumption |
| High | Broker MCP `trading` toolset exposes broader mutations than Esscher needs | `toolsets.py:21-38`; `server.py:204-210`[14][15] | Narrow MCP proxy or individual-tool allowlist outside application object |
| Medium | Q-FAST panel status copied into each decision blurs release approval and event inference | `research_to_permit.py:948-963`[4] | Put panel approval in `strategy_release/v1`; decisions bind release hash |
| Medium | BMO/AMC pooling may hide structurally different information/market regimes | #26[5]; evidence policy event categories | Preregister strata or justify pooling; report both |
| Medium | Hosted reasoner route hash does not make responses reproducible | #28[7] | Freeze exchange evidence and distinguish replayable validation from non-replayable inference |
| Medium | Deterministic option thresholds may create extreme `NO_PACKAGE` selection | #29[8] | Development-only availability study; report package coverage in full denominator |
| Medium | Current immediate-close demo is incompatible with 60-minute runtime | `paper_demo.py:469-509`; #31[10] | New durable lifecycle worker; do not adapt timing by sleeping inside demo |
| Medium | Official server disables output validation at OpenAPI mount | `server.py:176-200`[15] | Strict local normalization and schema validation for every consumed response |
| Low | Documentation tools remain always registered even under toolset filtering | `server.py:212`[15] | Exclude them in proxy where not needed; treat external text as untrusted |

## 11. Decisions that remain Ben's

The architecture can proceed only after Ben chooses, explicitly:

1. Whether Esscher is primarily a **quant research project** (signal gate can stop execution build-out) or an **operational architecture portfolio project** (may continue with inconclusive alpha, but must say so).
2. Whether to obtain a historical options source, accept underlying-only historical claims, or make option economics prospective/PAPER-only.
3. Whether a hosted fixed LLM challenger is acceptable, or whether “owned model” requires locally versioned weights trained only after a much larger dataset exists.
4. Whether BMO and AMC are separate strategies/strata or one pooled policy.

## VERDICT

**VERDICT: REVISE — keep the safety-first plane separation and deterministic authority chain, but split direction from package authority, insert an earlier data/baseline research gate, define liquidity and event strata precisely, harden PAPER/MCP least privilege outside the application wrapper, and separate underlying evidence from option economics before broad build-out.**

## Sources

[1] https://github.com/Tempest-Research/esscher-market/blob/7fca3946f0730e96b6754c463d3aff22b52ccd06/docs/ARCHITECTURE.md
[2] https://github.com/Tempest-Research/esscher-market/blob/7fca3946f0730e96b6754c463d3aff22b52ccd06/src/ringdown_market/alpha/models.py
[3] https://github.com/Tempest-Research/esscher-market/blob/7fca3946f0730e96b6754c463d3aff22b52ccd06/src/ringdown_market/alpha/qfast.py
[4] https://github.com/Tempest-Research/esscher-market/blob/7fca3946f0730e96b6754c463d3aff22b52ccd06/src/ringdown_market/contracts/research_to_permit.py
[5] https://github.com/Tempest-Research/esscher-market/issues/26
[6] https://github.com/Tempest-Research/esscher-market/issues/27
[7] https://github.com/Tempest-Research/esscher-market/issues/28
[8] https://github.com/Tempest-Research/esscher-market/issues/29
[9] https://github.com/Tempest-Research/esscher-market/issues/30
[10] https://github.com/Tempest-Research/esscher-market/issues/31
[11] https://github.com/Tempest-Research/esscher-market/issues/32
[13] https://github.com/alpacahq/alpaca-mcp-server/blob/872abbf28dab6cdde7d341fc13ac139b8002d1d9/README.md
[14] https://github.com/alpacahq/alpaca-mcp-server/blob/872abbf28dab6cdde7d341fc13ac139b8002d1d9/src/alpaca_mcp_server/toolsets.py
[15] https://github.com/alpacahq/alpaca-mcp-server/blob/872abbf28dab6cdde7d341fc13ac139b8002d1d9/src/alpaca_mcp_server/server.py
[17] https://github.com/alpacahq/alpaca-mcp-server/blob/872abbf28dab6cdde7d341fc13ac139b8002d1d9/src/alpaca_mcp_server/market_data_overrides.py
[18] https://github.com/Tempest-Research/esscher-market/blob/7fca3946f0730e96b6754c463d3aff22b52ccd06/src/ringdown_market/contracts/execution_policy.py
[19] https://github.com/Tempest-Research/esscher-market/blob/7fca3946f0730e96b6754c463d3aff22b52ccd06/src/ringdown_market/execution/models.py
[20] https://github.com/Tempest-Research/esscher-market/blob/7fca3946f0730e96b6754c463d3aff22b52ccd06/src/ringdown_market/execution/host_mcp.py
[22] https://github.com/Tempest-Research/esscher-market/blob/7fca3946f0730e96b6754c463d3aff22b52ccd06/src/ringdown_market/alpha/evaluation.py
