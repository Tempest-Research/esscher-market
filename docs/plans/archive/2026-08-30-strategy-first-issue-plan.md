# Strategy-first GitHub issue plan snapshot

**Status:** historical/live-tracker snapshot pending reconciliation with `../CURRENT.md`
**Captured:** 2026-08-30
**Source:** `Tempest-Research/esscher-market` issues #3, #9, and #26–#33

This document preserves the implementation graph that existed before the independent clean-room firm simulations and Ben's external research were reconciled. The issues remain live on GitHub. This snapshot is not permission to implement a disputed policy constant.

## Original working strategy

- Scheduled BMO/AMC earnings events.
- Supported US-listed optionable common equities priced at least $10.
- Observe the 09:30–09:35 America/New_York opening window.
- Produce `UP`, `DOWN`, or `UNCERTAIN` residual-underlying direction.
- Convert direction to one deterministic 7–21 DTE debit vertical, quantity one.
- Hold for 60 minutes from reconciled opening fill.
- Operate permanently through Alpaca PAPER.

The architecture review later identified required corrections: separate direction from package authority, separate underlying evidence from option economics, move data/baseline feasibility earlier, treat BMO/AMC as explicit strata, and prevent post-event option availability from rewriting the event denominator.

## Dependency graph

```text
PR #25 resolution
        |
        v
#26 strategy policy
        |
        +-------------------+
        |                   |
        v                   v
#27 data collector     #28 decision engine
        |                   |
        +---------+---------+
                  |
                  v
            #3 historical panel
                  |
                  v
#28 -> #29 package -> #30 risk -> #31 lifecycle
                  |                 |
                  +--------+--------+
                           v
                    #32 shadow ledger
                           |
                           v
                    #9 approved PAPER proof
                           |
                           v
                    #33 integrated tracker
```

The later architecture challenge recommends starting the prospective signal ledger after the decision release freezes rather than waiting for the entire runtime, and running option-data feasibility alongside the early research gate.

## Issue map

### #26 — strategy policy

- Owner: Alex.
- Original purpose: freeze hypothesis, universe, information set, features, abstention, expression, exit, baselines, and evidence thresholds before outcomes.
- Current caution: terminology and fixed strategy constants must be reconciled with `../CURRENT.md` and incoming research.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/26>

### #27 — point-in-time strategy snapshot collector

- Owner: MS-Mesh.
- Dependency: #26.
- Purpose: collect permitted issuer/SEC evidence and synchronized stock/market/sector data into canonical snapshots without order authority.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/27>

### #28 — residual decision engine

- Owner: Alex.
- Dependency: #26, with merge compatibility against #27.
- Purpose: produce source-attributable `UP`, `DOWN`, or `UNCERTAIN` decisions through deterministic validation and one bounded reasoner route.
- Required correction: the new decision contract must end at direction/abstention and contain no package, account, price, size, or exit authority.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/28>

### #3 — historical confirmation panel

- Owner: MS-Mesh.
- Dependencies: #2, #26, #27, and #28 in the captured graph.
- Purpose: untouched point-in-time panel, complete denominator, equal-risk baselines, latency reporting, and reject-only interpretation.
- Required correction: 20–30 events can verify wiring and falsify obvious failures; they do not establish learned-model or profitability credibility.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/3>

### #29 — deterministic option package compiler

- Owner: Alex.
- Dependency: #28.
- Purpose: map direction and a frozen option chain into one compliant debit vertical or `NO_PACKAGE`.
- Current caution: options as the core expression and the specific DTE/width/quote constants remain research decisions; measure package availability before freezing them.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/29>

### #30 — PAPER account risk and reservations

- Owner: Alex.
- Dependency: #29.
- Purpose: account-level limits, one source-of-truth ledger, durable reservation, permit idempotency, entry-disable, and close-only controls.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/30>

### #31 — monitored lifecycle and deterministic close

- Owner: Alex.
- Dependency: #30.
- Purpose: opening reconciliation, fill-relative hold, restart-safe close, final-flat proof, and `MANUAL_REQUIRED` on unresolved state.
- Current caution: exact holding and exit rules remain part of strategy reconciliation; lifecycle machinery must support the approved frozen policy without letting the model improvise exits.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/31>

### #32 — prospective shadow ledger

- Owner: MS-Mesh.
- Captured dependencies: #3 and #28–#31.
- Purpose: retain every event, decision, abstention, package result, risk result, hypothetical lifecycle, latency, and limitation without broker authority.
- Required correction: split prospective signal collection from full-stack shadow execution so chronological evidence collection starts as soon as the decision policy freezes.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/32>

### #9 — strategy-generated PAPER lifecycle

- Owner: Ben approval gate.
- Dependencies: evidence, package, risk, lifecycle, shadow ledger, and exact merged-head verification.
- Purpose: one explicitly approved strategy-generated Alpaca PAPER open-to-flat trace with final broker reconciliation.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/9>

### #33 — integrated delivery tracker

- Owners: Alex and MS-Mesh.
- Purpose: track the complete source-to-decision-to-PAPER causal chain.
- Current caution: update only after the current plan and independent research are reconciled; do not create a duplicate tracker.
- Link: <https://github.com/Tempest-Research/esscher-market/issues/33>

## Preserved value

This graph correctly established:

- strategy and evidence before demo work;
- deterministic abstention;
- source-grounded point-in-time collection;
- strategy/package/risk/execution separation;
- durable account and lifecycle truth;
- broker reconciliation and final-flat proof; and
- an explicit approval gate before PAPER mutation.

Its limitations are strategic rather than cosmetic: it assumed the scheduled-earnings/debit-vertical policy before completing the current competitive-strategy research and data feasibility work. Use `../CURRENT.md` as the decision boundary and this file as traceable history.
