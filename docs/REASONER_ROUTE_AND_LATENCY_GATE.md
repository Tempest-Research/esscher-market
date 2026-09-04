# Reasoner route and p95 latency gate

**Status: CURRENT — the owner-approved V5 direct route (qwen3.8-max-0902 via
the official Alibaba DashScope endpoint) is live-measured and gate-verified;
the packaged p95 profile is `HOST_MEASURED`.** The V1 history below is retained
verbatim as the record of the original direct-Kimi boundary and its fail-closed
incompatibility.

## Route generation history (2026-09-04, owner MS-Mesh)

| Gen | Provider / model | State | Why |
| --- | --- | --- | --- |
| V1/V2 | `moonshot_direct` / `kimi-k3` | dormant, V1 INCOMPATIBLE | K3 entitlement withdrawn; V1 frozen-decoding incompatibility below; DashScope-hosted kimi-k3 later re-measured: `top_p` wire-incompatible and >16 s |
| V3 | `minimax_direct` / `MiniMax-M3` | dormant alternate | live measurement: 22/22 TIMEOUT against the frozen 8s one-call budget |
| V4 (initial candidate) | `furry_vg_gateway` / `Kimi-K2.6-free` | rejected by measurement | 23/23 probes failed the frozen six-field validator (contradictions emitted as `evidence_id_1`/`evidence_id_2`); gateway hangs on `json_schema` |
| V4 | `furry_vg_gateway` / `deepseek-v4-flash-0731-free` | dormant alternate | 28/28 warm valid, p95 500 ms in session hours; free gateway enforces evening concurrency caps (429 "at concurrency limit") that would abstain live decisions |
| **V5 (current)** | `dashscope_qwen` / `qwen3.8-max-0902` | **current, APPROVED/COMPATIBLE/eligible** | official Alibaba DashScope metered infrastructure; adapter-level gate measurement 30/30 COMPLETED, 28/30 strict-schema-valid (2 summary-normalization drifts = typed abstentions), nearest-rank p95 5578 ms; date-pinned snapshot (the moving `qwen3.8-max` alias drifted 4/24) |

V5 artifacts: `contracts/policies/reasoner_route_v5.json` (route_sha256
`58878f4ec21308e767bcd8f53855a1c24d854cfa49e66adb799fc5dc5a9e50fb`) and
`reasoner_route_approval_v5.json` (approver MS-Mesh; model_config_sha256
`bf511e3bc2ef8a3d3a5acbbfaedc861a567a28e424e1dba3954b67a9d3d9b5f7`). Wire
truths: NO `response_format` (DashScope json_object demands a literal "json"
token the immutable frozen prompt never contains; the schema is prompt-directed
and client-validated, drift abstains), `enable_thinking=false` (empty
reasoning_content verified), temperature 0, top_p 1.0, no `tool_choice`,
1024-token cap, 8s one-call/no-retry. The engine lane is
`QwenDashScopeReasonerRoute` over the shared `DirectEnvelopeReasonerRoute`
boundary; the host credential is `QWEN_DASHSCOPE_API_KEY` (env-only, discarded
at construction). Metered-endpoint throttling would fail closed as typed
abstentions.

## Historical V1 record (direct Kimi K3)

**The owner-approved direct Kimi K3 descriptor validates, but V1 evaluation
remains fail-closed.** This note records the exact direct provider
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

## p95 latency profile (promoted 2026-09-04, re-frozen for V5)

The packaged p95 profile is `HOST_MEASURED`: nearest-rank p95 **5578 ms** over
**30** warm host observations (2 cold-start excluded) of the frozen fixture
decision prompt through the packaged V5 route adapter on 2026-09-04; 30/30
COMPLETED, 28/30 strict-schema-valid (2 summary-normalization drifts became
typed abstentions - disclosed); zero retries, no fallback routes;
content_sha256
`44ca36dd9981554b13f93c15e94ef031e8a10286d66c00b85d3dcde4f68153e4`. It
supersedes the V4 furry-gateway deepseek measurement (p95 500 ms) and the
original owner-preregistered 30,000 ms bound; the redacted measurement report
is retained host-side (`artifacts/measure/qwen_dashscope_latency_report.json`)
and the profile provenance note discloses the repeated-prompt caching caveat.
Earlier profiles remain recoverable from git history.

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
