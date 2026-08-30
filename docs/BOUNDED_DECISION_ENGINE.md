# Bounded decision engine and Gate C baselines

Deterministic, offline-first strategy services that generate source-attributable `UP`, `DOWN`, or `UNCERTAIN` records from one frozen strategy snapshot, and implement the matched Gate C baseline arms. This slice closes Issue #28 on top of the frozen strategy contract (#26) and the point-in-time snapshot collector (#27). It never reads a hand-authored `candidate_signal` fixture as production strategy output.

A decision from this package is **engineering evidence only**: it carries `authority: "DIRECTION_ONLY"` and cannot set symbol eligibility, instrument, contract, quantity, price, account, risk, entry, exit, or broker action. Claim levels follow the [source and claim policy](SOURCE_AND_CLAIM_POLICY.md#claim-levels); the accepted candidate, clocks, thresholds, and baselines are frozen in [STRATEGY_V1.md](STRATEGY_V1.md) and the packaged policy bytes.

## Boundary

- Pure services under `src/ringdown_market/strategy/`: `reasoner.py`, `engine.py`, `baselines.py`, `smoke.py`.
- Stdlib-only; no DNS, HTTP, browser, MCP, broker, account, position, or order capability. Tests prove this with an AST import scan and socket denial.
- No expression selection, account risk, untouched-panel tuning, PAPER order, demo, or submission work. Those belong to #29, #30, #3, and #31 respectively.
- Feature arithmetic and feature-receipt compilation remain owned by the snapshot collector (#27) and the feature-receipt slice (#43); this package consumes `StrategyInput` and never imputes a feature.

## Decision contract

The engine orchestrates the readable trace `INPUT -> FEATURE -> REASONER/BASELINE -> VALIDATOR/VETO -> OUTPUT` with stable reason codes:

1. **INPUT** binds policy, candidate manifest, strategy snapshot, and feature receipt SHA-256 identities.
2. **FEATURE** records data health, eligibility, and the ordered frozen feature set before any reasoner call.
3. **REASONER** invokes the injected route at most once, bounded by the frozen route, prompt, output-schema, and model-config hashes.
4. **VALIDATOR/VETO** applies the frozen #26 validator: eligibility, data health, exchange identity, clock fencing, citation, unknown-code, and confirmation gates.
5. **OUTPUT** is the canonical `esscher.validated_decision/v1` record plus a canonical `esscher.decision_trace/v1` byte string.

Deterministic validation runs before any reasoner call. Ineligible or unhealthy snapshots, starts before the feature receipt or after the decision cutoff, and duplicate invocations abort with a recorded `CANCELED` exchange and a stable engine reason code; the route is never invoked.

## Reasoner route

`ReasonerRoute` is a structured, injected, provider-neutral protocol. A request carries the joined input, a supplied start time, and an optional text-ablation flag; a result carries the immutable `esscher.reasoner_exchange/v1` receipt and the exact raw provider bytes. There is no transparent fallback: timeout, cancellation, provider error, late response, malformed JSON, hostile fields, and raw-hash drift all surface as `UNCERTAIN` with stable reason codes through the validator.

`DeterministicFakeReasoner` is the offline test double; every test uses fakes. Paid or live reasoner calls require a separate explicit approval and receipt outside this package; the frozen call policy (one call, zero retries, 8-second hard timeout, temperature 0, 512 output tokens) is enforced by the validator and mirrored by the engine deadline fence.

## Matched Gate C arms

`compile_gate_c_signals` emits one canonical `esscher.baseline_signal/v1` per frozen policy baseline, in frozen policy order, for the same snapshot and cutoff, plus 256 seeded placebo controls:

| Baseline | Rule | Evaluation-only |
| --- | --- | --- |
| `CASH_ALWAYS_UNCERTAIN` | Always `UNCERTAIN`, retained in the denominator. | no |
| `PRICE_CONTINUATION` | Sign of the frozen deterministic confirmation only when all confirmation thresholds pass. | no |
| `PRICE_REVERSAL` | Inverts a valid `PRICE_CONTINUATION` direction; retains its abstentions. | no |
| `DETERMINISTIC_PARSER` | Earnings: equal trinary votes for EPS surprise, revenue surprise, and guidance (`sum >= 2` UP, `<= -2` DOWN). Macro: frozen cohort component mapping at +/-0.5 z with at least two votes and absolute sum at least two; hot/hawkish maps DOWN, cool/dovish maps UP. | no |
| `BOUNDED_LLM` | The strict reasoner output plus deterministic validation and confirmation. | no |
| `NO_TEXT_ABLATION` | The same bounded route with only structured numeric features and data-health facts. | yes |
| `OPPOSITE_LLM_PLACEBO` | Inverts only a final directional `BOUNDED_LLM` output; retains every abstention. | yes |
| `SEEDED_RANDOM_PLACEBO_256` | 256 controls from `SHA-256(policy_sha256, event_id, counter_000_to_255)`, matched to bounded-LLM direction coverage and abstentions. | yes |

Every signal carries `execution_authority: false`. Placebo and ablation arms are evaluation-only and can never enter the production permit path; the bounded-LLM and ablation arms use dedicated engine instances so evaluation never consumes the production call fence. Gate C consumes all arms through the same canonical bytes with no special cases and no outcome leakage.

## Stable reason codes

Engine-local codes: `PREFLIGHT_INELIGIBLE`, `PREFLIGHT_DATA_HEALTH`, `START_BEFORE_FEATURE_RECEIPT`, `START_AFTER_DECISION_CUTOFF`, `DUPLICATE_REASONER_CALL`. Validator codes reused from the #26 contract include `EVENT_INELIGIBLE`, `DATA_HEALTH_INVALID`, `EXCHANGE_IDENTITY_MISMATCH`, `REASONER_POLICY_MISMATCH`, `REASONER_MODEL_CONFIG_MISMATCH`, `CLOCK_MISMATCH`, `LATE_RESPONSE`, `REASONER_TIMEOUT`, `REASONER_CANCELED`, `REASONER_PROVIDER_ERROR`, `REASONER_SCHEMA_INVALID`, `REASONER_RAW_HASH_MISMATCH`, `UNSUPPORTED_CITATION`, `UNSUPPORTED_UNKNOWN_CODE`, `MATERIAL_UNKNOWN`, `MISSING_PRIMARY_CITATION`, `MISSING_MARKET_CITATION`, `MISSING_FALSIFIER`, `REASONER_UNCERTAIN`, `CONFIRMATION_OPPOSED`, and `CONFIRMATION_NEUTRAL`. A deterministic veto may only turn `UP`/`DOWN` into `UNCERTAIN`; it can never manufacture direction or turn abstention into activity.

## Determinism

Identical snapshot, policy, and reasoner result produce identical decision bytes and identical baseline bundle bytes. All timestamps are supplied artifacts; the engine, baselines, and smoke harness never read a wall clock. BMO, AMC, and macro cohorts keep mechanically distinct clocks through the snapshot cohort and the frozen policy clock registry; baselines never pool cohorts.

## Route smoke

`run_route_smoke` invokes the injected route once and records status, schema outcome, and latency derived from the exchange's supplied timestamps. It imports no broker capability and cannot mutate anything; it only observes the receipt.

## Rollback

Revert the merged PR. The change adds isolated modules, tests, and documentation; there is no schema migration, credential change, runtime change, broker state, or external effect.
