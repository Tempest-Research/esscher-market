from __future__ import annotations

import json
from pathlib import Path

import pytest

from ringdown_market.panel.manifest import PanelRejectionReason
from ringdown_market.panel.windows import validate_market_window, validate_market_window_set

ROOT = Path(__file__).parents[1]
UNIVERSE = ROOT / "data" / "qfast-panel" / "universe"
WINDOWS = ROOT / "data" / "qfast-panel" / "market-windows"


def _event_ids() -> list[str]:
    return json.loads((UNIVERSE / "event-list-v1.json").read_text())["event_ids"]


def _window_bytes() -> list[bytes]:
    return [(WINDOWS / f"{event_id}.json").read_bytes() for event_id in _event_ids()]


def _mutate(raw: bytes, mutation) -> bytes:
    payload = json.loads(raw)
    mutation(payload)
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def test_all_twenty_three_windows_validate_as_synchronized() -> None:
    validated = validate_market_window_set(_window_bytes(), _event_ids())

    assert len(validated) == 23
    assert all(record.bar_count >= 60 for record in validated)


def test_window_bar_counts_are_aligned_across_symbols() -> None:
    for raw, event_id in zip(_window_bytes(), _event_ids(), strict=True):
        record = validate_market_window(raw, expected_event_id=event_id)
        assert record.event_id == event_id


def test_window_for_wrong_event_fails_closed() -> None:
    raw = (WINDOWS / f"{_event_ids()[0]}.json").read_bytes()

    with pytest.raises(Exception) as caught:
        validate_market_window(raw, expected_event_id="OTHER-EVENT")

    assert caught.value.reason is PanelRejectionReason.IDENTITY_MISMATCH


def test_unadjusted_window_fails_closed() -> None:
    raw = (WINDOWS / f"{_event_ids()[0]}.json").read_bytes()
    broken = _mutate(raw, lambda payload: payload.update({"adjustment": "none"}))

    with pytest.raises(Exception) as caught:
        validate_market_window(broken, expected_event_id=_event_ids()[0])

    assert caught.value.reason is PanelRejectionReason.SELECTION_RULE_VIOLATION


def test_incomplete_window_fails_closed() -> None:
    raw = (WINDOWS / f"{_event_ids()[0]}.json").read_bytes()

    def _mark_unavailable(payload: dict) -> None:
        payload["status"] = "UNAVAILABLE"

    broken = _mutate(raw, _mark_unavailable)

    with pytest.raises(Exception) as caught:
        validate_market_window(broken, expected_event_id=_event_ids()[0])

    assert caught.value.reason is PanelRejectionReason.MISSING_PRICE_POINT


def test_desynchronized_bar_counts_fail_closed() -> None:
    raw = (WINDOWS / f"{_event_ids()[0]}.json").read_bytes()

    def _desync(payload: dict) -> None:
        payload["bars"]["sector"]["bar_count"] -= 1

    broken = _mutate(raw, _desync)

    with pytest.raises(Exception) as caught:
        validate_market_window(broken, expected_event_id=_event_ids()[0])

    assert caught.value.reason is PanelRejectionReason.IDENTITY_MISMATCH
