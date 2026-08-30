# Evidence-bundle diff reports

The evidence-bundle diff library compares two frozen Esscher artifacts or two local bundle
directories without rerunning research, contacting a network service, or touching a broker. It
produces canonical JSON that says which source-grounded values changed. The report is comparison
tooling, not research evidence, alpha evidence, fill evidence, or an execution instruction.

## Supported inputs

Each input is either one `.json` file or a directory containing one or more `.json` files. Bundle
directories are searched recursively in stable relative-path order. Non-JSON files are ignored;
empty bundles, symbolic links, and parent-traversal paths fail closed.

The initial contract recognizes these repository-owned artifact families:

- `ringdown.point_in_time_evidence_manifest` versions 1 and 2;
- `ringdown.frozen_earnings_event_list` version 1;
- `ringdown.earnings_replay_selection_rule` version 1;
- the schema-version 1 `OFFLINE_RESEARCH` Q-FAST report emitted by `ringdown evaluate`.

Malformed JSON, duplicate object fields, non-finite numbers, non-object roots, unknown schemas,
unsupported schema versions, and unexpected top-level shapes are rejected. Q-FAST reports receive
strict field, type, identity, claim, latency, metric, and status-consistency validation. When a
directory contains exactly one selection rule, one event list, and its version 2 manifests, the
library validates their exact bytes together through the existing replay-evidence contract before
comparison.

A standalone evidence manifest, event list, or selection rule cannot prove its cross-artifact hash
and provenance relationships. It is therefore labeled `STRICT_JSON_SCHEMA_RECOGNIZED`, not
contract-validated. A complete valid replay set and a valid Q-FAST report are labeled
`CONTRACT_VALIDATED`; mixed directories are labeled `PARTIALLY_CONTRACT_VALIDATED`. Read this
status before interpreting a delta. Recognized standalone evidence may contain fields added by a
future reviewed contract; those fields are compared as content rather than silently discarded.

## Library API

Compare two files or directories without writing anything:

```python
from pathlib import Path

from ringdown_market.audit import compare_bundle_paths

report = compare_bundle_paths(
    Path("frozen/before"),
    Path("frozen/after"),
)
print(report.to_json_bytes().decode("utf-8"), end="")
```

Compare immutable artifact bytes when the caller already owns loading:

```python
from ringdown_market.audit import compare_bundle_bytes

report = compare_bundle_bytes(before_bytes, after_bytes)
```

Writing is separate and occurs only when the caller supplies an exact output file whose parent
already exists:

```python
from ringdown_market.audit import write_diff_report

write_diff_report(report, Path("build/evidence-bundle-diff.json"))
```

The library creates no directory, temporary file, cache, network connection, subprocess, MCP
session, or broker call. It does not mutate either input bundle. The output path may not use `..`
or name a symbolic link.

## Report contract

`BundleDiffReport.to_json_bytes()` emits sorted compact UTF-8 JSON followed by one newline. Equal
inputs produce equal bytes. Directory enumeration does not affect the result.

The report carries:

- raw-byte and canonical semantic hashes, validation status, and artifact counts for the before and
  after bundles;
- artifact additions, removals, and identity-preserving renames;
- stable JSON Pointer locations for field-level deltas;
- explicit before/after presence flags, so a missing field is distinct from JSON `null`;
- one category for each delta: `SCHEMA`, `DATA_CLASSIFICATION`, `IDENTITY`, `EVENT`,
  `PROVENANCE`, `LATENCY`, `VERDICT`, `CLAIM`, `ARTIFACT`, or fallback `CONTENT`.

Raw hashes preserve the exact-byte lineage used by Esscher's downstream contracts. A whitespace,
key-order, or set-like-list-order change therefore produces a `RAW_BYTES_SHA256` identity delta,
even if parsed values are semantically equal and their canonical hashes match. Lists whose contract
is set-like—such as claims, limitations, qualifiers, event IDs, rejection reasons, and source
references—are sorted for semantic comparison, avoiding an additional false content delta. Events,
evidence records, and feature dependencies are matched by their registered IDs, so additions and
removals do not degrade into positional list noise.

The fixed report labels are `COMPARISON_ONLY`, `NOT_ALPHA_EVIDENCE`, and
`NO_BROKER_EXECUTION`. A changed Q-FAST status is reported as a changed source value; the library
does not interpret that change as alpha, profitability, market quality, or execution evidence.

## Verification

Run the focused contract checks:

```bash
uv run pytest tests/test_bundle_diff.py
uv run ruff check src/ringdown_market/audit tests/test_bundle_diff.py
uv run ruff format --check src/ringdown_market/audit tests/test_bundle_diff.py
```

Before review, run the repository gates:

```bash
uv run python scripts/check_repo_hygiene.py
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

Observed on 30 August 2026:

- focused bundle-diff contract: 24 tests passed;
- repository hygiene: passed with 81 visible files checked;
- Ruff: all checks passed and all 53 files formatted;
- full test suite: 272 tests passed;
- source distribution and wheel: built successfully;
- installed-wheel audit API smoke: passed.

Passing these checks establishes deterministic comparison behavior only.
