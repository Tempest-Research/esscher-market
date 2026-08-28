# Ringdown

**Measure the move after the first reaction.**

Ringdown is a paper-only scheduled-earnings research system. This repository starts with an offline alpha and latency evaluation harness. It does not place orders, load credentials, or call external services.

## Current slice

Given a point-in-time event fixture and frozen latency profile, the harness produces a deterministic Q-FAST report that:

- rejects evidence published after the decision cutoff;
- prices the event from an achievable delayed entry, not an imaginary zero-latency fill;
- keeps abstentions in the eligible-event panel as zero returns;
- compares the candidate signal with frozen baselines at equal risk;
- reports `INSUFFICIENT_DATA`, `REJECTED`, or `NOT_REJECTED_SMALL_SAMPLE` without claiming alpha.

Synthetic fixtures are contract tests only. They are never financial evidence.

## Boundaries

- `PAPER`
- `OFFLINE_ONLY`
- `NO_BROKER_MUTATION`
- `NO_CREDENTIALS`
- `NO_NETWORK_CALLS`
- `NO_ALPHA_CLAIM`
- `NO_EXECUTABLE_OPTIONS_CLAIM`

## Project map

- [Architecture](docs/ARCHITECTURE.md) — implemented behavior, planned boundaries, and invariants.
- [Team onboarding](docs/TEAM_ONBOARDING.md) — required research and lane ownership.
- [Source and claim policy](docs/SOURCE_AND_CLAIM_POLICY.md) — evidence metadata and permitted claims.
- [Contributing](CONTRIBUTING.md) — branch, test, review, and safety gates.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Run the labeled synthetic contract fixture:

```bash
uv run ringdown evaluate \
  --input tests/fixtures/synthetic_contract_panel.json \
  --output build/synthetic-contract-report.json
```

The output is deterministic: identical input bytes and protocol settings produce identical report bytes. The included fixture deliberately uses only synthetic prices and carries `NOT_HISTORICAL_DATA`, `NOT_ALPHA_EVIDENCE`, and `NO_BROKER_EXECUTION` labels.

## Repository workflow

Use feature branches and draft pull requests. Keep reviewer requests empty until the diff and public wording are ready. See [CONTRIBUTING.md](CONTRIBUTING.md).
