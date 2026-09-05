from __future__ import annotations

import json
from pathlib import Path

import pytest

from esscher.panel.manifest import PanelRejectionReason
from esscher.panel.universe import validate_panel_universe

ROOT = Path(__file__).parents[1]
UNIVERSE = ROOT / "data" / "qfast-panel" / "universe"


def _universe_bytes() -> tuple[bytes, bytes, list[bytes]]:
    rule = (UNIVERSE / "selection-rule-v1.json").read_bytes()
    event_list = (UNIVERSE / "event-list-v1.json").read_bytes()
    manifests = [path.read_bytes() for path in sorted((UNIVERSE / "events").glob("*.json"))]
    return rule, event_list, manifests


def _mutate(raw: bytes, mutation) -> bytes:
    payload = json.loads(raw)
    mutation(payload)
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _assert_rejected(
    manifests: list[bytes],
    reason: PanelRejectionReason,
    *,
    rule: bytes | None = None,
    event_list: bytes | None = None,
) -> Exception:
    base_rule, base_list, _ = _universe_bytes()
    with pytest.raises(Exception) as caught:
        validate_panel_universe(rule or base_rule, event_list or base_list, manifests)
    assert getattr(caught.value, "reason", None) is reason
    return caught.value


def test_twenty_three_frozen_events_validate_as_data_only_evidence() -> None:
    rule, event_list, manifests = _universe_bytes()

    validated = validate_panel_universe(rule, event_list, manifests)

    assert len(validated) == 23
    assert all(not item.permit_eligible for item in validated)


def test_universe_meets_the_twenty_to_thirty_panel_floor() -> None:
    _, event_list, _ = _universe_bytes()
    payload = json.loads(event_list)
    assert 20 <= len(payload["event_ids"]) <= 30


def test_universe_excludes_p0_contract_development_events() -> None:
    _, event_list, _ = _universe_bytes()
    payload = json.loads(event_list)
    p0 = {
        "KR-2026Q2-EARNINGS",
        "GIS-2027Q1-EARNINGS",
        "MU-2026Q4-EARNINGS",
        "NKE-2027Q1-EARNINGS",
    }
    assert not p0 & set(payload["event_ids"])


def test_event_list_freeze_binds_selection_rule_bytes() -> None:
    rule, event_list, manifests = _universe_bytes()
    broken_rule = _mutate(rule, lambda payload: payload.update({"rule_id": "OTHER"}))

    _assert_rejected(
        manifests, PanelRejectionReason.HASH_MISMATCH, rule=broken_rule, event_list=event_list
    )


def test_unknown_manifest_field_fails_closed() -> None:
    _, _, manifests = _universe_bytes()
    manifests[0] = _mutate(manifests[0], lambda payload: payload.update({"extra": 1}))

    _assert_rejected(manifests, PanelRejectionReason.UNKNOWN_FIELD)


def test_post_cutoff_publication_bound_fails_closed() -> None:
    _, _, manifests = _universe_bytes()

    def _postdate(payload: dict) -> None:
        payload["records"][0]["published_at_interval"]["end"] = "2026-12-31T00:00:00Z"
        payload["latest_evidence_at"] = "2026-12-31T00:00:00Z"

    manifests[0] = _mutate(manifests[0], _postdate)

    _assert_rejected(manifests, PanelRejectionReason.POINT_IN_TIME_VIOLATION)


def test_manifest_not_bound_to_event_list_bytes_fails_closed() -> None:
    _, event_list, manifests = _universe_bytes()
    broken_list = _mutate(event_list, lambda payload: payload.update({"list_id": "OTHER"}))

    _assert_rejected(manifests, PanelRejectionReason.HASH_MISMATCH, event_list=broken_list)


def test_missing_manifest_for_frozen_event_fails_closed() -> None:
    _, _, manifests = _universe_bytes()

    _assert_rejected(manifests[:-1], PanelRejectionReason.MISSING_FIELD)
