# Reasoner route and p95 latency gate

**Status: DRAFT — contract frozen, provider approval pending.** This note is the
source-grounded documentation for issue #64: the frozen host-managed reasoner
route contract and the frozen p95 execution-latency profile. It distinguishes
verified external facts, owner choices, and unresolved questions.

## What this freezes

Two owner-controlled inputs that previously blocked real strategy evaluation:

1. **Reasoner route** — `esscher.reasoner_route/v1` descriptor plus
   `esscher.reasoner_route_approval/v1` receipt, packaged at
   `contracts/policies/reasoner_route_v1.json` and
   `contracts/policies/reasoner_route_approval_v1.json`.
2. **p95 latency profile** — `esscher.latency_profile/v1`, packaged at
   `contracts/policies/latency_profile_v1.json`.

Neither artifact carries credentials, broker authority, or secret-bearing
application arguments. Neither authorizes a broker call, PAPER order, provider
purchase, deployment, or live probe.

## Verified external facts

- The frozen strategy policy call policy is one call, zero retries, an
  8-second hard timeout, temperature 0.0, and a 512 output-token ceiling
  (`accepted_event_policy_v1.json`).
- The route/prompt/output-schema hashes are pinned by the
  `_REASONER_POLICY_HASH_REGISTRY` in `strategy/contracts.py`; the descriptor
  binds the current `strategy_policy_sha256`.
- The existing 30,000 ms synthetic fixture is contract test data only; it is
  not a measured profile.

## Owner choices

- **Proposed provider/model/adapter:** `dashscope` / `kimi-k3` /
  `OPENAI_COMPATIBLE_CHAT_V1`, base URL
  `https://dashscope.aliyuncs.com/compatible-mode/v1`, using a host-owned
  entitlement (no provider purchase). This is a proposal; it is not approved.
- **p95 profile:** `PREREGISTERED` at 30,000 ms, nearest-rank p95, host
  monotonic clock with UTC anchor. This is a conservative owner-preregistered
  bound for evaluation only; a `HOST_MEASURED` profile must supersede it before
  any promotion claim.

## Unresolved questions

- **Ben must explicitly approve** the exact provider/model/adapter and cost
  boundary before the approval receipt leaves `PENDING`. Until then
  `load_approved_reasoner_route()` reports `evaluation_eligible=False` and the
  host adapter refuses construction.
- **Each paid or live reasoner probe** requires separate current approval
  before execution; this PR makes zero live calls.
- **Source rights:** whether issuer evidence text may be transmitted to the
  chosen provider is recorded as an unresolved entitlement constraint; no
  provider call occurs here.
- **p95 measurement:** the HOST_MEASURED sample population
  (decision-cutoff-to-entry latencies under an authorized PAPER lifecycle,
  issue #68) is not yet collected.

## Fail-closed behavior

- Missing, stale, malformed, hash-mismatched, or unauthorized route or latency
  inputs raise `RouteContractRejected` / `LatencyProfileRejected` before any
  strategy evaluation.
- `SYNTHETIC` latency placeholders fail evaluation and promotion.
- A `REVOKED` or `PENDING` approval can never authorize evaluation.
- The reasoner route cannot receive broker/account authority or secret-bearing
  application arguments; the adapter rejects empty credentials and non-HTTPS
  base URLs.

## Consumption

- `panel.assembler.assemble_panel_report(..., latency_profile_bytes=...)`
  optionally binds a validated profile and cross-checks its p95 against the
  manifest's requested p95; real manifests now accept `HOST_MEASURED` or
  `PREREGISTERED` measurement kinds.
- `strategy.host_route.OpenAiCompatibleReasonerRoute` is the inert host
  adapter; it records an exchange receipt identical in shape to the
  deterministic fake and makes no network call unless a transport is injected.
