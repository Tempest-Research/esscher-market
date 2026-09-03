# Esscher PAPER-Trading Readiness PRD

**Status:** Draft execution contract
**Audit cut:** 2026-09-03 16:35 BST
**Canonical public baseline:** `main` at `f8219a0e98f412c35f2f98051339aefeef0b7ea9` (`v0.3.1`)
**Most complete reviewed remote candidate:** PR #88 at `b5a132dd5232d2a9ae5611287ed92442431bcc3e`
**Current verdict:** **NO-GO for broker-connected autonomous PAPER trading**

## 1. Outcome

Produce one reviewable `v0.4.0` release candidate that can connect to the approved Alpaca **PAPER** account, run Esscher's fail-closed autonomous application service through the official MCP route, reconcile broker truth, and finish flat.

This document separates three different claims:

1. **Repository-ready:** code, tests, contracts, entrypoints, and read-only preflight are complete.
2. **PAPER-session proven:** one explicitly authorised, bounded session produced broker-confirmed receipts and ended flat.
3. **Judged P&L / alpha demonstrated:** sufficient untouched evidence or judged trading results exist. This is **not** implied by either of the first two states.

## 2. Non-goals and authority boundary

- No live-money trading. Endpoint class must remain `PAPER` and fail closed otherwise.
- No order should be submitted merely to manufacture evidence.
- No autonomous PAPER session starts without Ben's explicit approval of the exact release SHA, account, arm, window, and risk envelope.
- No credential values enter Git, logs, receipts, screenshots, or this document.
- Synthetic/fake receipts remain labelled synthetic and cannot be presented as broker-confirmed execution or P&L.
- Q-FAST synthetic panel output remains `NOT_ALPHA_EVIDENCE`; operational plumbing does not create predictive edge.
- Green CI is necessary but does not prove broker readiness, account flatness, or profitability.

### 2.1 Worker execution protocol

- Workers are authorised to reuse the existing PR branches and commits, create isolated branches, push those branches, and open **draft** PRs linked to #89, #90, and any repository-only preparation under #91.
- Existing PR work must be integrated or reconciled by commit/patch identity; it must not be reimplemented merely to produce a new PR.
- Every worker PR must state its version impact, exact base/head SHA, tests run, unresolved gates, and what its evidence does **not** prove.
- Draft PRs must have no reviewer requests. They may be assigned to the corresponding GitHub issue owner for ownership visibility, but they remain draft until Ben approves the public wording and passes the comprehension gate.
- Workers may not merge, publish a release, use credentials, call a broker mutation tool, execute a PAPER session, or claim broker/P&L evidence.
- #89 owns the single RC branch. #90 stacks on that exact branch after it exists. Repository-only #91 preparation stacks after #90; Ben's route/account/release/session approvals cannot be delegated.

## 3. Repository truth at the audit cut

### 3.1 Open pull requests

| PR | Purpose | Head | Current state | PAPER-readiness interpretation |
|---|---|---|---|---|
| [#70](https://github.com/Tempest-Research/esscher-market/pull/70) | Direct Kimi K3 reasoner route and latency gate | `8f930c0` | Draft; checks green; mergeable | Code exists, but the route remains ineligible until host-measured latency evidence and owner approval are real. |
| [#81](https://github.com/Tempest-Research/esscher-market/pull/81) | Strategy release, V2 policy/risk, autonomous runtime | `3547423` | Draft; checks green; mergeable | Main release/runtime foundation. Current local checkout is this head, not `main`. |
| [#84](https://github.com/Tempest-Research/esscher-market/pull/84) | Exact Alpaca MCP 2.3.1 provenance/execution boundaries | `63fc1d9` | Draft; checks green; mergeable | Required execution-contract correction; not contained by PR #88. |
| [#85](https://github.com/Tempest-Research/esscher-market/pull/85) | Synthetic autonomous host runner/composition | `d8e4539` | Draft; checks green; mergeable | Composition is deliberately fake-only; not a real broker host. |
| [#86](https://github.com/Tempest-Research/esscher-market/pull/86) | Application service, older option-event work, Q-FAST shadow lane | `f880e8d` | Draft; checks green; mergeable | Most runtime integration exists, but the executable host is still synthetic. |
| [#87](https://github.com/Tempest-Research/esscher-market/pull/87) | Current option-event reconciliation and capture hardening | `9bb77e5` | Non-draft; checks green; mergeable; review requested from Ben | Contains fixes not safely assumed to be present in #86/#88; must still be reconciled by patch identity and tests. |
| [#88](https://github.com/Tempest-Research/esscher-market/pull/88) | Frozen Q-FAST panel/reporting on top of #86 | `b5a132d` | Draft; checks green; mergeable | Most complete stack, but still synthetic and missing #84 plus the novel #87 delta. |

Branch ancestry is not one clean linear stack:

- `#70 -> #81 -> #84`
- `#70 -> #81 -> #85 -> #86 -> #88`
- current `#87` is a separate branch from `#81`; #86 contains older/replayed option-event commits, not the current #87 tip

Therefore, merging PRs independently without a convergence pass risks duplicate or lost fixes.

### 3.2 Verification already performed

An isolated archive of PR #88 head `b5a132d` was installed with Python 3.12 and tested without changing the canonical checkout:

```text
uv run --python 3.12 --extra dev pytest -q
1642 passed, 15 skipped in 152.43s
```

The 15 skips were Windows symlink/junction privilege cases. This proves the PR #88 snapshot's automated suite passed on this host. It does **not** prove real Alpaca or Kimi connectivity.

### 3.3 GitHub issue disposition after triage

The live tracker was normalized after this audit. Four P0 issues now form the shortest safe critical path; two P1 issues preserve later evidence/package work.

| Issue | Owner | Current role |
|---|---|---|
| [#89](https://github.com/Tempest-Research/esscher-market/issues/89) | `@akurkar07` (Alex) | **P0:** converge the seven-PR stack into one exact `v0.4.0` RC and draft PR. |
| [#90](https://github.com/Tempest-Research/esscher-market/issues/90) | `@MS-Mesh` | **P0:** implement the production `PAPER_MCP` host, read-only preflight and broker reconciliation. |
| [#91](https://github.com/Tempest-Research/esscher-market/issues/91) | `@bbeennyy860-cyber` (Ben) | **P0:** measured route approval, truthful release/security evidence, exact arm and no-mutation rehearsal. |
| [#68](https://github.com/Tempest-Research/esscher-market/issues/68) | `@bbeennyy860-cyber` (Ben) | **P0 final gate:** separately authorize and run one bounded PAPER session to broker-confirmed flatness. |
| [#71](https://github.com/Tempest-Research/esscher-market/issues/71) | `@bbeennyy860-cyber` (Ben) | Umbrella tracker for `#89 -> #90 -> #91 -> #68`. |
| [#67](https://github.com/Tempest-Research/esscher-market/issues/67) | unassigned | **P1:** later strategy/Q-FAST evidence; not a substitute for broker readiness. |
| [#69](https://github.com/Tempest-Research/esscher-market/issues/69) | unassigned | **P1:** final verified package after operational proof. |

The following issues were closed as `NOT_PLANNED` with comments linking their preserved remaining work to #89-#91: [#64](https://github.com/Tempest-Research/esscher-market/issues/64), [#65](https://github.com/Tempest-Research/esscher-market/issues/65), [#66](https://github.com/Tempest-Research/esscher-market/issues/66), [#72](https://github.com/Tempest-Research/esscher-market/issues/72), [#73](https://github.com/Tempest-Research/esscher-market/issues/73), [#74](https://github.com/Tempest-Research/esscher-market/issues/74), [#75](https://github.com/Tempest-Research/esscher-market/issues/75), [#76](https://github.com/Tempest-Research/esscher-market/issues/76), [#77](https://github.com/Tempest-Research/esscher-market/issues/77), [#78](https://github.com/Tempest-Research/esscher-market/issues/78), [#79](https://github.com/Tempest-Research/esscher-market/issues/79), [#80](https://github.com/Tempest-Research/esscher-market/issues/80), [#82](https://github.com/Tempest-Research/esscher-market/issues/82) and [#83](https://github.com/Tempest-Research/esscher-market/issues/83). They were closed as **superseded, not completed**; their acceptance requirements remain binding through the replacement issues.

## 4. Product requirements

### PR-1 — One canonical release-candidate branch

Create `release/v0.4.0-paper-rc1` from current `main` and integrate the reviewed work without silently changing authorship or dropping safety fixes.

Required sequence:

1. Include #70 and #81 foundations.
2. Include #84's exact MCP 2.3.1 execution correction.
3. Include #85/#86/#88 runtime and evidence work.
4. Compare #87 against the replayed option-event commits already in #86 using stable patch IDs and file-level tests; apply only the novel current delta.
5. Resolve every conflict explicitly; no blanket `ours`/`theirs` resolution in execution, capture, lifecycle, or hashing files.
6. Run the full candidate suite from an isolated clean checkout.

**Acceptance criteria**

- One open integration PR targets `main`; superseded stack PRs are linked but not silently discarded.
- Commit authorship remains attributable.
- `git diff --check`, formatting/lint, full pytest, and package build pass on the exact head.
- Because the bound `main` baseline has no configured type checker and does not pass an unsuppressed whole-repository type check, the RC uses this approved no-new-regressions type policy:
  - pin Pyright `1.1.411` and run it with the same environment over the exact base SHA and exact RC SHA;
  - every added or modified Python file under `src/`, `tests/`, or `scripts/` must have zero Pyright `error` diagnostics on the RC;
  - for unchanged Python files, normalized RC error diagnostics must be a subset of the base diagnostics, and the RC total error count must not exceed the base total;
  - normalize unchanged-file diagnostics by repository-relative file, rule, severity, and message so line movement alone cannot manufacture a regression or a pass;
  - do not add ignores, suppressions, generated waiver baselines, or relaxed Pyright configuration merely to pass this gate;
  - record the exact tool version, commands, base/RC identities, JSON reports, changed-file set, normalization method, counts, and comparison result in the integration evidence.
- Historical typing debt remains separately tracked and cannot be represented as corrected by this RC.
- Version impact is declared **minor** and package/changelog report `0.4.0` consistently.
- Ben reviews the diff and public PR wording before the draft is marked ready.

**Evidence**

- integration manifest containing base SHA, included PRs/commit SHAs, patch IDs, conflict decisions, candidate SHA, tree hash, and test receipts.

**Owner:** Alex (`@akurkar07`) via #89.
**Safety gate:** no broker access in this requirement.

### PR-2 — Real PAPER host composition; no fake fallback

Add a separate production composition path. Preserve `SYNTHETIC_FAKE` unchanged for deterministic tests and demos.

Required implementation seams:

- `runtime/host_composition.py`: explicit `PAPER_MCP` execution class and production plan factory; never infer it from credentials.
- `execution/host_mcp.py` and `execution/lifecycle_mcp.py`: prepare one host-owned, preflighted MCP session and keep credentials/session internals out of domain code.
- `runtime/autonomous_application_service.py`: compose the real lifecycle broker, real source adapters, release-bound authority, durable claim/recovery, and terminal reconciliation.
- `sourcedata/alpaca_option_events.py` (new or equivalent): acquire/paginate raw Alpaca order/activity/account data and map it through `runtime/option_events.py` into typed lifecycle events.
- `strategy/host_route.py`: use only the exact approved direct Kimi route; no provider or model fallback.
- CLI: separate `paper-preflight` and `paper-run` commands. `paper-run` must require explicit paths to a promoted release, arm, ledger, and output directory.

**Acceptance criteria**

- Real composition is impossible unless endpoint, account capability, schema/tool provenance, release, arm, route, latency profile, source health, and durable state all validate.
- Missing/invalid production dependencies return `NO_TRADE` or a typed terminal rejection; they never fall back to fakes.
- Mutation is claim-first and readback-first on ambiguity; retries cannot duplicate exposure.
- Restart tests prove an incomplete order is recovered from broker truth before any second mutation.
- Account/activity pagination, partial fills, cancel/replace, assignment, exercise, expiry, duplicate events, unknown events, and malformed payloads are covered with recorded redacted fixtures.
- Host wall-clock scheduling and hard-flat deadlines are tested with an injected fake clock; no busy loop.

**Evidence**

- unit/contract tests plus a deterministic no-network composition test showing both execution classes and their fail-closed separation.

**Owner:** MS Mesh (`@MS-Mesh`) via #90; independent safety review remains required.
**Safety gate:** tests use fakes/fixtures only.

### PR-3 — Read-only Alpaca/Kimi preflight

Implement one no-mutation command that proves the current host can support the exact release.

The preflight must verify and emit a canonical redacted receipt for:

- Alpaca endpoint/account is PAPER, expected account identity is bound, options level is exactly the required capability, account is unblocked, and the configured starting-balance contract is satisfied.
- Required MCP tools exist under the exact selected Alpaca MCP 2.3.1 schema identity/hash.
- Read-only account, open-order, position, and activity queries succeed and paginate correctly.
- Existing exposure is either zero or explicitly blocks the session.
- Kimi route ID/model/schema match the packaged contract.
- Host-measured route profile contains at least **20** observations, uses the preregistered nearest-rank p95 method, has zero retries/fallbacks, satisfies the 30-second runtime budget, and is explicitly owner-approved.
- Secrets are redacted and absent from serialized output.

**Acceptance criteria**

- Command performs no broker mutation and can be safely repeated.
- Any mismatch exits non-zero and produces a reason-coded rejection receipt.
- A test session that records a mutation attempt fails.
- The receipt binds code SHA, build hash, account-capability ID, MCP schema hash, route-config hash, latency-profile hash, and timestamp.

**Evidence**

- `artifacts/paper-preflight/<run-id>/preflight-receipt.json`
- 20-sample route-latency artifact and signed owner-approval artifact
- explicit `NO_BROKER_MUTATION` claim validated by tests

**Owners:** MS Mesh implements the preflight in #90; Ben owns final route/account approval in #91.
**Safety gate:** read-only external calls only; require explicit approval before using credentials.

### PR-4 — Honest release qualification and exact arm

The current runtime rejects any release not evaluated as `PROMOTED`. Do not set `evidence_qualified=true` or `security_passed=true` merely to make the arm pass.

Required work:

1. Produce content-addressed evidence and security reports for the exact candidate.
2. Decide whether the existing evidence truly meets the release contract. Q-FAST outputs labelled `NOT_ALPHA_EVIDENCE` cannot be relabelled as alpha evidence.
3. If evidence is insufficient, remain `NO-GO`. A separate, tightly bounded `PAPER_REHEARSAL` release class would be a new reviewed product decision, not a same-day bypass.
4. Mint one release and one arm bound to the exact code/build/policy/report hashes, account capability, source IDs, ledger, process ID, start/end window, and flatten/recovery authority.

**Acceptance criteria**

- `evaluate_release()` returns `PROMOTED` from truthful inputs.
- The release is append-only in the release log; supersession and stale-arm rejection tests pass.
- Arm duration is bounded and expires automatically.
- Maximum new exposure is set explicitly; recommended first proof is one event, one defined-risk vertical, quantity 1, no overlapping event exposure, and mandatory hard-flat cutoff.
- Ben approves the exact release/arm/risk envelope before any `paper-run` command.

**Evidence**

- release JSON, arm JSON, evidence/security reports, hashes, release-log entry, and approval record.

**Owner:** Ben (`@bbeennyy860-cyber`) via #91, using Alex's immutable RC from #89.
**Safety gate:** still no order submission.

### PR-5 — No-mutation rehearsal and operational runbook

Run the full production composition with mutation disabled.

**Acceptance criteria**

- Preflight passes, release/arm load, scheduler starts, candidates/evidence/decision path execute, durable state survives restart, and the system either abstains or emits a would-be permit without calling an order tool.
- Kill switch, expired arm, stale source, unapproved route, latency breach, persistence failure, ambiguous readback, and hard-flat paths are rehearsed.
- Runbook includes stop conditions, manual escalation, recovery commands, and the authoritative broker queries used to prove flatness.

**Evidence**

- no-mutation rehearsal bundle and validated runbook.

**Owner:** Ben (`@bbeennyy860-cyber`) via #91, with implementation support from #90.
**Safety gate:** no broker mutation.

### PR-6 — Explicitly authorised bounded PAPER session

This is an operational gate, not ordinary repository implementation.

**Preconditions**

- PR-1 through PR-5 complete on one immutable candidate.
- Ben has reviewed and approved the exact session manifest.
- Broker reports no unexpected orders/positions before start.

**Required receipts**

- pre/post account snapshots;
- raw and typed option-event ledger;
- candidate/evidence/exchange/decision/risk/permit chain;
- exact MCP request/readback identities;
- fill/cancel/close/expiry/assignment events as applicable;
- reconciled local/broker state;
- broker-confirmed zero relevant positions and no unexpected open orders after hard-flat;
- incident record for every ambiguity or manual intervention.

**Stop conditions**

Any schema drift, account mismatch, route mismatch, stale evidence, source-health failure, persistence failure, ambiguous mutation, missed deadline, unexpected position/order, or inability to prove flatness stops new exposure immediately and preserves flatten/recovery authority.

**Acceptance criteria**

- One bounded session completes without exceeding its immutable envelope.
- Final flatness is proved from Alpaca's authoritative PAPER state, not only local receipts.
- Results are reported as one PAPER operational proof. They are not profitability or judged-P&L evidence.

**Owner:** Ben (`@bbeennyy860-cyber`) via #68.
**Safety gate:** requires a second, exact-session authorization immediately before any broker mutation.

## 5. Recommended work order for today

1. **#89 — Alex:** converge the seven-PR stack, resolve #87, publish one draft RC PR, and run clean verification.
2. **#90 — MS Mesh:** stack the production `PAPER_MCP` host, account/activity adapter and read-only preflight onto the exact RC.
3. **#91 — Ben:** capture/approve the 20-sample Kimi route profile, review the immutable diff, produce truthful release/security evidence, mint the bounded arm and complete the no-mutation rehearsal.
4. **#68 — Ben:** after a second exact-session authorization, run one bounded PAPER session and prove broker-confirmed flatness.

#89 starts first. #90 may begin design and fixture work immediately but must target #89's exact RC branch once published. #91 may collect route measurements in parallel, but its release decision must bind the final #89+#90 SHA. #68 stays blocked until all three predecessor issues pass.

## 6. Definition of done

### Repository-ready

- [ ] One canonical `v0.4.0` RC contains all intended PR deltas with no unresolved overlap.
- [ ] Full clean CI/package gates pass on the exact RC SHA.
- [ ] Real PAPER composition exists and cannot silently use fakes.
- [ ] Account/activity ingestion and recovery are broker-shaped, paginated, typed, and tested.
- [ ] Read-only preflight passes on the intended host and emits a redacted receipt.
- [ ] Kimi route has 20+ host measurements and explicit owner approval.
- [ ] Evidence/security reports truthfully permit release promotion.
- [ ] Exact release and bounded arm validate.
- [ ] No-mutation rehearsal and restart/recovery adversaries pass.

### PAPER-session proven

- [ ] Ben explicitly authorises the immutable session manifest.
- [ ] Broker-confirmed execution/reconciliation receipts exist.
- [ ] Session ends with authoritative broker-confirmed flatness.

### Judged P&L / alpha claim

- [ ] Separate untouched or judged evidence meets its preregistered standard.
- [ ] Fees, spread, slippage, latency, fill assumptions, and baselines are included.
- [ ] No synthetic or single-session result is presented as general profitability.

## 7. Immediate decision

**Recommendation:** use PR #88 as the semantic integration starting point, add #84, reconcile only the novel #87 delta, and open one draft RC PR to `main`. Do not merge the seven current PRs independently. The first engineering target after convergence is the real PAPER host/account-activity composition plus read-only preflight; without those, Esscher remains a well-tested synthetic system rather than a broker-ready one.
