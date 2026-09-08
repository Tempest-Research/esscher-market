# Collaboration and staged delivery

Draft 0.2 · 8 September 2026 · Discussion proposal, not an assigned schedule

## Direction and current decision boundary

First make one research experiment understandable and reproducible by another person. Keep the research laboratory independently useful; connect execution only after a working research example and an inspected simulator boundary exist.

Qlib is the first learning and fit-test candidate, not a final platform selection. This plan authorizes neither a broad implementation nor an account connection. No milestone below is claimed complete. Publication is not agreement from Alex or Yaroslav, and does not supersede Esscher's [current plan](../plans/CURRENT.md).

## People and responsibility

Ben is developing the research-platform direction; Alex has an execution project the group wants to connect. Ben expects Yaroslav will probably help both. Alex's current implementation and Yaroslav's chosen role are not established in this pack.

Proposed coordination: Ben on the research product, Alex on execution, and Yaroslav on one or more substantive contributions across the boundary. This is not a restriction on who may write research or systems code. Choose interests and availability before assigning work.

Potential Yaroslav starting points:

- A baseline/candidate experiment or the non-author reproduction and challenge.
- A data-availability, market/instrument or calendar/corporate-action check.
- A portable simulator/recovery test corpus used by both projects, later.
- The target-versus-achieved comparison or an execution lifecycle component, later.

Choose one bounded deliverable rather than making him the default owner of all integration and QA. The M1U reviewer must not be the author of the experiment they are assessing.

## Two workstreams, not one blocking chain

```text
Research:     L0 guided understanding -> M1 reference -> M1U teammate usefulness
                                                       |
                                                       v
                                         reuse / bounded extension decision

Integration: M0 inspect real systems and agree a compatible profile
                  |
                  +--- joins M1 + M1U only for a separately reviewed M2

Later:        M2 simulator round-trip -> M3 recovery -> M4 fee investigation
                                                     -> M5 only by approval
```

M0 retains its original identifier for continuity; the number does not make it a prerequisite for research. L0 is the learning path for contributors who need it, not a universal qualification exam or a publication gate. A team can prepare M1 while a contributor learns the workflow.

## L0: understand one guided experiment

**Purpose:** learn the research loop before designing abstractions around it. Keep the existing programming and maths curriculum primary, with the project as a small application rather than a competing course or a full-platform commitment.

Work through one saved example, one step at a time:

1. State the question, baseline and candidate; distinguish data, features and labels.
2. Explain the chronological train/validation/test split and what information is allowed at each decision time.
3. Inspect model scores and the portfolio rule that converts rankings into holdings. Scores do not directly become orders.
4. Calculate a tiny weighted portfolio return and a fee effect by hand, then check them with a small learner-written function and tests.
5. Inspect a bounded portfolio backtest: returns, losses, turnover and cost assumptions. Separate prediction quality from portfolio results.
6. Save the example and explain what the result supports, what it does not, and what remains unclear.

Programming connections: functions, collections, indexing/tabular data and tests. Maths connections: percentage returns, compounding, weighted sums, averages, variability and uncertainty. Learn unfamiliar concepts when needed; advanced calculus, deep learning and infrastructure design are not entrance requirements.

**Evidence:** a saved worked example, checked code/output, an own-words explanation and a clear record of guided versus independent work. Running framework cells alone does not establish implementation fluency or research validity. Unknown data rights, adjustments or point-in-time integrity keep the run learning-only; successful loading/training does not resolve them.

**Next bounded exercise:** continue from the existing prediction scores into the portfolio-selection rule, using a tiny example before running and interpreting the backtest. Do not start with a dashboard or integration adapter.

## M1: reproducible research reference

**Dependencies:** a chosen research question, suitable permitted data and an understood evaluation procedure. **Does not depend on M0, Alex's repository/adapter, a common execution market, target export or a simulator.**

Use existing Qlib and appropriate recording/versioning tools to compare one simple baseline and one candidate. Prefer one changed feature, model choice or declared parameter, rather than changing several things at once. Do not treat a tutorial model as an original contribution.

**Deliverable:** one shareable experiment package with:

- Hypothesis, proposed mechanism, baseline, candidate and predeclared comparison/falsification criteria.
- Data source/version and permitted retrieval procedure, universe/calendar, availability/adjustment conventions, rights and limitations; do not redistribute data without permission.
- Code revision, exact package/environment setup, configuration and seeds where applicable.
- Feature/label definitions, chronological splits, any leakage safeguards and held-out evaluation rules.
- Portfolio rule, starting account state, rebalance schedule and fee/spread/slippage assumptions.
- Prediction and portfolio metrics, relevant sensitivity results, failed runs and continue/reject/inconclusive rationale.
- Exact setup/run instructions exercised against the real implementation, artifact identities and declared comparison tolerances.

**Acceptance:** a rerun reproduces declared outputs within tolerances fixed before comparison; differences or nondeterminism are reported rather than hidden. The baseline and candidate use comparable data, periods and costs except for the declared experimental change. Preserve rejected experiments. A learning dataset with unresolved fitness questions is not promoted into validated market evidence just because replay works.

## M1U: teammate usefulness and reuse test

**Depends on M1 only.** This is the first research-product milestone, not a prediction-profitability gate.

Before testing, select a non-author, the existing-tool workflow, the required tasks and the team's acceptable setup/repair effort. Record that standard before observing the attempt; do not invent a universal time threshold.

Using only the supplied artifacts and instructions, without author narration, the teammate must:

1. Set up a fresh environment and obtain the permitted data through the documented route.
2. Reproduce baseline and candidate outputs within the declared tolerances.
3. Identify what changed, compare prediction/portfolio metrics and inspect at least one limitation or failed/rejected result.
4. Change one declared assumption in a new identified run and explain the result; use development data or a predeclared sensitivity protocol, not repeated tuning against the final test set.
5. Write their own continue/reject/inconclusive explanation, citing assumptions and outputs rather than copying the author's conclusion.

**Evidence:** the teammate's run identities, comparison, explanation and a friction log containing completion/setup time, missing information, manual repairs, author interventions and steps that could not be completed. If oral help is required, record a failed step and improve the materials before a new attempt; do not label a coached attempt independent.

**Decision:** if existing tools meet the agreed standard, adopt/document that workflow rather than building a custom workbench for its own sake. If a meaningful problem remains, describe it from the observed attempt and propose the smallest extension with the same before/after test. That is a later scope decision, not automatic implementation approval. One teammate test is initial usefulness evidence, not broad market demand.

## M0: inspect and ratify the later integration

This work can happen separately when authorized inputs are available; it must not block L0, M1 or M1U.

**Inputs:** canonical repositories/branches, or an architecture diagram plus actual sample input/output if code cannot be shared.

Trace Alex's real lifecycle and label capabilities implemented/partial/missing/unknown. Map one real research output into one real simulator input before ratifying field names, transport or storage. Inspect clocks, instruments, price adjustments, cash, rounding, account state and authority. A small offline mapping spike may supply evidence without completing M2 or creating broker effects.

**Deliverable:** a source-linked capability/mapping note, a compatible integration market/horizon/account profile, one contract maintainer, minimum required semantics, explicit mismatches, reuse decisions and a bounded change list. If the research example is incompatible, prepare a separate compatible integration reference; do not retroactively redefine the original research experiment.

The detailed records in [architecture and contracts](02-architecture-and-contracts.md) remain proposed design requirements until this evidence exists. Safety constraints are not optional merely because the field layout is provisional.

## M2: simulator round-trip

**Depends on M0, M1, M1U and a separately reviewed integration decision.** Research exports an agreed target; execution validates, sizes, simulates and reconciles; research imports events as a separate linked record.

**Acceptance:** one selected decision is traceable through intent, orders, fills and achieved positions. Unmatched assumptions are explained rather than forcing equality. No paper broker is necessary. Use a compatible integration reference when the research-only experiment cannot be translated faithfully.

## M3: interruption and recovery

**Depends on M2.** Add deterministic fault cases from [the acceptance corpus](02-architecture-and-contracts.md#5-proposed-acceptance-corpus), ideally one failing test at a time. Preserve minimized regression traces and account-state invariants.

**Acceptance:** duplicate delivery, lost acknowledgements, restart, partial-fill/cancel races, invalid inputs and state mismatch behave as specified. Unresolved external outcomes lead to abstention, not fabricated success. Synthetic lifecycle fixtures remain explicitly separate from historical market and model evidence.

## M4: first discrepancy investigation — fees

**Depends on M2 and enough M3 evidence to trust the event stream.** Use the [controlled fee comparison](02-architecture-and-contracts.md#6-first-discrepancy-study-fees): one initial account, fixed fills and valuation path; change only the declared fee schedule.

**Acceptance:** the report links each fee to its fill/event, reconciles the cash and net-asset-value difference to the total charge difference, shows quantity/exposure effects under the common valuation, and reruns within declared tolerances. Missing, duplicated or inconsistent events prevent a reconciled conclusion. Label this fixed-fill fee sensitivity, not an estimate of fees' total effect on future decisions or live execution.

Partial-fill, rounding and latency investigations are later studies with their own controls. Review M2-M4 as the first integrated slice before broadening; M1U remains independently useful without it.

## M5: optional broker-paper follow-up

A separate decision after the integrated slice. Requires explicit connector/account approval, supported instruments, current inputs, venue-specific capability research and documented simulated-fill limitations.

**Acceptance:** an approved paper rebalance reconciles against venue reports. This is not live trading, proof of market impact modelling or evidence of durable profitability. Do not collect latency data by placing real orders under this plan. Esscher's permanently paper-only boundary is unchanged.

## Evaluation discipline

### Research quality

Record hypotheses before inspecting final test outcomes; maintain chronological train/validation/test boundaries. Tune on training/validation rather than repeatedly mining the test period. When labels overlap across splits, investigate leakage and appropriate gaps/purging. Log failed and rejected experiments as well as selected ones.

Compare a simple baseline against one candidate, with the same universe, periods and cost conventions except for the predeclared variable under test. Separate prediction metrics from portfolio returns and account for turnover, drawdown, spread/fee/slippage sensitivity and uncertainty. A negative or inconclusive result can be the correct conclusion.

### Execution quality

State which invariants and failures were tested, which adapters were exercised, and which cases remain unsupported. Replay tests demonstrate behavior within their defined model; they do not confer guarantees on every external broker.

### Product value

Use the M1U attempt and before/after evidence, not an agent's endorsement, feature count or passing integration tests. A valuable engineering/learning project need not invent a new category; claims of improved research productivity still require observation.

## Collaboration mechanics

- Keep each repository independently runnable where practical; use versioned contracts rather than hand-copied shared logic.
- One owner edits a shared file at a time. Consumers review contract meaning and compatibility.
- Each change carries purpose, non-goals, changed artifact, focused verification and unresolved risks.
- A second person reviews meaningful changes; the author does not label their own work independently reviewed.
- Agents assist but do not decide ownership, presume consent or erase inconvenient results. Publication and account connections require explicit scoped authorization.
- Agree licence and contribution expectations before copying substantial components or publishing; no new repository is assumed by this pack.

## First group discussion agenda

1. Choose the initial research question and baseline/candidate comparison; distinguish a learning run from research evidence.
2. Agree who will perform the non-author M1U attempt and what acceptable friction means.
3. Identify which existing tools to try before custom work, without treating Qlib as already selected.
4. Let Alex and Yaroslav choose their own bounded contributions. Inspect execution independently when authorized samples exist.
5. Record the next artifact and review owner, not a broad build commitment or invented deadline.

## Exact resume point

The research learning path resumes at prediction scores -> portfolio-selection rule -> small checked calculation -> interpreted backtest. Preserve what is already understood; fill gaps through bounded programming and maths practice rather than restarting setup or generating the whole platform.

The separate integration path awaits an authorized inspection of Alex's canonical repository/branch or supplied architecture/sample messages. No execution dependency blocks the research learning path. The next platform-selection decision follows M1U evidence, not this document's publication.
