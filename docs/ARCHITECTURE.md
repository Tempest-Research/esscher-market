# Architecture

This document separates implemented behavior from planned behavior. A roadmap is not a runtime receipt.

## Implemented on `main`

Ringdown is an offline Python 3.12 research harness:

```text
labeled event fixture
        |
        v
point-in-time contracts
        |
        +--> frozen baselines
        |
        v
latency-adjusted residual evaluation
        |
        v
Q-FAST reject-only gate
        |
        v
deterministic JSON report + hashes
```

Current modules:

- `alpha/models.py`: immutable decision and market-path contracts;
- `alpha/evaluation.py`: eligible-path-observation and fill-relative evaluation;
- `alpha/baselines.py`: deterministic frozen comparators, including abstention;
- `alpha/qfast.py`: small-sample rejection and latency gates;
- `cli.py`: labeled input parsing and deterministic report generation.

The current repository does not connect to Alpaca, place paper orders, load credentials, produce executable option prices, or establish alpha.

## Planned vertical path

The intended system path is:

```text
scheduled event manifest
        |
        v
timestamped issuer/SEC evidence
        |
        v
frozen decision snapshot
        |
        v
residual signal or abstention
        |
        v
risk and execution permit
        |
        v
one official Alpaca MCP adapter
        |
        v
paper order readback + reconciliation
        |
        v
sanitized static public trace
```

Each arrow is a contract boundary. Missing or ambiguous information fails closed to `NO_TRADE`, reconciliation, or `SHADOW_ONLY`; it does not trigger a second adapter.

## Core evaluation

For event `i`, Ringdown evaluates:

```text
g_i = signal_i * residual_return_i
```

The residual return removes frozen market and sector components measured over the same eligible path-observation window. `UNCERTAIN` maps to zero but remains in the panel. This prevents the system from deleting difficult events after seeing their outcomes.

## Non-negotiable invariants

1. No evidence or feature timestamp may exceed the decision cutoff.
2. Entry starts at the first eligible path observation at or after the modeled latency; the current synthetic path is not proof of an executable fill.
3. Exit is measured relative to the achieved entry, not an idealized cutoff.
4. Candidate and baselines use the same panel, timing, and risk assumptions.
5. Synthetic fixtures cannot be relabeled as historical evidence.
6. Broker mutation is paper-only and passes through one frozen official adapter.
7. An ambiguous submission is reconciled by deterministic client ID, never blindly retried.
8. Public artifacts are static, sanitized, and incapable of mutation.

Historical panel admission additionally requires per-feature source dependencies, typed publication timestamps, entitlement metadata, and a common outcome path. The current aggregate `DecisionSnapshot` timestamp checks do not establish those feature-level dependencies by themselves.

## Path ownership

- `src/ringdown_market/alpha/`, evidence manifests, and replay fixtures: evidence lane.
- `src/ringdown_market/execution/`: runtime/integration lane.
- `web/` and public presentation assets: proof/submission lane.
- `.github/`, packaging, shared contracts, and final integration: Ben.

See [CODEOWNERS](../.github/CODEOWNERS) for path ownership and review routing. Branch protection requires pull requests, strict CI, current branches, resolved conversations, and linear history, but does not require a blanket approval count. Independent review remains mandatory when an issue, ownership boundary, or risk level explicitly requires it.
