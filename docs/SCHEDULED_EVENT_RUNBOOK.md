# One-shot scheduled PAPER event runner

This runbook operates one already-approved frozen Esscher event per process, reconciles it through the sole prepared Alpaca MCP PAPER session, persists one atomic sanitized state record, and exits. It does not create events or permits, run a daemon, install a host schedule, add a broker adapter, or authorize a PAPER mutation by itself.

## Safety boundary

The command accepts only:

- one strict `ringdown.scheduled_event_manifest` v1 document;
- `run_mode: "PAPER"` and `data_class: "INDICATIVE_DATA"`;
- one `event_run_id` bound to exact opening permit, closing permit, and capability hashes;
- one host-owned `module:function` returning the reviewed `PaperDemoPlan` built over `PreparedHostMcpSession`;
- one explicit durable state directory preserved across every restart.

The application never loads credentials or creates an MCP server. The host plan remains the only bridge to the official preflighted MCP PAPER session. Invalid mode, malformed or out-of-window manifest, and mismatched identities stop before the host-plan factory is called. `--dry-run` performs no state, attempt-marker, submit, cancel, or close mutation.

## Approved manifest

Freeze exact canonical JSON with these fields and no others:

```json
{
  "schema": "ringdown.scheduled_event_manifest",
  "schema_version": 1,
  "event_run_id": "<exact opening-permit event_run_id>",
  "open_permit_id": "<exact opening permit_id>",
  "close_permit_id": "<exact closing permit_id>",
  "capability_sha256": "<current sanitized host capability hash>",
  "run_mode": "PAPER",
  "data_class": "INDICATIVE_DATA",
  "approved_at": "<timezone-aware operator approval time>",
  "not_before": "<timezone-aware due-window start>",
  "expires_at": "<timezone-aware due-window end>",
  "claims": ["PAPER_OPERATIONAL_RESULT", "INDICATIVE_DATA"]
}
```

`approved_at <= not_before < expires_at`. The due window is half-open: the command is eligible at `not_before` and expired at `expires_at`. The opening and closing permits must independently be active at invocation time. Any byte change creates a different manifest SHA-256 and cannot resume an existing state record.

Do not put account IDs, broker order IDs, credentials, private event data, raw MCP responses, or secret-like fields in this file.

## 1. Preflight

1. Confirm the implementation head and CI under review are the intended bytes.
2. Confirm the host plan maps frozen research artifacts through the existing decision-to-permit bridge and returns one `PaperDemoPlan`; it must not construct an opening permit directly.
3. Confirm the prepared host capability observation is current, `PAPER`, and has the exact `capability_sha256` copied into the manifest.
4. Confirm both permits share the manifest `event_run_id`, the expected snapshot, registered PAPER policy, and active due window.
5. Select a durable local state directory. Preserve it and its hidden `.attempts/` and `.one-shot.lock` entries across restarts.
6. Confirm no other event state is `RECONCILING` or `MANUAL_RECONCILIATION`.

The host plan may perform the already-reviewed read-only capability/account preflight. No mutation is authorized by preflight.

## 2. Dry run

```bash
uv run ringdown run-scheduled-event \
  --manifest .local/scheduled/<event_run_id>.json \
  --state-dir .local/scheduled-state \
  --host-plan operator_paper_plan:build_plan \
  --dry-run
```

A successful dry run exits `0` with a sanitized `ringdown.scheduled_run_result` whose disposition is `DRY_RUN_VALIDATED`, lifecycle is `VALIDATED`, and `broker_mutation` is `NOT_ATTEMPTED`. It must not create the state directory when none existed.

Stop if the output is rejected, the event or permit identities differ, PAPER cannot be proved, the due window is inactive, the state is malformed, or another active event exists.

## 3. Armed one-shot invocation

Run only after the separate operator approval for this exact manifest and PAPER event:

```bash
uv run ringdown run-scheduled-event \
  --manifest .local/scheduled/<event_run_id>.json \
  --state-dir .local/scheduled-state \
  --host-plan operator_paper_plan:build_plan
```

The command takes a non-blocking cross-process one-shot lock, rechecks durable state, validates the exact plan, atomically writes `RECONCILING`, and enters the existing bounded PAPER lifecycle. Durable deterministic client-order markers are written before each opening, cancel, or atomic-close attempt. On restart, an existing marker forces broker readback; it never authorizes another submission.

Successful terminal output is labeled `PAPER_OPERATIONAL_RESULT` and `INDICATIVE_DATA`, and contains only sanitized event/permit/capability identity, lifecycle, request/order hashes, final-flat observation, and PAPER P&L classification. Raw broker and account identifiers are excluded.

## 4. Restart and reconciliation

For an interrupted `RECONCILING` event, rerun the exact same manifest, state directory, and host plan. Local state is only a restart cursor. Deterministic broker order and position readback remains authoritative.

- Never delete or edit an attempt marker to make a submission possible.
- Never change manifest bytes for the same `event_run_id`.
- Never use a second adapter, direct REST/SDK/CLI order call, blind retry, per-leg repair, account-wide cancel, or account-wide close.
- A terminal `CANCELED_FLAT` or `CLOSED_FLAT` invocation is a successful `TERMINAL_NOOP`, including after the due window has expired.
- `MANUAL_RECONCILIATION` never auto-resumes and blocks every other event until a human proves broker truth and a separately reviewed recovery path updates state.
- If the manifest window or either permit expires while an event is already `RECONCILING`, an armed restart atomically persists `DUE_WINDOW_EXPIRED_DURING_RECONCILIATION` or `RESTART_PLAN_INVALID_OR_EXPIRED` and stops for manual reconciliation. Dry run reports the same stop without changing state.

Unknown status, ambiguous readback, partial fill, contradictory identity, malformed economics, non-flat position, uncertain mutation outcome, or expired restart authorization emits no guessed P&L and stops with a sanitized allowlisted failure code.

## Exit codes

- `0`: dry-run validated, newly reconciled terminal result, or terminal no-op.
- `2`: manifest, permit, due-window, PAPER boundary, or pre-mutation plan identity rejected.
- `3`: ambiguous, unknown, partial, integrity-invalid existing state, or otherwise manual broker reconciliation required; no further mutation is attempted.
- `4`: another one-shot invocation or active event overlaps this run; no mutation is attempted.
- any other nonzero exit: unexpected application or host failure; preserve all state and investigate before retrying.

## Stop conditions

Stop and escalate without another invocation when:

- PAPER mode or the official host MCP identity is not exact;
- the frozen manifest, permit, capability, or state hash does not verify;
- the manifest or either permit is before-window or expired;
- another event is active;
- broker readback is absent, unknown, ambiguous, partial, contradictory, or non-flat;
- a terminal receipt or state integrity hash fails;
- any secret, account ID, or raw broker response appears in an artifact.

A PAPER P&L value is an operational observation only. It is not alpha evidence, expected profitability, executable historical fill evidence, live readiness, or authorization to deploy or schedule this command. Host scheduling remains a separate unimplemented and unapproved operation.
