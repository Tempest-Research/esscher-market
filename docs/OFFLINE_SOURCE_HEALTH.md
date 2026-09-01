# Offline source-provenance health checks

Deterministic, offline health checks for the source and provenance metadata of frozen point-in-time evidence manifests. The checker explains missing or contradictory evidence fields before a researcher attempts evaluation. It never fetches a URL, resolves a hostname, starts a broker or MCP session, or decides whether a claim is true.

A `HEALTHY` report is **engineering evidence only**: it shows that the manifest metadata is internally consistent with the frozen contract and repository policy. It does not establish that a source is reachable, that a claim is accurate, or anything about alpha, profitability, or fill quality. Claim levels are defined in the [source and claim policy](SOURCE_AND_CLAIM_POLICY.md#claim-levels).

## Boundary

- Library API only: `ringdown_market.audit.source_health`. No CLI, runtime, evaluation, or execution surface is touched.
- Zero network capability: no DNS, HTTP, browser, MCP, broker, or account access. The module imports no network, subprocess, or broker capability, and tests enforce this.
- No source scraping, correction, schema evolution, or outcome inspection.
- Reuses the strict parsing of the frozen replay-evidence contract (`ringdown_market.contracts.replay_evidence`) instead of defining a parallel schema.

## Usage

```python
from pathlib import Path
from ringdown_market.audit.source_health import (
    canonical_report_bytes,
    check_manifest,
    check_path,
)

manifest = Path("data/earnings-replays/events/KR-2026Q2-EARNINGS.json").read_bytes()
report = check_manifest(manifest)
print(report.status, len(report.findings))

# Optional frozen context enables identity and issuer-URL cross-checks.
event_list = Path("data/earnings-replays/event-list-v1.json").read_bytes()
selection_rule = Path("data/earnings-replays/selection-rule-v1.json").read_bytes()
report = check_manifest(manifest, event_list=event_list, selection_rule=selection_rule)

# File inputs are screened fail-closed for traversal, links, and non-regular files.
report = check_path(Path("KR-2026Q2-EARNINGS.json"), root=Path("data/earnings-replays/events"))

canonical = canonical_report_bytes(report)
```

Each finding carries a stable code, severity (`ERROR` or `WARNING`), an exact RFC 6901 JSON pointer into the manifest, a message, and remediation text grounded in repository policy.

## Fail-closed behavior

Malformed JSON, duplicate keys, a non-object root, and unsupported schema or schema version stop the check with a single terminal finding and status `FAILED_CLOSED`; no speculative findings are emitted after a terminal failure. Untrusted file paths (missing targets, directories, traversal components, symbolic links or junctions, absolute paths under a supplied root) also fail closed with `UNTRUSTED_PATH`. Optional context bytes must be supplied together and must themselves pass the replay-evidence contract; otherwise cross-checks are skipped with `CONTEXT_INVALID` while manifest-level checks still run.

## Determinism

Identical manifest bytes always produce byte-identical canonical findings via `canonical_report_bytes`. Findings are emitted in a fixed check order (manifest fields, then records in index order, then mappings, dependencies, filing, ordering, cardinality, and context cross-checks). The report carries no wall-clock time, randomness, or environment dependence.

## Finding codes

| Code | Severity | Meaning | Policy grounding |
| --- | --- | --- | --- |
| `PARSE_FAILED` | ERROR | Bytes are not strict UTF-8 JSON with a single root object. | [Retrieval and content hash rules](research/point-in-time-evidence-gate.md#retrieval-and-content-hash-rules) |
| `DUPLICATE_KEY` | ERROR | A JSON object repeats a key. | [Retrieval and content hash rules](research/point-in-time-evidence-gate.md#retrieval-and-content-hash-rules) |
| `UNSUPPORTED_SCHEMA` | ERROR | Schema or schema version is not the data-only evidence manifest v2. | [Historical-panel admission gate](research/point-in-time-evidence-gate.md#8-historical-panel-admission-gate) |
| `UNTRUSTED_PATH` | ERROR | File input is missing, not a regular file, or carries traversal or link components. | [Public artifacts](SOURCE_AND_CLAIM_POLICY.md#public-artifacts) |
| `FIELD_MISSING` | ERROR | A schema-required field is absent. | [Required evidence metadata](SOURCE_AND_CLAIM_POLICY.md#required-evidence-metadata) |
| `FIELD_MALFORMED` | ERROR | A schema-required field has the wrong shape. | [Required evidence metadata](SOURCE_AND_CLAIM_POLICY.md#required-evidence-metadata) |
| `UNKNOWN_FIELD` | ERROR | A field is not part of the frozen schema. | [Historical-panel admission gate](research/point-in-time-evidence-gate.md#8-historical-panel-admission-gate) |
| `URL_MISSING` | ERROR | A source URL field is absent. | [Required evidence record](research/point-in-time-evidence-gate.md#required-evidence-record) |
| `URL_MALFORMED` | ERROR | A source URL is empty, unparseable, carries control characters, or embeds credentials. | [Required evidence record](research/point-in-time-evidence-gate.md#required-evidence-record) |
| `URL_NOT_HTTPS` | ERROR | A remote source URL does not use HTTPS. | [Required evidence metadata](SOURCE_AND_CLAIM_POLICY.md#required-evidence-metadata) |
| `URL_NOT_PUBLIC` | ERROR | A source URL names a loopback, private, or otherwise non-public host. | [Public artifacts](SOURCE_AND_CLAIM_POLICY.md#public-artifacts) |
| `MUTABLE_LOCAL_REFERENCE` | ERROR | A file, data, or relative reference cannot support a public artifact. | [Retrieval and content hash rules](research/point-in-time-evidence-gate.md#retrieval-and-content-hash-rules) |
| `PUBLISHER_MISSING` | ERROR | A record has no usable publisher. | [Required evidence record](research/point-in-time-evidence-gate.md#required-evidence-record) |
| `RETRIEVAL_TIME_MISSING` | ERROR | A record has no usable UTC retrieval timestamp. | [Required evidence record](research/point-in-time-evidence-gate.md#required-evidence-record) |
| `PUBLICATION_TIME_MISSING` | ERROR | No exact publication time, conservative date interval, or explicit unresolved state is recorded. | [Missing, revised, and conflicting evidence](research/point-in-time-evidence-gate.md#missing-revised-and-conflicting-evidence) |
| `PUBLICATION_PRECISION_CONFLICT` | ERROR | Publication time, interval, precision, and type contradict each other or over-claim precision. | [`published_at` is typed, not assumed](research/point-in-time-evidence-gate.md#published_at-is-typed-not-assumed) |
| `UNRESOLVED_STATE_NOT_EXPLICIT` | ERROR | Missing or conflicting evidence is not marked explicitly unresolved. | [Status vocabulary](SOURCE_AND_CLAIM_POLICY.md#status-vocabulary) |
| `RETRIEVAL_TIME_AS_PUBLICATION` | ERROR / WARNING | Retrieval, collector-observed, or acceptance semantics stand in for publication time. | [Required evidence metadata](SOURCE_AND_CLAIM_POLICY.md#required-evidence-metadata) |
| `CUTOFF_ORDERING_CONTRADICTION` | ERROR | Publication, retrieval, snapshot, freeze, or acceptance times violate decision-cutoff ordering. | [What each decision timestamp means](research/point-in-time-evidence-gate.md#4-what-each-decision-timestamp-means) |
| `DEPENDENCY_MISSING_OR_OPEN` | ERROR | Feature dependencies are missing, reference unknown evidence, or are not closed as `ELIGIBLE`. | [Feature-level provenance is a separate requirement](research/point-in-time-evidence-gate.md#feature-level-provenance-is-a-separate-requirement) |
| `PROVENANCE_MAPPING_INVALID` | ERROR | `field_source_refs` lacks required coverage or references unknown evidence. | [Required evidence record](research/point-in-time-evidence-gate.md#required-evidence-record) |
| `REVISION_IDENTITY_MISSING` | ERROR | A content hash or SEC acceptance revision identity is missing or malformed. | [Retrieval and content hash rules](research/point-in-time-evidence-gate.md#retrieval-and-content-hash-rules) |
| `CLASSIFICATION_MISSING` | ERROR | Data class, required qualifiers, entitlement note, or redistribution status is missing or unregistered. | [Data classes](SOURCE_AND_CLAIM_POLICY.md#data-classes) |
| `ISSUER_PRIMARY_CARDINALITY` | ERROR | Not exactly one `ISSUER_PRIMARY` record is present. | [Source hierarchy and provenance](research/point-in-time-evidence-gate.md#2-source-hierarchy-and-provenance) |
| `ISSUER_URL_MISMATCH` | ERROR | The `ISSUER_PRIMARY` source URL differs from the frozen issuer release URL. | [Source hierarchy and provenance](research/point-in-time-evidence-gate.md#2-source-hierarchy-and-provenance) |
| `IDENTITY_MISMATCH` | ERROR | Manifest identity differs from the frozen event list entry. | [Historical-panel admission gate](research/point-in-time-evidence-gate.md#8-historical-panel-admission-gate) |
| `PROVENANCE_CONTRADICTION` | ERROR | Duplicate evidence IDs, hash-binding breaks, invented filing provenance, or a derived `latest_evidence_at` mismatch. | [Source arbitration and statuses](research/point-in-time-evidence-gate.md#source-arbitration-and-statuses) |
| `CONTEXT_INVALID` | ERROR | Optional event-list and selection-rule context is partial or fails the replay contract. | [Historical-panel admission gate](research/point-in-time-evidence-gate.md#8-historical-panel-admission-gate) |

## Non-goals

- No source scraping, reachability probing, or automatic correction.
- No Q-FAST panel construction, evaluation, or outcome inspection.
- No policy or schema evolution.
- No credentials, broker or MCP calls, deployment, release, or submission changes.
