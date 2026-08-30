# Q-FAST confirmation panel status (issue #3)

This note records the preregistered state of the untouched confirmation panel required by issue #3 and the exact stop conditions that currently apply. It is evidence-lane documentation: it creates no panel rows, tunes nothing, and admits no synthetic event as historical data.

## Frozen artifacts

- Selection rule: [`data/confirmation-panel/selection-rule-v1.json`](../../data/confirmation-panel/selection-rule-v1.json) — frozen inclusion/exclusion criteria, latency profiles, baseline rule, abstention rule, and claim boundary. SHA-256 `aa9e36be2929e9b43074c992533d5d4d02ddce7a6338fe504ffd015509a8484f`.
- Panel manifest: [`data/confirmation-panel/event-list-v1.json`](../../data/confirmation-panel/event-list-v1.json) — bound to the selection rule hash and to the frozen strategy policy hash `fb3eb4dc0e8898a6cea1ad159611623c8cad16143a6dc71ad4179c610a72ac10`.
- Panel contract: `src/ringdown_market/data/panel.py` — strict parser and deterministic zero-latency/p95 Q-FAST report builder.

## Panel rules (preregistered)

- The four P0 contract-development events (`KR-2026Q2-EARNINGS`, `GIS-2027Q1-EARNINGS`, `MU-2026Q4-EARNINGS`, `NKE-2027Q1-EARNINGS`) are excluded by identity and can never be admitted.
- Eligible universe: scheduled BMO/AMC earnings of US-listed optionable common equities priced at least `$10.00`, with an exact publication instant, synchronized market windows, and permitted redistribution.
- The eligible universe and every exclusion reason are frozen before candidate returns are inspected. No event is added or removed because its realized result helps the candidate.
- Abstentions remain in the eligible-event denominator with zero signed return.
- All frozen baselines run at equal risk under the common unit residual-return convention.
- Zero-latency and p95-latency results are reported separately; the p95 profile gates execution (`SHADOW_ONLY` on Q-LATENCY failure).
- Panel floor: 20 eligible untouched events; ceiling: 30.

## Current status: COLLECTION_INCOMPLETE

The panel manifest is frozen with **zero admitted events**. No historical event has yet passed the admission gate with permitted point-in-time provenance. The following issue #3 stop conditions are recorded verbatim as the reasons:

- `ELIGIBLE_EVENTS_BELOW_MINIMUM` — the eligible panel is below the 20-event floor.
- `PUBLICATION_TIMESTAMPS_NOT_ESTABLISHED` — no candidate event yet carries a source-supported exact publication instant with typed precision.
- `REDISTRIBUTION_RIGHTS_UNCLEAR` — market-data entitlement for historical windows has not been confirmed for redistribution.
- `MARKET_WINDOWS_NOT_SYNCHRONIZED` — synchronized issuer/market/sector windows have not been collected for any candidate event.

Per the preregistered rules, the panel stays empty rather than admitting synthetic, estimated, or post-hoc assembled events. Q-FAST therefore reports `INSUFFICIENT_DATA` on both latency profiles, and the latency gate reports `INSUFFICIENT_DATA`. Under issue #33's evidence gates this is honestly reported as **not met**; it keeps issue #9's historical-confirmation precondition unmet until real collection completes.

## Deterministic verification

```text
command: uv run pytest tests/test_confirmation_panel.py -q
result: panel contract tests pass (manifest honesty, rule/policy binding, development-event
        exclusion, size ceiling, outcome-leakage rejection, report determinism, abstention
        denominator semantics)
```

The report builder is deterministic: identical panel manifest bytes and identical panel rows produce byte-identical report bytes and identical SHA-256.

## Closure path

Before the confirmation panel can admit events, each stop condition must be closed with recorded evidence: exact publication timestamps with typed precision per the [point-in-time evidence gate](point-in-time-evidence-gate.md), confirmed redistribution status per source, and synchronized adjusted-bar windows under the frozen adjusted-bar policy. Admitted events then flow through the same strict manifest contract; the four development events remain excluded regardless of outcomes.
