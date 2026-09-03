# Reasoner route and p95 latency gate

**Status: DRAFT — the owner-approved direct Kimi K3 descriptor validates, but
V1 evaluation remains fail-closed.** This note records the exact direct provider
boundary and the separate frozen p95 execution-latency profile. It does not
claim an operational Kimi route, a provider completion, token usage, measured
latency, an account, a broker call, a PAPER order, or live execution.

## What this freezes

1. **Direct reasoner route** — `esscher.reasoner_route/v1` descriptor and
   `esscher.reasoner_route_approval/v1` receipt, packaged at
   `contracts/policies/reasoner_route_v1.json` and
   `contracts/policies/reasoner_route_approval_v1.json`.
2. **p95 latency profile** — `esscher.latency_profile/v1`, packaged at
   `contracts/policies/latency_profile_v1.json`.

The route descriptor binds these exact direct-provider semantics:

- provider ID: `moonshot_direct`;
- base URL: `https://api.moonshot.ai/v1`;
- model: `kimi-k3`, no revision override;
- one call, zero retries, hard timeout of 8 seconds;
- `reasoning_effort: "low"` and `max_completion_tokens: 512`;
- `tool_choice: "none"`, with no `tools` request field;
- `response_format.type: "json_schema"`, strict mode, and the exact six-field
  Esscher output schema;
- omitted request fields: `temperature`, `top_p`, `seed`, `max_tokens`, `n`,
  `presence_penalty`, `frequency_penalty`, and `tools`;
- provider-fixed effective decoding recorded in the route/model identity,
  rather than emitted as request fields: temperature `1.0`, top-p `0.95`.

The descriptor bytes hash is
`d77d72faadb14914c9add3f2d964af3f23f7780d2cdf5ce43a172897440b847f`.
Its direct-Kimi model-config hash is
`7d1036ab10d14b508c94dd8a4b0b05ce773373b18073510c1cc15e2414769c23`.
The exact canonical strict-output-schema hash is
`c8275548471afc1def1e0e80dde68d40fde7ad32b4605afc74e1cc7bd360f409`.
The model-config identity includes the base URL, legacy V1 caller decoding,
and the complete provider request policy above; it is not a provider/model plus
legacy decoding hash.

Neither artifact carries credentials, broker authority, account authority,
orders, or secret-bearing application arguments. The route authorizes neither a
provider purchase nor a probe budget.

## Owner selection, approval, and V1 compatibility

The owner selected the direct Moonshot/Kimi route. The packaged approval receipt
is therefore `APPROVED` by `bbeennyy860-cyber` at
`2026-09-01T13:33:32Z`. That receipt is intentionally distinct from operational
readiness.

The accepted V1 event policy freezes caller temperature `0.0` (with the legacy
route identity's top-p `1`). K3's selected direct boundary instead records
provider-fixed effective sampling of temperature `1.0` and top-p `0.95`, while
omitting both caller-controlled request fields. The validated route consequently
returns:

- `compatibility_state: INCOMPATIBLE`;
- `compatibility_reason_code: FROZEN_POLICY_DECODING_INCOMPATIBLE`;
- `evaluation_eligible: false`.

`OpenAiCompatibleReasonerRoute` refuses construction from that route. No
compatible receipt is fabricated. A later autonomous V2 policy must resolve the
sampling contract once, alongside the new strategy lanes; it must not silently
amend V1 or retroactively reuse V1 evidence.

## Pure direct-Kimi request path

`strategy.host_route.build_kimi_k3_request()` is pure and deterministic even
while route construction is blocked. It builds the exact fake-transport target:

`https://api.moonshot.ai/v1/chat/completions`

Its provider payload contains only:

- model identity;
- the immutable system message with authority, citation, output-contract, and
  prompt-injection rules;
- a user message containing canonical typed `StrategySnapshot` data (including
  evidence references), canonical `FeatureReceipt` feature records, and their
  immutable identities;
- the direct K3 request-policy fields listed above.

Any supplied news or text is defined by the system contract as quoted untrusted
data, never as instructions. Existing canonical serializers produce snapshot
and feature data; the request builder does not reproduce model fields manually.
It recomputes their canonical bytes and rejects stale superficial identities.
`request_sha256` hashes the exact canonical provider payload plus the immutable
route/model/prompt/schema and strategy identities. A later canonical feature or
evidence mutation therefore changes request identity, and a stale container hash
is rejected.

The payload has no API key, account, broker, order, or forbidden sampling/tool
field. `load_route_environment()` reads only `KIMI_API_KEY`; base URL, model,
reasoning effort, schema, and fixed sampling values come only from validated
packaged descriptor bytes. The adapter validates then discards its API-key
argument; a future host-owned transport, not the strategy package, owns
authentication.

`invoke_kimi_k3_transport()` is a pure injected-transport seam: exactly one
call, no retry. `TimeoutError` maps to existing `ExchangeStatus.TIMEOUT` /
`REASONER_TIMEOUT`; other transport failures map to
`ExchangeStatus.PROVIDER_ERROR` / `REASONER_PROVIDER_ERROR`. Provider exception
text is not returned or recorded. The package imports no network client, and
the fake-transport socket guard proves no network access in this path.

## Current external blocker

The currently stored `KIMI_API_KEY` is invalid. Before this correction,
`/v1/models` and one synthetic chat attempt returned HTTP 401
`invalid_authentication_error`. This change made no provider API request,
and there is no provider completion, token, latency, or successful-inference
receipt. Replacing the host credential alone cannot unblock V1: the frozen
policy compatibility gate remains closed.

The current official direct-K3 references are:

- <https://platform.kimi.ai/docs/guide/kimi-k3-quickstart>
- <https://platform.kimi.ai/docs/api/chat>

## p95 latency profile (unchanged)

The frozen p95 profile remains `PREREGISTERED` at 30,000 ms, nearest-rank p95,
with a host monotonic clock and UTC anchor. The existing 30,000 ms synthetic
fixture is contract test data, not a measured profile. A `HOST_MEASURED` profile
must supersede it before any promotion claim.

## Fail-closed behavior

- Missing, malformed, stale, secret-bearing, hash-mismatched, identity-drifted,
  or policy-drifted route artifacts raise `RouteContractRejected` before a
  reasoner route can be constructed.
- Mutating provider/base URL/model yields typed identity rejection; mutating
  reasoning effort, strict-schema binding, provider-fixed sampling, or omitted
  fields yields typed policy rejection. Exact descriptor bytes are separately
  bound by the approval receipt.
- An approved direct route that is V1-incompatible remains inert; an approval
  state never substitutes for compatibility.
- `SYNTHETIC` latency placeholders fail evaluation and promotion.
- No route artifact authorizes broker/account authority, a provider purchase,
  a probe, a PAPER order, deployment, or live execution.

## Deferred V2 work

V2 must make one explicit policy decision covering provider-effective decoding,
route/prompt/schema registry integration, evaluation lanes, and prospective
evidence reset. It must re-run independent review and the applicable evaluation
proof before any operational route construction, provider probe, or promotion
claim. The invalid host key remains a separate external prerequisite after that
policy work.
