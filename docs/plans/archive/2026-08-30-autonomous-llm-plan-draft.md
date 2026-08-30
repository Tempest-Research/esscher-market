# Esscher autonomous LLM research-to-PAPER plan

**Status:** proposed architecture, not implementation authority
**Date:** 2026-08-30
**Canonical source audited:** `Tempest-Research/esscher-market` at `7fca3946f0730e96b6754c463d3aff22b52ccd06`
**Permanent execution boundary:** Alpaca PAPER only

## Decision summary

1. Use a provider-neutral `ReasonerProvider` interface with **Kimi K3 as the first hosted runtime implementation**. Use a Kimi API key held by the runtime secret store; never commit or paste it. Do not make a personal Codex OAuth session a production dependency.
2. Do not train an Esscher model for v1. First implement deterministic features and baselines, then one fixed LLM reasoner that can emit only `UP`, `DOWN`, or `UNCERTAIN` with evidence references and one strongest falsifier.
3. Preserve autonomy at the **system** level. The scheduler, collectors, reasoner, validator, package compiler, risk kernel, execution proxy, lifecycle worker, and reconciler run without a human choosing each event. Autonomy does not give the LLM broker authority.
4. Freeze the event universe before outcomes. Separate event eligibility from post-open option-package availability. Every eligible event remains in the denominator, including `UNCERTAIN`, `NO_PACKAGE`, data failures, and risk rejections.
5. Treat BMO and AMC as separate preregistered strata. They may share code and a policy family but must not be pooled silently.
6. Separate two claims:
   - underlying residual-direction quality;
   - option-package economics and execution quality.
7. For v1, use historical data to evaluate deterministic market/fundamental baselines and the data pipeline. Treat Kimi historical classifications as exploratory unless training-cutoff contamination can be ruled out. Use a post-freeze prospective ledger as the decisive LLM evidence surface.
8. Unless a licensed historical option BBO source is acquired, make option economics prospective/PAPER-only. Do not convert historical stock direction into invented option P&L.
9. Replace the legacy decision contract that embeds option legs with a direction-only decision contract. A deterministic downstream compiler owns expiry, strikes, debit, quantity, package rejection, risk, permits, and exits.
10. Do not create 50–100 flat issues. Use roughly 22 bounded work items in a dependency graph, with Alex owning contracts/strategy/runtime and Yaro owning sources/data/evaluation. One shared file has one owner.

## Why the ex-ante universe is beneficial

The ex-ante universe does not manufacture alpha. It makes the experiment valid and the runtime operable.

- It prevents survivorship bias: historical eligibility is based on what was known then, not today’s surviving tickers.
- It prevents outcome selection: a stock cannot be added because its earnings move later looked attractive.
- It gives an honest denominator: poor option liquidity becomes `NO_PACKAGE`, not a deleted losing or inconvenient event.
- It bounds operational workload and data-quality requirements.
- It makes historical, untouched, prospective, and PAPER results comparable under one rule.
- Static pre-event liquidity can reduce names that are structurally impossible to trade. Post-open option quote quality must remain a downstream package result so it does not redefine the event sample after the reaction is visible.

The first feasibility study should measure coverage before freezing exact liquidity thresholds. Current `$10`, optionability, 7–21 DTE, 2-second quote age/skew, 30% leg spread, and `$2.50/$5.00` width values are safe deterministic candidates, not yet empirical truths.

## Runtime architecture

```text
AUTONOMOUS ORCHESTRATOR
        |
        v
Calendar + security-master freeze
        |
        v
Read-only collectors
  issuer IR / SEC / permitted news
  stock / SPY / sector observations
        |
        v
RawEvidenceEnvelope + MarketObservationBatch
        |
        v
Deterministic snapshot + feature compiler
        |                          \
        |                           -> frozen no-LLM baselines
        v
Bounded Kimi K3 reasoner
  one snapshot, fixed prompt/schema
  no outcome, options, account, or broker tools
        |
        v
Deterministic decision validator
  UP | DOWN | UNCERTAIN
  evidence IDs + strongest falsifier
        |
        +----------> research/evaluation ledger
        |
        v
Read-only option-chain snapshot
        |
        v
Deterministic package compiler
  PACKAGE | NO_PACKAGE
        |
        v
Risk kernel + durable reservation
        |
        v
One-use PAPER permit v2
        |
        v
Narrow PAPER broker proxy
        |
        v
60-minute lifecycle + reconciliation
        |
        v
CLOSED_FLAT | MANUAL_REQUIRED receipt
```

Production loads only an approved `StrategyRelease`. It cannot train, tune, rewrite prompts, change thresholds, or create releases.

## Authority matrix

### The LLM may

- synthesize the frozen issuer/SEC/news evidence and deterministic features;
- identify support and contradiction;
- choose `UP`, `DOWN`, or `UNCERTAIN`;
- cite only supplied evidence IDs;
- name one strongest falsifier;
- request a bounded read-only evidence excerpt if the snapshot deliberately omits full text.

### The LLM may not

- change the event universe or information cutoff;
- call generic web search at decision time;
- read future prices or realised outcomes;
- see or choose option contracts, strikes, expiry, quantity, limit price, account, risk budget, or exit;
- call the Alpaca trading MCP or hold broker credentials;
- retry through a hidden model fallback;
- report self-confidence as order authority;
- modify its prompt, policy, tools, or model route.

### Deterministic code owns

- schedule and eligibility;
- timestamp/entitlement/data-health checks;
- arithmetic and feature construction;
- schema/citation validation and abstention;
- option-chain eligibility and package ranking;
- risk, reservations, idempotency and permits;
- order lifecycle, 60-minute exit, reconciliation and flat proof;
- release approval and revocation.

This remains genuinely autonomous: no human chooses the trade. It is also auditable: stochastic reasoning is contained inside one evidence-producing component rather than smeared across collection, risk, and execution.

## Provider decision

### Recommended v1

- Interface: `ReasonerProvider` protocol.
- First adapter: Kimi K3 through its OpenAI-compatible HTTP API.
- Runtime model field: `kimi-k3`.
- Start with a measured reasoning effort; do not accept the documented default `max` without p50/p95 latency and cost evidence.
- Secret: `MOONSHOT_API_KEY` supplied by the runtime secret store/environment, never repository configuration.
- One configured route only. Provider failure, timeout, rate limit, malformed JSON, unsupported citation, or deadline breach returns `UNCERTAIN`.

### Why not Codex OAuth in production

OpenAI documents ChatGPT sign-in and API-key sign-in as local-person workflows, supports device auth for headless sessions, and explicitly says to use API-key authentication for programmatic Codex CLI workflows such as CI/CD. A personal OAuth cache is renewable interactive identity, not a clean project service credential. It couples Esscher availability to Ben’s ChatGPT session, subscription state, token lifecycle, and Codex CLI behavior.

Codex OAuth remains useful for development, code generation, and offline research experiments. It should not sit on the causal path between an earnings event and a PAPER decision. If OpenAI is selected as a runtime provider later, use an OpenAI Platform project/service credential or approved enterprise workload identity, not Ben’s personal Codex session.

## Reasoner exchange contract

`esscher.reasoner_exchange/v1` should record:

- event, snapshot, feature, policy, prompt, schema, and strategy-release hashes;
- provider, requested model, returned model/revision when available;
- decoding and reasoning parameters;
- canonical request bytes hash and private raw request receipt;
- start, deadline, end, latency, cancellation/error classification;
- canonical raw-response hash and private raw response;
- validator result and stable rejection/abstention code;
- token usage and bounded cost receipt when the provider returns it.

The hosted model is not assumed reproducible. The exact request/response is evidence. Re-running `kimi-k3` tomorrow may not prove the same model binary was used, so provider canaries and release revocation are required.

Suggested model output:

```json
{
  "decision": "UP | DOWN | UNCERTAIN",
  "claim_evidence_ids": ["evidence-id"],
  "strongest_falsifier_evidence_id": "evidence-id | null",
  "unknowns": ["stable reason"],
  "summary": "bounded explanation"
}
```

No confidence score controls execution. The validator recomputes all arithmetic, verifies every evidence ID, rejects unsupported claims, and converts any ambiguity to `UNCERTAIN`.

## Historical and prospective validation

Using a hosted LLM removes training; it does not remove validation.

### Survivorship-safe panel

For every historical decision date, rebuild eligibility from an as-of security master and event calendar. Preserve issuer IDs, ticker changes, splits, delistings, exclusions, reschedules, unknown event timing, and every eligible abstention. Never start from today’s stock list.

### Walk-forward evaluation

At each chronological cut:

1. use only earlier events for development;
2. freeze the policy/prompt/feature release;
3. evaluate later events without changing it;
4. apply a purge/embargo around overlapping event/holding windows;
5. group repeated issuers and event episodes when estimating uncertainty.

Random train/test splits are not acceptable for repeated earnings events.

### Hosted-LLM contamination caveat

A current general LLM may already know historical company events and subsequent outcomes from pretraining. A point-in-time prompt alone does not erase that internal knowledge. Unless the provider discloses a defensible training cutoff and the evaluated events are later than it, historical Kimi decisions are exploratory rather than untouched alpha evidence.

Therefore:

- historical panels primarily validate data construction, deterministic features, simple baselines, coverage and latency;
- Kimi is evaluated for schema/citation/abstention behavior on development cases;
- post-freeze prospective events provide the cleanest reasoner evidence;
- no prompt/model shopping occurs after prospective collection starts.

## Data-plane scope

1. **Calendar/security master:** daily frozen eligible-event manifest, stable issuer/security IDs, BMO/AMC classification, reschedule/conflict state, corporate-action mapping.
2. **Issuer/SEC evidence:** IR release, 8-K/exhibit, accession/acceptance timestamp, raw bytes/hash, publication precision and entitlement.
3. **Permitted news:** pre-cutoff only, publisher timestamp rather than retrieval time, deduplicated, source class separated from primary evidence, raw payload retained only where rights allow.
4. **Consensus:** optional until a licensed point-in-time source exists. Never use today’s revised consensus as historical truth.
5. **Market context:** synchronized stock/SPY/sector bars/quotes under one feed, adjustment, timezone and pagination policy.
6. **Deterministic features:** surprise vectors when defensible, guidance sign/change, opening residual, pre-event beta, liquidity/data-health receipts, BMO/AMC stratum.
7. **Option data:** current/prospective chain snapshots first. Historical option P&L requires licensed timestamped BBO data or remains out of scope.

## Two-owner GitHub work graph

The existing issues #26–#33 are directionally correct but too broad. Rewrite/split them rather than create duplicate parallel graphs.

### Phase 0 — clear the gate

1. **Alex:** repair or close PR #25. If repaired: canonical hashing must be order-invariant where semantics are set-like, Q-FAST cross-field contradictions must downgrade claims, and regressions must pass.
2. **Yaro:** development-only source/entitlement feasibility matrix. Check calendar precision, issuer/SEC availability, permitted news, consensus options, stock feed coverage, option-chain coverage, and BMO/AMC handling. No outcome tuning.

These can run in parallel.

### Phase 1 — freeze architecture

3. **Alex:** policy skeleton: target, strata, information cutoff, authority boundary, baselines, partitions, amendment rule.
4. **Alex:** contract v2 spine: raw evidence, observations, snapshot, feature receipt, reasoner exchange, validated direction-only decision, package decision, strategy release and permit v2 lineage. Quarantine legacy decision-with-package v1.
5. **Yaro:** produce 3–5 development-only byte-frozen golden raw/observation examples against the skeleton.
6. **Alex:** final policy freeze after feasibility. Exact source semantics, feature definitions, package-availability study rules and partition manifests become immutable before untouched outcomes.

### Phase 2 — parallel research and reasoner lanes

7. **Yaro:** automated earnings calendar/security-master collector.
8. **Yaro:** issuer IR + SEC raw-evidence collector.
9. **Yaro:** permitted-news ingestion and deduplication.
10. **Yaro:** stock/SPY/sector collector with feed/adjustment/pagination receipts.
11. **Yaro:** snapshot compiler plus golden integration fixtures.
12. **Alex:** deterministic feature compiler and frozen no-LLM baselines.
13. **Alex:** provider-neutral reasoner interface plus fake reasoner.
14. **Alex:** Kimi K3 adapter with JSON, deadline, token/cost, retry and cancellation receipts; no broker tools.
15. **Alex:** deterministic decision validator and strategy-release loader/revoker.

Collector must merge before the decision lane is accepted against real snapshot bytes.

### Phase 3 — early research continuation gate

16. **Yaro:** expand #3 into the survivorship-safe historical underlying panel, untouched manifest, baseline report, clustered uncertainty and honest denominator. Historical Kimi output remains exploratory unless contamination is ruled out.
17. **Yaro:** start the prospective signal ledger immediately after the strategy/reasoner release freezes. Do not wait for execution runtime.
18. **Yaro:** option-data availability study and prospective chain capture. Quantify `NO_PACKAGE` rates before freezing package constants.

Failure or inconclusive evidence can stop strategy claims. It must not be laundered into “validated AI alpha.”

### Phase 4 — deterministic expression and runtime

19. **Alex:** direction-to-debit-vertical package compiler using frozen prospective/current chain snapshots.
20. **Alex:** account PAPER risk kernel, SQLite WAL reservations and permit v2.
21. **Alex:** narrow PAPER-only Alpaca MCP proxy plus durable 60-minute lifecycle/reconciliation worker.
22. **Yaro:** full-stack shadow ledger joining decision, package, risk, hypothetical lifecycle and limitations.

### Phase 5 — proof

23. **Alex integration owner; Yaro evidence owner:** offline causal vertical slice from raw bytes through fake broker final-flat receipt. No hand-authored signal/package/permit; restart/fault tests; deterministic lineage.
24. **Ben approval gate:** one strategy-generated Alpaca PAPER open-to-flat lifecycle under #9. No second autonomous attempt is implied.

This is 24 bounded work items including the existing final gate, not 100 flat tickets. If an item cannot be explained as one contract, one component, or one proof surface, it is not ready to become an issue.

## What can be completed today

With Alex and Yaro working in parallel, today can plausibly produce:

- PR #25 resolution;
- source feasibility evidence;
- policy and contract skeletons;
- golden development snapshots;
- collector/provider/validator scaffolding with fakes;
- a non-mutating offline causal slice if dependencies land cleanly.

Today cannot honestly produce:

- a survivorship-safe multi-year panel with settled licensing by force of issue count;
- untouched or prospective evidence gathered after a freeze;
- proof that Kimi adds alpha;
- historical option economics without a legitimate quote source;
- a qualifying strategy-generated PAPER trade while markets/events do not permit one;
- profitability.

Speed can compress engineering. It cannot compress chronology, market calendars, data rights, or an untouched sample without destroying the evidence.

## Acceptance gates

1. **Policy/source:** frozen rules and 3–5 real development golden snapshots; no untouched outcomes seen.
2. **Contract:** direction-only decision; legacy decision/package cannot enter permit v2; strict canonical hashes.
3. **Reasoner:** Kimi exchange is bounded, cited, deadline-aware and fail-closed; no broker/account/option authority.
4. **Research:** full denominator, simple baselines, leakage-safe chronology, clustered uncertainty, underlying and option claims separate.
5. **Option feasibility:** package coverage and quote quality measured prospectively or from a licensed historical source.
6. **Risk/runtime:** one-use reservations, PAPER attestation, narrow MCP proxy, restart-safe state machine, broker reconciliation.
7. **Offline slice:** raw evidence causally reaches final-flat fake receipt with no hand-authored downstream artifact.
8. **PAPER:** explicit approval, one generated lifecycle, matched broker observations, 60-minute fill-relative exit, and broker-proven flat state.

## Sources

- OpenAI authentication: <https://learn.chatgpt.com/docs/auth>
- OpenAI production best practices: <https://developers.openai.com/api/docs/guides/production-best-practices>
- Kimi API quickstart: <https://platform.kimi.ai/docs/overview>
- Kimi model list: <https://platform.kimi.ai/docs/models>
- Kimi JSON mode: <https://platform.kimi.ai/docs/guide/use-json-mode-feature-of-kimi-api>
- Kimi K3 tool-calling guidance: <https://platform.kimi.ai/docs/guide/kimi-k3-tool-calling-best-practice>
- Esscher issues #3, #9 and #26–#33: <https://github.com/Tempest-Research/esscher-market/issues>
- Independent quant/data/AI memo: Kanban `t_4e94d91f`
- Independent architecture challenge: Kanban `t_9291814f`
- Official Alpaca MCP source pin: `alpacahq/alpaca-mcp-server@872abbf28dab6cdde7d341fc13ac139b8002d1d9`
