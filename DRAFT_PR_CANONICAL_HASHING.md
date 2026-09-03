# Draft PR: Fix canonical hashing for set-like artifact fields

## Summary

Implement order-invariant canonical hashing for set-like artifact fields in the bundle diff audit layer. This ensures that semantically identical collections of claims, evidence references, and metadata produce stable canonical SHA-256 digests regardless of iteration order.

## Branch
`feat/66-option-event-reconciliation` → `feat/strategy-release`

## Type
Fix (infrastructure/audit)

## What Changed

### Core Implementation
[src/ringdown_market/audit/bundle_diff.py](src/ringdown_market/audit/bundle_diff.py):

1. **Modified `_canonicalize_set_like()` function** (lines 263–280):
   - Recursively traverses the artifact structure
   - Identifies set-like fields by key membership in `_SET_LIKE_LIST_KEYS` or exact match `"event_ids"`
   - Sorts list items by their canonical JSON representation when `set_like=True`
   - Preserves order for non-set-like lists

2. **Identified set-like keys** (lines 168–175):
   ```python
   _SET_LIKE_LIST_KEYS: Final[frozenset[str]] = frozenset({
       "claim_boundary",
       "claims",
       "data_qualifiers",
       "limitations",
       "missing_or_conflicting_evidence",
       "reject_reasons",
       "source_refs",
   })
   ```
   These fields represent unordered collections (evidence references, failure reasons, qualifiers, etc.) where semantic equality does not depend on order.

3. **Updated `_canonical_json()` function** (lines 282–298):
   - Now calls `_canonicalize_set_like()` before serialization
   - Ensures all set-like fields are sorted before JSON encoding

### Test Coverage
[tests/test_bundle_diff.py](tests/test_bundle_diff.py) (lines 172–192):

1. **`test_set_like_list_order_is_semantically_equal()`**
   - Mutation: Reverse the `limitations` field
   - Assertion: `report["semantically_equal"] is True` and categories are only `{"IDENTITY"}`
   - Validates: Order changes in set-like fields do not cause semantic inequality

2. **`test_set_like_list_order_keeps_canonical_hash_stable()`**
   - Mutation: Reverse the `limitations` field
   - Assertions:
     - `report["left"]["canonical_sha256"] == report["right"]["canonical_sha256"]` (same canonical hash)
     - `report["left"]["raw_sha256"] != report["right"]["raw_sha256"]` (different raw hash)
   - Validates: Canonical digests are stable for semantically equivalent artifacts

## Why This Fix Is Needed

### Context: Option-Event Reconciliation

Issue #66 introduces deterministic option-event lifecycle tracking and reconciliation receipts. Option events (assignment, exercise, expiry, etc.) are collected from broker data and normalized into canonical records. The collection process may retrieve events in different orders depending on:
- Pagination order from broker APIs
- Processing order in different execution paths
- Historical vs. prospective data collection timing

### The Problem

Without order-invariant hashing, the same semantic event set collected in different orders would produce different canonical SHA-256 digests:
```
{"limitations": ["INCOMPLETE_PAGE", "UNMATCHED_QUANTITY"]}  → sha256_A
{"limitations": ["UNMATCHED_QUANTITY", "INCOMPLETE_PAGE"]}  → sha256_B  ✗ Different!
```

This breaks:
1. **Idempotent replay** — Re-fetching the same events in different order would be treated as different artifacts
2. **Duplicate detection** — The same event set from different sources would not be recognized as duplicates
3. **Artifact caching and audit trails** — Comparison reports would incorrectly flag semantic equivalence as changed

### The Solution

The `_canonicalize_set_like()` function ensures set-like fields are sorted before hashing:
```
{"limitations": ["INCOMPLETE_PAGE", "UNMATCHED_QUANTITY"]}  → sha256_A
{"limitations": ["UNMATCHED_QUANTITY", "INCOMPLETE_PAGE"]}  → sha256_A  ✓ Same!
```

Now identical semantic content reliably produces identical canonical digests.

## Test Results

All 61 bundle-diff tests pass, including the two new regression tests:
```
tests/test_bundle_diff.py::test_set_like_list_order_is_semantically_equal PASSED
tests/test_bundle_diff.py::test_set_like_list_order_keeps_canonical_hash_stable PASSED
```

Full suite:
```
pytest tests/test_bundle_diff.py -q
61 passed, 2 skipped in 1.04s
```

Full repository audit:
```
pytest -q
1518 passed, 14 skipped in 116.42s
```

## Related Work

- **#66 (parent)**: Option-event reconciliation — deterministic lifecycle contracts and append-only journal storage
- **#82 (sibling)**: Autonomous host runner composition — integrates option-event state into permit lifecycle
- **#68 (follow-on)**: PAPER session authorization for external broker receipts

This fix provides the artifact audit infrastructure required to validate and compare the deterministic evidence bundles produced by the #66/#82/#68 implementation path.

## Commit

- **Hash**: `9bb77e5`
- **Message**: "Fix canonical hashing for set-like artifact fields"
- **Files**:
  - `src/ringdown_market/audit/bundle_diff.py` (+33 lines)
  - `tests/test_bundle_diff.py` (+2 tests, -1 line)

## Validation Checklist

- [x] Implementation complete and audited
- [x] All bundle-diff tests pass (61 passed, 2 skipped)
- [x] Full repo suite passes (1518 passed, 14 skipped)
- [x] Regression tests added (two new tests)
- [x] Code follows project conventions (sorted keys, final constants, type hints)
- [x] Branch pushed to remote
- [x] Commit message follows project pattern

## How to Finalize This PR

1. Log in to GitHub: `gh auth login`
2. Create the PR: `gh pr create --title "Fix canonical hashing for set-like artifact fields" --body-file DRAFT_PR_CANONICAL_HASHING.md`
   OR use the GitHub web UI to create a PR from `feat/66-option-event-reconciliation` → `feat/strategy-release`

3. Once merged, the infrastructure will be ready for:
   - Reliable artifact comparison in option-event reconciliation workflows
   - Audit and verification of deterministic option lifecycle events
   - Stable canonical hashing for idempotent replay and duplicate detection
