# Evidence bundle diff

`esscher.audit.bundle_diff` is deterministic, offline comparison
tooling for already-produced Esscher JSON artifacts. It explains changes
between two artifacts or two directory bundles without rerunning research,
collecting sources, opening a broker session, or changing local state except
when the caller explicitly requests a report output file.

This is comparison tooling, not research evidence. A diff never establishes
alpha, profitability, executable fills, option pricing, or broker activity. It
only reports values that are present in the compared inputs, and every report
carries the fixed labels `COMPARISON_ONLY`, `NOT_ALPHA_EVIDENCE`, and
`NO_BROKER_EXECUTION` with data class `OFFLINE_ARTIFACT_COMPARISON`.

## Supported inputs

The loader uses a closed registry of the artifact schemas present on the
current main branch:

- frozen event lists, selection rules, and point-in-time evidence manifests;
- frozen research decisions, feature snapshots, and evaluation reports;
- execution-policy, protocol, permit, approval, receipt, and scheduled-run
  records;
- synthetic panel and contract-fixture wrappers used by the repository tests;
- prior evidence-bundle diff reports.

Unknown schemas, unsupported versions, malformed JSON, duplicate keys,
non-finite numbers, non-object roots, and unexpected shapes fail closed with a
stable `BundleDiffErrorReason`. Directory bundles must contain only regular
`.json` files; enumeration is sorted, symlinks and junctions are rejected, and
resolved members must remain inside the requested bundle root.

## Validation status

Each side of the report discloses how far its artifacts were validated:

- `CONTRACT_VALIDATED`: the artifact passed a full contract check. Q-FAST
  evaluation reports pass strict field, type, identity, claim, latency,
  metric, and status-consistency validation. Directory bundles containing
  exactly one selection rule, one event list, and version 2 evidence manifests
  are validated through the existing replay-evidence contract before
  comparison; a contract rejection fails closed with
  `CONTRACT_VALIDATION_FAILED`.
- `PARTIALLY_CONTRACT_VALIDATED`: the bundle mixes contract-validated and
  schema-recognized artifacts.
- `STRICT_JSON_SCHEMA_RECOGNIZED`: the artifact is recognized and strict-JSON
  valid, but a standalone artifact cannot prove cross-artifact hash and
  provenance relationships. Read this status before interpreting a delta.

## Report contract

The report has schema `ringdown.evidence_bundle_diff_report` version `1`. Each
side carries `kind`, `validation_status`, raw and canonical SHA-256 digests,
aggregate event IDs, and per-artifact metadata. Every delta has a category, a
JSON-Pointer-like path, a change kind, and explicit left/right presence flags,
so a missing field is never confused with JSON `null`.

Raw hashes preserve exact-byte lineage: a whitespace or key-order change
produces an `IDENTITY` delta for `@raw_bytes_sha256` even when parsed values
are semantically equal and canonical hashes match. `identical` means zero
deltas of any kind; `semantically_equal` allows identity and rename deltas.
Lists whose contract is set-like (claims, limitations, qualifiers, source
references, rejection reasons, event IDs) are compared as sets. Events,
evidence records, and feature dependencies are matched by their registered
IDs, and bundle members with unchanged identity but changed file names are
reported as `ARTIFACT` renames instead of positional noise.

Delta categories: `SCHEMA`, `CLASSIFICATION`, `HASH`, `EVENT_ID`, `INCLUSION`,
`TIMING`, `PROVENANCE`, `LATENCY`, `VERDICT`, `CLAIM`, `LIMITATION`,
`IDENTITY`, `ARTIFACT`, `FILE`, and fallback `FIELD`.

## Python API

Compare immutable artifact bytes:

```python
from esscher.audit.bundle_diff import compare_artifacts, canonical_report_bytes

report = compare_artifacts(left_bytes, right_bytes)
report_bytes = canonical_report_bytes(report)
```

Compare `.json` files or directory bundles by path:

```python
from esscher.audit.bundle_diff import compare_paths

report = compare_paths("left-bundle", "right-bundle")
```

Write only to an explicitly requested output file whose parent already exists:

```python
from esscher.audit.bundle_diff import write_report

write_report("left-bundle", "right-bundle", "build/evidence-bundle-diff.json")
```

## Command-line entry

The isolated module entry point does not modify the top-level `ringdown` CLI:

```bash
mkdir -p build
uv run python -m esscher.audit.bundle_diff \
  data/earnings-replays \
  data/earnings-replays \
  --output build/evidence-bundle-diff.json
```

Without `--output`, the canonical report is written to standard output. Errors
are printed with their stable reason and the command exits `2`. The library
raises `BundleDiffError` with the same reason, path, and detail.

## Verification

The Issue #22 implementation was verified locally with:

```bash
uv run ruff check src/esscher/audit tests/test_bundle_diff.py
uv run ruff format --check src/esscher/audit tests/test_bundle_diff.py
uv run pytest tests/test_bundle_diff.py -q
```

Observed result:

```text
All checks passed!
60 passed, 2 skipped in 0.50s
```

The two skips are symlink tests because symbolic-link creation is unavailable
in the verification environment. The implementation rejects symlinks and
junctions when the platform exposes them.
