# Evidence bundle diff

`ringdown_market.audit.bundle_diff` is deterministic, offline comparison
tooling for already-produced Esscher JSON artifacts. It explains changes
between two artifacts or two directory bundles without rerunning research,
collecting sources, opening a broker session, or changing local state except
when the caller explicitly requests a report output file.

This is comparison tooling, not research evidence. A diff never establishes
alpha, profitability, executable fills, option pricing, or broker activity. It
only reports values that are present in the compared inputs.

## Supported inputs

The loader uses a closed registry of the schemas present on the current main
branch. It supports the following direct artifacts:

- frozen event lists, selection rules, and point-in-time evidence manifests;
- frozen research decisions, feature snapshots, and evaluation reports;
- execution-policy and protocol records;
- PAPER permits, approvals, receipt bundles, and scheduled-run records;
- synthetic panel and contract-fixture wrappers used by the repository tests.

Unknown schemas, unsupported versions, missing schema metadata, and ambiguous
containers fail closed with a stable `BundleDiffErrorReason`. JSON inputs must
be UTF-8 objects with unique keys, finite numbers, and bounded nesting.

Directory bundles contain only regular `.json` files. Filesystem enumeration is
sorted, symlinks and junctions are rejected, and resolved members must remain
inside the requested bundle root. Both sides must be either individual
artifacts or directory bundles; mixed input kinds are rejected.

## Python API

Compare immutable artifact bytes when the exact source representation is
already in memory:

```python
from ringdown_market.audit.bundle_diff import compare_artifacts, canonical_report_bytes

report = compare_artifacts(left_bytes, right_bytes)
report_bytes = canonical_report_bytes(report)
```

Compare `.json` files or directory bundles by path:

```python
from ringdown_market.audit.bundle_diff import compare_paths

report = compare_paths("left-bundle", "right-bundle")
```

The report has schema `ringdown.evidence_bundle_diff_report` version `1` and
contains sorted left/right artifact metadata, aggregate event IDs, an
`identical` boolean, and stable `deltas`. Every delta has a category, JSON
Pointer-like path, change kind, and explicit left/right presence flags. Missing
values are not confused with an existing JSON `null` value.

## Delta categories

The comparator emits these categories when their values change:

- `SCHEMA`: artifact schema or version;
- `CLASSIFICATION`: data or fixture class;
- `HASH`: protocol, input, policy, selection-rule, event-list, content, or other SHA-256 field;
- `EVENT_ID`: event membership, keyed event changes, or event-list order;
- `INCLUSION`: inclusion/exclusion state or reason;
- `TIMING`: publication precision, timestamps, timezones, and session windows;
- `PROVENANCE`: source URLs, publishers, source references, field status, entitlement, and redistribution metadata;
- `LATENCY`: latency profile or gate settings;
- `VERDICT`: candidate/baseline signals, verdicts, statuses, and lifecycle states;
- `CLAIM` and `LIMITATION`: claim-boundary and limitation labels;
- `FILE` and `FIELD`: bundle membership and other registered-value changes.

Lists of labels are compared as sorted sets for meaningful added/removed
labels. Event collections are keyed by `event_id`, so reordering event records
does not create positional field noise. Event IDs remain visible in the report
even when event records are stored in separate bundle members.

## Command-line entry

The isolated module entry point does not modify the top-level `ringdown` CLI:

```bash
mkdir -p build
uv run python -m ringdown_market.audit.bundle_diff \
  data/earnings-replays \
  data/earnings-replays \
  --output build/evidence-bundle-diff.json
```

The output parent must already exist. Without `--output`, the canonical report
is written to standard output:

```bash
uv run python -m ringdown_market.audit.bundle_diff \
  data/earnings-replays \
  data/earnings-replays
```

Errors are printed with their stable reason and the command exits `2`. The
library raises `BundleDiffError` with the same reason, path, and detail.

## Verification

The Issue #22 implementation was verified locally with:

```bash
uv run ruff check src/ringdown_market/audit tests/test_bundle_diff.py
uv run ruff format --check src/ringdown_market/audit tests/test_bundle_diff.py
uv run pytest tests/test_bundle_diff.py -q
```

Observed result:

```text
All checks passed!
52 passed, 2 skipped
```

The two skips are symlink tests because symbolic-link creation is unavailable
in the verification environment. The implementation rejects symlinks when the
platform exposes them.
