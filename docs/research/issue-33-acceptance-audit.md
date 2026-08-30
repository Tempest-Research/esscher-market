# Issue #33 acceptance audit — strategy-driven Alpaca PAPER system

Status of the meta tracker's Definition of Done on branch `integration/issue-33-trade-passport`
(head commit recorded by git at audit time). Every claim below points at code, tests, or
artifacts in this tree. Anything not met is reported as not met.

## 1. One trace exists and is independently readable

Delivered as the Trade Passport (`esscher.trade_passport/v1`):

```text
permitted source bytes            -> SOURCE_EVIDENCE entry (evidence ids + content sha256s)
 -> point-in-time snapshot        -> SNAPSHOT entry (snapshot sha + strategy policy sha)
 -> strategy decision             -> DECISION entry (decision sha, snapshot/policy/route/reasoner bindings)
 -> option package                -> PACKAGE entry (package sha + decision sha binding)
 -> account risk/reservation      -> RISK_RESERVATION entry (risk policy sha + permit binding)
 -> PAPER permit                  -> PERMIT entry (one-use binding)
 -> broker order/fills            -> OPEN_SUBMISSION + OPEN_FILL entries (deterministic client order id)
 -> 60-minute monitor             -> HOLD entry (fill-relative anchor, no model exit)
 -> deterministic close           -> CLOSE_SUBMISSION + CLOSE_FILL entries (atomic multi-leg)
 -> final-flat reconciliation     -> FINAL_FLAT_RECONCILIATION entry (broker position truth)
 -> attributable result           -> RESULT entry (PAPER_OPERATIONAL_RESULT claims)
```

- Chain builder: `src/ringdown_market/passport/chain.py` (append-only, hash-linked, genesis anchor).
- Independent verifier: `src/ringdown_market/passport/verifier.py` (linkage, head anchor, stage
  order, causal bindings, policy identities, flatness, claim labels).
- Offline causal slice (work-order step 12): `src/ringdown_market/passport/slice.py` behind
  `ringdown passport-slice`; installed-wheel smoke produces a verified 13-entry passport,
  byte-identical across runs (passport sha256 `cb458383a23f630ef134ddded37f602976d77a2ea6baed7ba5d75ec43b202c1e`
  at audit time).
- Tests: `tests/test_passport.py` (12) — tamper detection for middle entries, final entry,
  deleted stages, reordered stages, unflat reconciliation, empty passport, unknown stages.

## 2. Policy hashes bound through the trace

| Policy | Identity | Bound at |
| --- | --- | --- |
| Strategy policy (#26) | `STRATEGY_POLICY_V1_SHA256` = `fb3eb4dc0e8898a6cea1ad159611623c8cad16143a6dc71ad4179c610a72ac10` | snapshot, decision, panel gates |
| Snapshot protocol (#27) | `STRATEGY_SNAPSHOT_PROTOCOL_SHA256` (`data/snapshot.py`) | panel `KNOWN_SNAPSHOT_PROTOCOL_SHA256` |
| Decision protocol | `RESEARCH_DECISION_PROTOCOL_SHA256` (`contracts/execution_policy.py`) | panel manifest gate |
| Paper permit policy | `PAPER_PERMIT_POLICY_SHA256` (`contracts/execution_policy.py`) | permit boundary |
| Risk policy (#30) | `RISK_POLICY_SHA256` (`risk/policy.py`) | RISK_RESERVATION entry |
| Execution protocol | `ALPACA_MCP_PROTOCOL_SHA256` (MCP `2.3.0` @ `872abbf…`) | permit + host boundary |

The verifier rejects any passport whose snapshot/decision/risk entries are not bound to the
frozen identities (`POLICY_BINDING_BROKEN`).

## 3. Exact-main CI, clean install, review, and comprehension gates

- CI surface: `scripts/check_repo_hygiene.py`, `ruff check`, `ruff format --check`, `pytest`,
  `uv build`, installed-wheel smoke (`evaluate`, `render-judge-trace`, `capture-snapshot`,
  `passport-slice`).
- At audit head on this branch: hygiene PASS (198 files), ruff clean, **500 tests passing**,
  wheel builds and the passport slice smoke verifies true with byte-identical output.
- Comprehension gate: PR description carries the five-question section per the repository
  template.

## 4. Historical confirmation and prospective shadow thresholds — HONESTLY NOT MET

- Historical panel: the untouched confirmation panel universe is frozen with 23 historical
  BMO/AMC events, EDGAR provenance, and synchronized windows (merged from the advanced #3
  branch), but the assembled confirmatory panel still requires a host-measured p95
  execution-latency profile; until then the assembler rejects real manifests with
  `LATENCY_PROFILE_NOT_MEASURED`. The upstream-contract gates are now satisfied (merged
  strategy-policy and snapshot-protocol hashes registered). The secondary confirmation-view
  manifest remains `COLLECTION_INCOMPLETE` with `INSUFFICIENT_DATA` under recorded issue-#3
  stop conditions.
- Prospective shadow: the ledger machinery is merged and tested; zero prospective events have
  been observed post-freeze, so the preregistered threshold disposition is `NOT_MET`. No
  success language is inferred anywhere (`docs/research/qfast-confirmation-panel.md`,
  `evaluation/shadow.py`).

## 5. No prewritten strategy output masquerades as generated

- The production decision path runs snapshot -> reasoner route -> validator; hand-authored
  `candidate_signal` fixtures are labelled supplied test inputs
  (`tests/contract_fixtures/frozen_research_decision_v1.json` metadata, `docs/STRATEGY_V1.md`).
- The passport verifier requires a directional DECISION entry bound to a snapshot sha and a
  reasoner output sha; a hand-authored decision cannot satisfy the causal bindings.
- Engine/strategy modules are import-isolated from execution/runtime broker machinery
  (`tests/test_strategy_engine.py::test_strategy_modules_never_import_broker_surfaces`,
  `tests/test_shadow_ledger.py::test_evaluation_modules_import_no_broker_machinery`).

## 6. No real-money mode, hidden fallback, credential leakage, duplicate mutation, or unreconciled success

- `PAPER_ONLY` / `OFFICIAL_ALPACA_MCP_ONLY` boundaries are frozen in policy and enforced by
  the host boundary; no direct REST/CLI fallback exists in-package (hygiene host scan).
- Credential rejection: capture host-config validation rejects credential-like keys
  (`tests/test_strategy_snapshot.py::test_cli_capture_snapshot_end_to_end`); hygiene forbids
  env/secret files; `.env` remains host-side and gitignored.
- Duplicate mutation: ledger UNIQUE constraints on event/package identity and one-use permit
  bindings; lifecycle submit-once deterministic client order ids; concurrent-reservation race
  test allows exactly one winner (`tests/test_risk_kernel.py`).
- Unreconciled success: `CLOSED_FLAT` requires broker position truth containing neither leg;
  otherwise `MANUAL_REQUIRED` with no fabricated receipt (`tests/test_lifecycle.py`).

## 7. Explicit current authorization before any PAPER mutation

- No PAPER mutation has been attempted from this branch; the lifecycle runtime only advances
  through the risk-approved state machine, and the sanctioned run path requires Ben's explicit
  current approval per issue #9 (`ISSUE_9_PREFLIGHT.md` in the work folder documents the gate).

## 8. Remaining work (honestly listed)

1. Host-measured p95 execution-latency profile with provenance (unblocks real panel assembly).
2. Candidate decisions for the 23-event panel through an approved reasoner route with receipts.
3. Prospective shadow records post-freeze.
4. Issue #9 sanctioned PAPER open-to-flat run (Ben-owned approval).
5. Issue #11 release audit (Ben-owned).
