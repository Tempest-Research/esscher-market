# Collaboration and staged delivery

Draft 0.1 · 8 September 2026 · Discussion proposal, not an assigned schedule

## People and responsibility

Ben is developing the research-platform direction; Alex has an execution project the group wants to connect. Ben expects Yaroslav will probably help both. Alex's current implementation and Yaroslav's chosen role are not established in this pack.

Proposed coordination: Ben on the research product, Alex on execution, and Yaroslav on one or more substantive contributions across the boundary. This is not a restriction on who may write research or systems code. Choose interests and availability before assigning work.

Potential Yaroslav starting points:

- A baseline/candidate research experiment, including reproducibility and data-availability checks.
- A market/instrument adapter and its calendar/corporate-action tests.
- A portable simulator/recovery test corpus used by both projects.
- The target-versus-achieved comparison and evidence-linked interface.
- An execution lifecycle component with independent review from Alex.

Choose one bounded initial deliverable rather than making him the default owner of all integration and QA.

## Dependency-ordered milestones

### M0: inspect and ratify

Inputs: canonical repositories/branches or an architecture diagram plus example input/output if code cannot be shared.

Actions: trace a real lifecycle; label capabilities implemented/partial/missing/unknown; choose a common market/horizon/account; agree what to reuse and how targets are represented.

Deliverable: a short decision note with source/file references, one maintainer for the shared contract, a small real change list and explicit non-goals. No implementation status should be inferred from this proposal.

### M1: reproducible research reference

Depends on M0's compatible profile. Freeze a dataset, baseline model/policy, decision times, starting account state and fees. Complete a portfolio backtest and export targets. Preserve chronological evaluation and a protected test set.

Acceptance: another run reproduces declared outputs within stated tolerances; data rights, assumptions and limitations are visible. A lifecycle-only synthetic fixture is separately labelled and cannot substantiate predictive performance.

### M2: simulator round-trip

Depends on M1 and agreed contracts. Research exports; execution validates, sizes, simulates and reconciles; research imports the events as a separate linked record.

Acceptance: one selected decision is traceable through intent, orders, fills and achieved positions. Unmatched assumptions are explained rather than forcing equality. No paper broker is necessary for this milestone.

### M3: interruption and recovery

Depends on M2. Add deterministic fault cases from `02-architecture-and-contracts.md`, ideally one failing test at a time. Preserve minimized regression traces and account-state invariants.

Acceptance: duplicate delivery, lost acknowledgements, restart, partial-fill/cancel races, invalid inputs and state mismatch behave as specified. Unresolved external outcomes lead to abstention, not fabricated success.

### M4: discrepancy report

Depends on M2 and enough M3 evidence to trust the event stream. Compare target/achieved holdings, costs, timings, rejects and unfilled opportunity cost. State the reference-price benchmark and distinguish observed facts from counterfactual scenarios.

Acceptance: seeded faults are visible with exact event references and a reproducible investigation. Review M0-M4 as the first complete integrated slice before expanding.

### M5: optional broker-paper follow-up

A separate decision after the first slice. Requires explicit connector/account approval, supported instruments, current inputs, venue-specific capability research and documented simulated-fill limitations.

Acceptance: an approved paper rebalance reconciles against venue reports. This is not live trading, proof of market impact modelling or evidence of durable profitability. Do not collect latency data by placing real orders under this plan.

## Evaluation discipline

### Research quality

Record hypotheses before inspecting final test outcomes; maintain chronological train/validation/test boundaries. Tune on training/validation rather than repeatedly mining the test period. When labels overlap across splits, investigate leakage and appropriate gaps/purging. Log failed and rejected experiments as well as selected ones.

Compare a simple baseline against one candidate, with the same universe, periods and cost conventions. Separate prediction metrics from portfolio returns and account for turnover, drawdown, spread/fee/slippage sensitivity and uncertainty. A negative result can be the correct conclusion.

### Execution quality

State which invariants and failures were tested, which adapters were exercised, and which cases remain unsupported. Replay tests demonstrate behavior within their defined model; they do not confer guarantees on every external broker.

### Product value

Can a teammate reproduce the run, understand why a strategy was accepted/rejected, and investigate a discrepancy without reconstructing scattered notebooks? This is separate from claiming an investment edge.

## Collaboration mechanics

- Keep each repository independently runnable where practical; use versioned contracts rather than hand-copied shared logic.
- One owner edits a shared file at a time. Consumers review contract meaning and compatibility.
- Each change carries purpose, non-goals, changed artifact, focused verification and unresolved risks.
- A second person reviews meaningful changes; the author does not label their own work independently reviewed.
- Agents assist but do not decide ownership, claim consent, publish code, connect accounts or erase inconvenient results.
- Agree licence and contribution expectations before copying substantial components or publishing; no new repository is assumed by this pack.

## First group discussion agenda

1. Ben explains one research question and a small reference run.
2. Alex traces his current pipeline and identifies what he wants to build or reuse.
3. Yaroslav identifies a research, systems or cross-project contribution he wants to explore.
4. Everyone challenges the target-weight contract and chooses one compatible profile.
5. Agree M0's evidence, the first bounded slice and the review owner; leave broader ideas as deferred questions.

## Exact resume point

The next technical action is a read-only inspection of Alex's canonical repository/branch or supplied architecture/sample messages. Do not treat this document as a completed audit or start an engine rewrite from its proposed module names.
