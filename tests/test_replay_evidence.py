from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ringdown_market.contracts.replay_evidence import (
    ReplayEvidenceRejected,
    ReplayEvidenceRejectionReason,
    validate_replay_evidence_set,
)

ROOT = Path(__file__).parents[1]
DATA = ROOT / "data" / "earnings-replays"
EVENT_FILES = (
    DATA / "events" / "KR-2026Q2-EARNINGS.json",
    DATA / "events" / "GIS-2027Q1-EARNINGS.json",
    DATA / "events" / "MU-2026Q4-EARNINGS.json",
    DATA / "events" / "NKE-2027Q1-EARNINGS.json",
)


def _bundle() -> tuple[bytes, bytes, list[bytes]]:
    return (
        (DATA / "event-list-v1.json").read_bytes(),
        (DATA / "selection-rule-v1.json").read_bytes(),
        [path.read_bytes() for path in EVENT_FILES],
    )


def _mutate(raw: bytes, mutation) -> bytes:
    payload = json.loads(raw)
    mutation(payload)
    return (json.dumps(payload, indent=2) + "\n").encode()


def _rebind_bundle(
    event_list: bytes,
    selection_rule: bytes,
    manifests: list[bytes],
) -> tuple[bytes, bytes, list[bytes]]:
    selection_rule_sha256 = hashlib.sha256(selection_rule).hexdigest()
    event_list = _mutate(
        event_list,
        lambda payload: payload.update({"selection_rule_sha256": selection_rule_sha256}),
    )
    event_list_sha256 = hashlib.sha256(event_list).hexdigest()
    manifests = [
        _mutate(
            raw,
            lambda payload: payload.update(
                {
                    "event_list_sha256": event_list_sha256,
                    "selection_rule_sha256": selection_rule_sha256,
                }
            ),
        )
        for raw in manifests
    ]
    return event_list, selection_rule, manifests


def _assert_rejected(
    manifests: list[bytes],
    reason: ReplayEvidenceRejectionReason,
    *,
    event_list_bytes: bytes | None = None,
    selection_rule_bytes: bytes | None = None,
) -> ReplayEvidenceRejected:
    event_list, selection_rule, _ = _bundle()
    with pytest.raises(ReplayEvidenceRejected) as caught:
        validate_replay_evidence_set(
            event_list_bytes or event_list,
            selection_rule_bytes or selection_rule,
            manifests,
        )
    assert caught.value.reason is reason
    return caught.value


def test_four_frozen_events_validate_as_data_only_evidence() -> None:
    event_list, selection_rule, manifests = _bundle()

    validated = validate_replay_evidence_set(event_list, selection_rule, manifests)

    assert tuple(item.event_id for item in validated) == (
        "KR-2026Q2-EARNINGS",
        "GIS-2027Q1-EARNINGS",
        "MU-2026Q4-EARNINGS",
        "NKE-2027Q1-EARNINGS",
    )
    assert all(not item.permit_eligible for item in validated)


def test_protocol_classifications_do_not_claim_issuer_source_provenance() -> None:
    _, _, manifests = _bundle()

    for raw in manifests:
        field_source_refs = json.loads(raw)["field_source_refs"]
        assert "sector" not in field_source_refs
        assert "market_proxy" not in field_source_refs
        assert "sector_proxy" not in field_source_refs


def test_missing_event_timezone_fails_closed() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0], lambda payload: payload["event_context"].pop("event_timezone")
    )

    caught = _assert_rejected(manifests, ReplayEvidenceRejectionReason.MISSING_FIELD)

    assert caught.path.endswith("event_context.event_timezone")


def test_post_cutoff_retrieval_timestamp_fails_closed() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload["records"][0].update({"retrieved_at": "2026-09-11T12:00:01Z"}),
    )

    caught = _assert_rejected(manifests, ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION)

    assert caught.path.endswith("records[0].retrieved_at")


def test_missing_event_context_provenance_fails_closed() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload["field_source_refs"].pop("scheduled_event_at"),
    )

    caught = _assert_rejected(manifests, ReplayEvidenceRejectionReason.MISSING_PROVENANCE)

    assert caught.path.endswith("field_source_refs.scheduled_event_at")


def test_duplicate_event_manifest_fails_closed() -> None:
    _, _, manifests = _bundle()
    manifests[1] = manifests[0]

    _assert_rejected(manifests, ReplayEvidenceRejectionReason.DUPLICATE_EVENT_ID)


def test_event_list_hash_mismatch_fails_closed() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload.update({"event_list_sha256": "0" * 64}),
    )

    _assert_rejected(manifests, ReplayEvidenceRejectionReason.HASH_MISMATCH)


def test_selection_rule_hash_mismatch_fails_closed() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload.update({"selection_rule_sha256": "f" * 64}),
    )

    _assert_rejected(manifests, ReplayEvidenceRejectionReason.HASH_MISMATCH)


def test_event_list_duplicate_id_fails_closed() -> None:
    event_list, selection_rule, manifests = _bundle()
    duplicate_list = _mutate(
        event_list,
        lambda payload: payload["event_ids"].__setitem__(1, payload["event_ids"][0]),
    )

    caught = _assert_rejected(
        manifests,
        ReplayEvidenceRejectionReason.DUPLICATE_EVENT_ID,
        event_list_bytes=duplicate_list,
        selection_rule_bytes=selection_rule,
    )

    assert caught.path == "event_list.event_ids[1]"


def test_selection_rule_cannot_disable_frozen_criterion() -> None:
    event_list, selection_rule, manifests = _bundle()
    selection_rule = _mutate(
        selection_rule,
        lambda payload: payload["criteria"].update(
            {"post_cutoff_source_paths_forbidden_at_freeze": False}
        ),
    )
    event_list, selection_rule, manifests = _rebind_bundle(event_list, selection_rule, manifests)

    caught = _assert_rejected(
        manifests,
        ReplayEvidenceRejectionReason.SELECTION_RULE_VIOLATION,
        event_list_bytes=event_list,
        selection_rule_bytes=selection_rule,
    )

    assert caught.path.endswith("post_cutoff_source_paths_forbidden_at_freeze")


def test_official_session_source_requires_frozen_provenance() -> None:
    event_list, selection_rule, manifests = _bundle()
    selection_rule = _mutate(
        selection_rule,
        lambda payload: payload["official_session_source"].pop("content_sha256"),
    )
    event_list, selection_rule, manifests = _rebind_bundle(event_list, selection_rule, manifests)

    caught = _assert_rejected(
        manifests,
        ReplayEvidenceRejectionReason.MISSING_FIELD,
        event_list_bytes=event_list,
        selection_rule_bytes=selection_rule,
    )

    assert caught.path.endswith("official_session_source.content_sha256")


def test_material_event_context_conflict_fails_closed() -> None:
    event_list, selection_rule, manifests = _bundle()
    event_list = _mutate(
        event_list,
        lambda payload: payload["events"][0].update(
            {"missing_or_conflicting_evidence": ["scheduled time conflict"]}
        ),
    )
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload["event_context"].update(
            {"missing_or_conflicting_evidence": ["scheduled time conflict"]}
        ),
    )
    event_list, selection_rule, manifests = _rebind_bundle(event_list, selection_rule, manifests)

    caught = _assert_rejected(
        manifests,
        ReplayEvidenceRejectionReason.MISSING_PROVENANCE,
        event_list_bytes=event_list,
        selection_rule_bytes=selection_rule,
    )

    assert caught.path.endswith("missing_or_conflicting_evidence")


def test_event_list_post_freeze_timestamp_fails_closed() -> None:
    event_list, selection_rule, manifests = _bundle()
    event_list = _mutate(
        event_list,
        lambda payload: payload.update({"frozen_at": "2026-08-29T20:09:22Z"}),
    )
    event_list, selection_rule, manifests = _rebind_bundle(event_list, selection_rule, manifests)

    caught = _assert_rejected(
        manifests,
        ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION,
        event_list_bytes=event_list,
        selection_rule_bytes=selection_rule,
    )

    assert caught.path == "event_list.frozen_at"


def test_manifest_post_freeze_snapshot_fails_closed() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload.update(
            {
                "feature_snapshot_at": "2026-08-29T20:09:22Z",
                "frozen_at": "2026-08-29T20:09:22Z",
            }
        ),
    )

    caught = _assert_rejected(manifests, ReplayEvidenceRejectionReason.POINT_IN_TIME_VIOLATION)

    assert caught.path.endswith("feature_snapshot_at")


def test_issuer_primary_source_must_match_frozen_event_url() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload["records"][0].update(
            {"source_url": "https://example.com/swapped-issuer-release"}
        ),
    )

    caught = _assert_rejected(manifests, ReplayEvidenceRejectionReason.PROVENANCE_MISMATCH)

    assert caught.path.endswith("records[0].source_url")


def test_manifest_requires_exactly_one_issuer_primary_record() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload["records"][1].update({"source_kind": "ISSUER_PRIMARY"}),
    )

    caught = _assert_rejected(manifests, ReplayEvidenceRejectionReason.MISSING_PROVENANCE)

    assert caught.path.endswith("records")


def test_manifest_redistribution_status_must_be_metadata_and_hash_only() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0], lambda payload: payload.update({"redistribution_status": "FULL_CONTENT"})
    )

    caught = _assert_rejected(manifests, ReplayEvidenceRejectionReason.CLAIM_BOUNDARY_MISMATCH)

    assert caught.path.endswith("redistribution_status")


def test_record_redistribution_status_must_be_metadata_and_hash_only() -> None:
    _, _, manifests = _bundle()
    manifests[0] = _mutate(
        manifests[0],
        lambda payload: payload["records"][0].update({"redistribution_status": "FULL_CONTENT"}),
    )

    caught = _assert_rejected(manifests, ReplayEvidenceRejectionReason.CLAIM_BOUNDARY_MISMATCH)

    assert caught.path.endswith("records[0].redistribution_status")
