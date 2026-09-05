# Q-FAST panel evidence and prospective ledger

Deterministic P0 evidence slice for issue #67 over the frozen untouched Q-FAST
universe (23 events / 7 GICS sectors under `data/qfast-panel`). The slice adds
three evidence-only modules and a source-health gate; it never contacts a
provider, account, or broker, never reads a wall clock, and never lets an
outcome influence a feature, threshold, abstention, baseline, or release
choice. Every artifact produced here is synthetic or fake-only and permanently
carries `NOT_ALPHA_EVIDENCE`; a green run is engineering evidence only.

Owned surface:

- `src/esscher/alpha/prospective_ledger.py` — hash-chained append-only
  prospective signal ledger (`esscher.prospective_ledger_entry/v1`).
- `src/esscher/alpha/qfast_panel_reports.py` — panel manifest builder,
  source-health gate (`esscher.qfast_source_health_gate/v1`), and the panel
  evidence report (`esscher.qfast_panel_report/v1`).
- `src/esscher/alpha/fullstack_shadow_comparison.py` — full-stack
  shadow comparison (`esscher.qfast_fullstack_comparison/v1`) against the #66
  application-service receipts.
- `tests/test_qfast_panel_evidence.py` and this document.

Consumed, never edited: the frozen shadow runner and evidence validator
([QFAST_SHADOW_RUNNER.md](QFAST_SHADOW_RUNNER.md)), the panel/universe/window
validators, the #23 source-health checker, the #66 deadline-aware application
service contracts (`runtime/autonomous_application_service.py`,
`runtime/health_receipts.py`, `runtime/stage_budgets.py`; see
[AUTONOMOUS_APPLICATION_SERVICE.md](AUTONOMOUS_APPLICATION_SERVICE.md)), the
strategy release contract, and the frozen data layer under
[data/qfast-panel](../data/qfast-panel/README.md).

## Trace: INPUT → EVENT → OUTPUT → STATE

1. **INPUT** — the frozen universe identity is read from committed bytes: the
   panel selection rule (`selection-rule-v1.json`), the universe event list,
   the universe replay selection rule, 23 historical evidence manifests, and
   23 synchronized market-window provenance records. `resolve_panel_manifest`
   first checks whether the committed `universe-freeze-v1.json` already is a
   `ringdown.qfast_panel_manifest` document — it is not (its schema is
   `ringdown.qfast_panel_universe_freeze`), so `build_panel_manifest` compiles
   canonical manifest bytes binding the frozen rule hash, frozen event IDs in
   frozen order, the frozen strategy policy / snapshot protocol / research
   decision protocol hashes, the four preserved P0 exclusions, and the
   separated zero and p95 latency profiles. The rehearsal manifest is a
   `SYNTHETIC_CONTRACT_FIXTURE`: real evidence-manifest provenance stays in the
   source-health gate and the prospective ledger because a synthetic panel may
   never claim historical provenance.
2. **EVENT** — `run_source_health_gate` checks all 23 source manifests before
   any evaluation: v2 point-in-time manifests go through
   `audit.source_health.check_manifest`; the frozen universe ships v1
   `ringdown.historical_evidence_manifest` documents, which `check_manifest`
   fails closed on by schema, so those are validated through the frozen
   `panel.universe.validate_panel_universe` point-in-time contract
   (publication bound ≤ cutoff, EDGAR acceptance ≤ cutoff, retrieval clocks,
   content hashes, issuer URL binding). Any non-healthy manifest rejects the
   whole report with the validator codes. `run_qfast_panel` then replays the
   frozen shadow evaluation per event at zero and p95 latency over the
   identical eligible denominator: synthetic `SYNTHETIC` producer receipts and
   a synthetic rehearsal bundle (real frozen cutoffs, fake scores and fake
   price paths), baselines from ex-ante scores only, and perturbation arms
   (hold ×2, latency ÷2) for stability.
3. **OUTPUT** — one canonical `esscher.qfast_panel_report/v1` payload: 23
   per-event rows (both arms, abstentions retained with zero signed return),
   outcome-derived signal accuracy computed only inside the report, four
   separately reported PnL conventions, explicit conservative cost/slippage/
   latency/missing-fill/option-case fields, the source-health gate payload and
   hash, bindings to every input hash, and the machine-readable promotion
   recommendation. Identical inputs always produce byte-identical output.
4. **STATE** — `alpha/prospective_ledger.py` records the prospective protocol
   in an append-only hash-chained JSONL file under a caller-owned state
   directory: the freeze entry registers all 23 events (event ID, sector,
   decision cutoff, source manifest SHA-256, strategy identity SHA, injected
   registration clock) before any outcome access; signals append immutably;
   outcome inspection is a separate read-only entry kind.
   `alpha/fullstack_shadow_comparison.py` finally sha-links each panel row to
   the #66 service receipts (nine stage receipt shas, health receipt sha,
   service terminal receipt sha) and reports every divergence as an explicit
   finding. No ledger, journal, or receipt written by this slice is committed
   to the repository.

## PnL conventions (always reported separately, never merged)

| Convention | Class label | Definition | Abstentions |
| --- | --- | --- | --- |
| Signal accuracy | `SIGNAL_ACCURACY` / `OUTCOME_DERIVED_SYNTHETIC_FAKE` | Direction versus the frozen post-cutoff synthetic outcome label, computed only inside the report after decisions are frozen; never fed back to features, thresholds, or abstentions | Counted as non-matches inside the all-event denominator |
| Theoretical residual PnL | `SHADOW_THEORETICAL` | Beta-adjusted log residual return from the first achievable entry (cutoff plus arm latency) through the fill-relative hold, p95 arm primary, zero arm reported per row | Exactly zero signed return |
| Platform-convention PnL | `PLATFORM_CONVENTION` | Contract multiplier (100 units) × entry notional × signed residual, minus the explicit frozen fee (1.00 USD per trade) and slippage (5.0 bps of notional) | Zero: no trade, no cost |
| Fake-execution PnL | `FAKE_EXECUTION_SERVICE` | Fills and costs from a linked #66 application-service fake run; `NOT_AVAILABLE` (per event and in aggregate) whenever no service run is linked or a fill is missing | Zero or `NOT_AVAILABLE`; missing fills are listed explicitly |

Conservatism is explicit in the payload: frozen fee/slippage constants, the
latency treatment (entry at the first achievable synthetic point at or after
cutoff plus arm latency), the missing-fill policy (abstentions and missing
fills keep zero signed return), and the option-case policy (no assignment,
exercise, or expiry is modeled in the shadow rehearsal; a linked service run
must state its observed option-case status).

## Promotion semantics

The report emits exactly one of two recommendations:

- `REJECTED` — with machine-readable reasons (`synthetic_receipts_not_candidate_evidence`,
  `latency_profile_not_measured`, `configuration_rejected`, `qfast:*`,
  `INSUFFICIENT_SAMPLE`, `WEAK_CANDIDATE_OR_BASELINE`, `PERTURBATION_INSTABILITY`,
  `SOURCE_LINEAGE_GAP`, `SOURCE_HEALTH_GATE_REJECTED`, `RELEASE_UNPARSEABLE`,
  `RELEASE_NOT_BOUND`). Synthetic decisions plus the packaged PREREGISTERED
  profile always yield `REJECTED`; report status stays `REJECTED` for
  insufficient samples, weak baselines, instability, lineage gaps, or any
  non-healthy source manifest, and the claim is always `NOT_ALPHA_EVIDENCE` —
  never a profitability claim.
- `BIND_SINGLE_RELEASE` — only when the frozen shadow promotion gate itself is
  clean (route-bound receipts, HOST_MEASURED profile, surviving p95 gate,
  sufficient sample, stable perturbations) and exactly one content-addressed
  `StrategyRelease` sha parses from the supplied release bytes via
  `parse_strategy_release`. The bound sha appears once as
  `promotion.release_sha256`; a supplied release is additionally echoed as
  audit identity (`release_identity_sha256`) even when the recommendation is
  `REJECTED`.

## Prospective ledger semantics

- Genesis is `0` × 64; every entry self-hashes over its canonical unsigned
  payload including `prior_entry_sha256`, so tampering, reordering, and
  truncation fail `verify_chain` / `verify_ledger_bytes` offline.
- The first entry must be the universe freeze. After the freeze, adding an
  event rejects with `EVENT_ADDED_AFTER_FREEZE`, dropping one with
  `EVENT_REMOVED_AFTER_FREEZE`, and changing sector, cutoff, window, or hashes
  with `EVENT_RELABELED_AFTER_FREEZE`. An exactly identical freeze replay is
  idempotent; any other repeat is `DUPLICATE_FREEZE`.
- Signals are append-only, one per event (`DUPLICATE_SIGNAL` otherwise), only
  for frozen events (`UNKNOWN_EVENT`), only before the event's outcome window
  closes (`LATE_SIGNAL_AFTER_OUTCOME_WINDOW`), and never after an outcome
  inspection was recorded for that event (`SIGNAL_AFTER_OUTCOME_INSPECTION`).
- `inspect_outcome` is the separated read-only inspection API: it appends an
  `OUTCOME_INSPECTION` entry and never mutates signal state. All clocks are
  injected; identical registrations and signals produce byte-identical files.

## Criteria-to-test mapping (`tests/test_qfast_panel_evidence.py`)

| Issue #67 acceptance criterion | Test |
| --- | --- |
| No event added/removed/relabeled after outcome inspection | `test_no_event_added_removed_or_relabeled_after_outcome_inspection`, `test_prospective_ledger_rejects_late_and_inspected_signals`, `test_prospective_freeze_registers_all_23_frozen_events` |
| Publication/retrieval/availability clocks and source hashes point-in-time valid | `test_source_health_gate_passes_over_the_frozen_23_manifests`, `test_source_health_gate_rejects_unhealthy_and_context_free_manifests` |
| Abstentions in the all-event denominator with zero signed return | `test_abstentions_stay_in_all_event_denominator_with_zero_signed_return` |
| Zero and p95 reports share the exact frozen event set and strategy identity | `test_zero_and_p95_reports_share_frozen_event_set_and_strategy_identity` |
| Four PnL conventions reported separately | `test_four_pnl_conventions_are_reported_separately`, `test_fake_execution_pnl_links_service_fills_and_costs` |
| Costs/slippage/latency/missing-fill/option cases conservative and explicit | `test_platform_convention_pnl_is_conservative_and_explicit`, `test_fake_execution_pnl_links_service_fills_and_costs` |
| Insufficient sample / weak baselines / instability / lineage gaps reject without profit claims | `test_insufficient_sample_panel_is_rejected_without_profit_claim`, `test_weak_candidate_and_baselines_reject_without_profit_claim`, `test_perturbation_instability_rejects_the_panel_report`, `test_lineage_gaps_reject_the_panel_report`, `test_unhealthy_source_manifest_rejects_the_panel_report` |
| Promotion binds exactly one release sha or explicit rejection | `test_promotion_binds_exactly_one_release_sha_for_candidate_evidence`, `test_promotion_is_rejected_for_synthetic_decisions_and_preregistered_profile`, `test_promotion_rejects_unparseable_and_unbound_releases` |
| Determinism and no network | `test_repeat_panel_runs_are_byte_identical`, `test_deterministic_ledger_bytes`, `test_new_modules_import_no_network_or_broker_capability`, `test_full_panel_run_performs_no_network` |
| Full-stack comparison links service receipts and flags divergence | `test_fullstack_comparison_links_service_receipts`, `test_fullstack_comparison_flags_divergence_explicitly`, `test_service_event_receipts_contract_rejects_malformed_input` |
| Universe freeze document is not the panel manifest | `test_universe_freeze_document_is_not_the_panel_manifest` |
| Ledger tamper detection and restart | `test_prospective_ledger_detects_tampering_and_replays` |

## Boundary

No provider, account, broker, order, deployment, merge, tag, or real-money
path; no committed generated artifacts (ledgers, reports, and comparisons live
only in caller-owned state directories or test temporaries). Raw market bars
stay host-side (`METADATA_AND_HASH_ONLY`). Synthetic decisions can never
become candidate evidence, and nothing in this slice can arm a release: the
promotion recommendation is a report field, not an authority.
