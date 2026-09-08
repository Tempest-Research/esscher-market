# Sources and focused reading guide

Draft 0.1 · Public sources retrieved 8 September 2026

## Evidence scope

These are first-party project documentation/repositories, not an exhaustive competitive survey. Sources support the described upstream interfaces and approaches, not the correctness of a future integration, a verified feature list for Alex's code, or profitable performance. Current documentation can change; an implementation decision should record the exact installed version and source commit it tests.

The earlier architecture discussion informed this pack; its proposed responsibilities are labelled as proposals. No tests or trading operations were performed to create this document package. Local PDF/document checks are not tests of either project.

## Reading map

### Qlib — research engine and strategy reuse

The overview describes data processing, training and backtesting.[1] The portfolio-strategy documentation describes custom strategies and weight-based generation of positions/order lists.[2]

Read with this question: **where should the team's research workspace wrap Qlib, and what would merely reimplement it?** Trace a baseline from data to model to prediction to portfolio strategy. The explicit proposal is a separate product using Qlib, not an immediate fork or a claim that Qlib only generates scores.

### RD-Agent — adjacent automated R&D

The repository describes automated R&D and coordinated factor/model work in its quantitative-finance scenario.[8] Read it as a close comparator for AI-assisted research, not proof that the team's idea is unique. This pack does not reproduce or adopt its advertised performance claims.

Question: **what would the team's collaboration, provenance or execution feedback add that is not already available?**

### NautilusTrader — execution and reconciliation

The architecture guide describes event-driven components and execution flow; the live guide covers lifecycle/reconciliation concerns.[3][4]

Question: **what happens when a venue accepts an order but the program loses the acknowledgement and restarts?** Follow commands, events and state ownership. Read documented guarantees and adapter limitations before deciding what to reuse. A conceptual match is not proof that every venue or failure is supported.

### QuantConnect LEAN — reality models

The reality-modelling documentation covers fills, slippage, brokerage and other assumptions. It explicitly notes defaults suited to highly liquid assets and the need for appropriate custom models in other conditions.[5]

Question: **which assumptions could change our strategy's cost-aware outcome, and how are they replaced or tested?** Do not confuse availability of a model interface with a calibrated model for the group's chosen market.

### HftBacktest — specialist microstructure reference

The fill documentation describes queue/fill assumptions and replay limitations; the latency guide distinguishes feed, order-entry and order-response latency.[6][7]

Question: **how much does a hypothetical order's fill depend on queue and latency assumptions, and what cannot be inferred from replay?** It cannot make the historical market react to a simulated order. This is a later specialist reference if the group's horizon and licensed data warrant it, not a requirement for a daily portfolio integration.

## Independent-research reminders

- Check primary documentation and source, not only an agent summary.
- Inspect current licences and data-provider rights before copying or distributing code/data.
- Preserve source dates and commit references when advancing from discussion to implementation.
- Distinguish documented support from tests actually run in a specific configuration.
- Benchmark claims remain the authors' claims unless independently reproduced.
- A source recommending live experimentation is not authorization to trade; this pack stays simulator-first and excludes live money.

The numbered Sources block below is generated from the same retrieval ledger as the group brief.

## Sources

Public sources retrieved 8 September 2026. These support the upstream capabilities described, not a verified implementation of either proposed project. Documentation can change; pin versions and source commits when making implementation decisions.

[1] https://github.com/microsoft/qlib — Qlib overview

[2] https://qlib.readthedocs.io/en/latest/component/strategy.html — Qlib portfolio strategy

[3] https://nautilustrader.io/docs/latest/concepts/architecture — NautilusTrader architecture

[4] https://nautilustrader.io/docs/latest/concepts/live — NautilusTrader live lifecycle

[5] https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts — LEAN reality modelling

[6] https://hftbacktest.readthedocs.io/en/latest/order_fill.html — HftBacktest order fills

[7] https://hftbacktest.readthedocs.io/en/latest/latency_models.html — HftBacktest latency

[8] https://github.com/microsoft/RD-Agent — RD-Agent overview
