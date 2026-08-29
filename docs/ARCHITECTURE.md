# Architecture

This document separates implemented behavior from remaining integration work. A roadmap is not a runtime receipt.

## Implemented in the `0.2.0` draft

Ringdown has two connected, paper-only planes.

### Research plane

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

### Paper execution plane

```text
immutable opening permit
        |
        v
host PAPER identity + capability/account preflight
        |
        v
exact Alpaca MCP multi-leg request
        |
        v
submit once + read back by deterministic client ID
        |
        +--> unfilled: cancel order
        |
        +--> filled: atomic reversed multi-leg close
        |
        +--> partial or unknown: stop for manual reconciliation
        |
        v
get_all_positions broker truth
        |
        v
CANCELED_FLAT or CLOSED_FLAT receipt
```

Current modules:

- `alpha/models.py`: immutable decision and market-path contracts;
- `alpha/evaluation.py`: eligible-path-observation and fill-relative evaluation;
- `alpha/baselines.py`: deterministic frozen comparators, including abstention;
- `alpha/qfast.py`: small-sample rejection and latency gates;
- `execution/models.py`: immutable opening and closing permits for one debit vertical;
- `execution/host_mcp.py`: the host identity, startup capability/account preflight, sanitized observation, bounded runtime allowlist, and typed transport failures;
- `execution/mcp.py`: the single Alpaca MCP request, readback, cancellation, atomic-close, and event-flat reconciliation boundary;
- `cli.py`: labeled input parsing, deterministic report generation, and package-version output.

The execution boundary is pinned to Alpaca MCP `2.3.0` at commit `872abbf28dab6cdde7d341fc13ac139b8002d1d9`. The package does not load credentials or instantiate an MCP server. A host must inject one normalized session and attest its PAPER environment from host-owned MCP configuration. The factory verifies the six required tools from the official surface, reads only sanitized account eligibility, then exposes only the adapter's five runtime tools. Its prepared-session object constructs the existing `McpPaperBroker`; no alternate production broker path is introduced. A timed-out mutation is typed as ambiguous so the adapter reads back its deterministic client-order ID instead of submitting again.

The adapter and host boundary are contract-tested with injected fake sessions. The repository does not yet contain a sanitized real paper-account receipt, executable historical option prices, or evidence of alpha or profitability.

## Remaining vertical integration

The intended complete path is:

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
implemented paper execution plane
        |
        v
sanitized static public trace
```

The real point-in-time manifest, evidence qualification, signal-to-permit bridge, host-specific MCP client construction, and static proof artifact remain separate reviewed slices. Missing or ambiguous information fails closed to abstention or reconciliation; it never selects another adapter.

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
6. Execution is permanently paper-account only and uses one pinned official Alpaca MCP adapter.
7. Direct REST, CLI, and second-adapter fallbacks are prohibited.
8. An ambiguous order submission is reconciled by deterministic client ID, never blindly retried.
9. A filled spread closes as one reversed multi-leg order; partial fills never trigger sequential-leg repair.
10. A terminal flat receipt requires broker position truth to contain neither event leg.
11. Public artifacts are static, sanitized, and incapable of mutation.

Historical panel admission additionally requires per-feature source dependencies, typed publication timestamps, entitlement metadata, and a common outcome path. The current aggregate `DecisionSnapshot` timestamp checks do not establish those feature-level dependencies by themselves.

## Path ownership

- `src/ringdown_market/alpha/`, evidence manifests, and replay fixtures: evidence lane.
- `src/ringdown_market/execution/`: runtime/integration lane.
- `web/` and public presentation assets: proof/submission lane.
- `.github/`, packaging, shared contracts, and final integration: Ben.

See [CODEOWNERS](../.github/CODEOWNERS) for path ownership and review routing. Branch protection requires pull requests, strict CI, current branches, resolved conversations, and linear history, but does not require a blanket approval count. Independent review remains mandatory when an issue, ownership boundary, or risk level explicitly requires it.
