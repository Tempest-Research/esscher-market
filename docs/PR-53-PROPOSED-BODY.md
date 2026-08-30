# Proposed PR #53 body: `strategy/freeze-event-policy`

**Status:** local PR-body review artifact only. This file is a proposed replacement for the
public PR body; it has not been sent to GitHub. It describes the corrected strategy-contract
code head `9aa9566b0794ed328e070a745be4cf25c097833b`. The commit that adds this Markdown
artifact is documentation-only and does not change the policy or strategy code.

## Change

Freeze the accepted Esscher event-strategy research contract as canonical, hash-bound policy
data and direction-only typed contracts. The change adds complete-denominator candidate
manifests, point-in-time snapshots, deterministic feature receipts, bounded reasoner exchanges,
validated `UP`/`DOWN`/`UNCERTAIN` decisions, separate earnings/macro clocks, and
synthetic-only fixtures.

The corrected contract additionally binds the AMC prior eligible-session close to the retained
candidate schedule, rejects a pre-open release masquerading as AMC after a weekend, binds each
macro cohort to its own BLS release family and official publication timestamp, and rejects
present feature values or components whose cited evidence was not available by the feature's
observation time.

Deliberately out of scope: real collection, a paid/live reasoner call, outcome tuning,
expression promotion, account risk constants, broker mutation, and PAPER orders. Gate A facts
remain `UNVERIFIED`; expression, exit, and risk remain `UNSELECTED`.

## Ownership

- Lane: evidence / strategy contract
- Owned paths changed: `src/ringdown_market/strategy/`, focused strategy tests/fixtures,
  strategy documentation, packaging metadata, and this local proposed-body artifact
- Cross-lane contract approved: issue #26 and `docs/plans/CURRENT.md`; #40 remains the owner
  of changing external facts
- Version impact: `minor` (the package remains `0.2.0`; issue #11 owns the release-train bump
  to `0.3.0`)

## Source grounding

- Source: issue #26 accepted decisions; accepted synthesis in
  `docs/plans/reviews/2026-08-30-independent-quant-firm-synthesis.md`; current authority in
  `docs/plans/CURRENT.md`
- External facts: Gate A values are recorded as `UNVERIFIED`, never inferred from drafts or
  legacy demo constants
- Data class: `SYNTHETIC_CONTRACT_FIXTURE`
- Data qualifiers: `NOT_ALPHA_EVIDENCE`, `NOT_HISTORICAL_DATA`, `NO_BROKER_EXECUTION`,
  `NO_EXECUTION_AUTHORITY`
- Corrected code head: `9aa9566b0794ed328e070a745be4cf25c097833b`
- Policy identity: `ESSCHER_ACCEPTED_EVENT_POLICY_V1`, SHA-256
  `afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca`
- Reasoner bindings (`route`, `prompt-contract`, `output-schema` SHA-256):
  - `EARNINGS_RESIDUAL_CONTINUATION_V1`:
    `af801a9baf24cff5b1f093e3802834855e8b82d56491b7244bba59ba357b30e3`,
    `617897661b723c2315f3cb60fbb15b6e57dfc571098a4be4563b324cd6a0354f`,
    `08dd5302e8e03e01a7012acb59048329516e6a801f8b24827066f43430c04fa4`
  - `MACRO_SPY_CONTINUATION_CHALLENGER_V1`:
    `c2dd3668be1595f6658506f830ccad06b92b532c36732fff667f7f59ce641dd2`,
    `52f7b1c152128414363225aa441bf40e3b099ff045952891d9b2743bb3bccfec`,
    `08dd5302e8e03e01a7012acb59048329516e6a801f8b24827066f43430c04fa4`

## Verification

This proposed body replaces stale results for `9ddf77763191fcf8026ea7189e2702de927836d5`.
It does not mutate the public PR body. Exact local results for the repaired branch are recorded
below after the documentation artifact is added and checked.

Exact local results on the working tree containing this artifact, with strategy code at the
corrected repair head `9aa9566b0794ed328e070a745be4cf25c097833b`:

```text
uv run pytest -q tests/test_strategy_contracts.py tests/test_strategy_policy.py
54 passed in 0.44s

uv run python scripts/check_repo_hygiene.py
repository hygiene: PASS (98 visible files checked)

uv run ruff check .
All checks passed!

uv run ruff format --check .
68 files already formatted

uv run pytest
302 passed in 2.59s

uv build
Successfully built dist/ringdown_market-0.2.0.tar.gz
Successfully built dist/ringdown_market-0.2.0-py3-none-any.whl

Clean built-wheel smoke outside the checkout
policy_id=ESSCHER_ACCEPTED_EVENT_POLICY_V1
policy_sha256=afce93b52b96e0d8c71deeb80027a1c87a4cf3623e9417db14de00279fc23bca
candidate_count=2
ringdown 0.2.0
evaluate_mode=OFFLINE_RESEARCH
event_count=4
judge_trace=PASS
```

## Safety and claims

- [x] No credentials, account identifiers, private data, or local automation files
- [x] No unapproved broker mutation or direct-REST fallback
- [x] Ambiguous external state fails closed or enters reconciliation
- [x] `PAPER`, `INDICATIVE_DATA`, and evidence limitations remain visible where applicable
- [x] No alpha, profitability, executable-fill, or production-readiness claim exceeds the evidence
- [x] Public artifacts are static and sanitized

Evidence for the safety checks:

- Repository hygiene scans visible content for secret files/patterns and direct Alpaca hosts.
- `test_strategy_package_has_no_execution_runtime_network_or_broker_imports` rejects network,
  MCP, execution, runtime, subprocess, and broker dependencies.
- Malformed, late, policy-drifted, unsupported, or provider-failed reasoner output resolves to
  recorded `UNCERTAIN`; the strategy contract has no package/order authority.
- The repaired contract rejects a macro release from the wrong frozen family, a macro publication
  outside the schedule tolerance, an unbound official publication time, an AMC pre-open release
  against a retained close boundary, and feature evidence that arrives after the feature window.
- The synthetic bundle is asserted to carry `SYNTHETIC_CONTRACT_FIXTURE`,
  `NOT_ALPHA_EVIDENCE`, `NOT_HISTORICAL_DATA`, `NO_BROKER_EXECUTION`, and
  `NO_EXECUTION_AUTHORITY`.
- The committed file inventory contains no environment file, credential, account dump, private
  dataset, or local automation artifact.

## Comprehension

- Purpose and behavior change: turn the accepted strategy design into one immutable
  machine-readable contract that downstream collection and decision work can consume without
  inventing features, clocks, thresholds, or authority.
- One input-to-output data flow: exact candidate-manifest bytes -> point-in-time strategy
  snapshot -> deterministic feature receipt -> bounded reasoner exchange -> validator/
  confirmation veto -> direction-only `UP`, `DOWN`, or `UNCERTAIN` decision.
- Safety invariant preserved: the LLM can choose only direction/abstention and evidence-backed
  explanation; deterministic code retains universe, arithmetic, timing, expression, risk,
  permit, account, exit, and broker authority.
- Failure or edge case covered: discretionary ticker insertion, post-cutoff evidence, an AMC
  pre-open release after a weekend, wrong macro release family, unbound/late official
  publication time, late feature provenance, shifted cohort clocks, missing conditional features,
  non-finite values, hash drift, late responses, unsupported citations, and execution fields all
  fail closed.
- Trade-off chosen: freeze owner-selected research hypotheses now while deliberately leaving
  provider choice, trade expression, account budgets, and lifecycle exits unresolved until their
  evidence gates pass.

## Rollback

Revert the PR merge (including the corrected contract commits
`fa4f8991d5548535aa4c950c2119100a5375f7f8` and
`9aa9566b0794ed328e070a745be4cf25c097833b`) and stop loading
`ESSCHER_ACCEPTED_EVENT_POLICY_V1`. The change has no network, account, order, position,
broker, or external-state mutation, so rollback cannot corrupt broker state or evidence. Existing
legacy research/execution fixtures remain separate and unchanged.

## Remaining gate

Independent code-owner review, manual review, and merge approval remain pending and are not
claimed by this record. This artifact remains local until Ben approves replacing the public PR
body; no PR title, body, reviewers, or review state has been changed by this work.
