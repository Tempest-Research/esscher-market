from __future__ import annotations

import json
from pathlib import Path

import pytest

from ringdown_market.contracts.latency_profile import (
    latency_profile_content_sha256,
    packaged_latency_profile_bytes,
)
from ringdown_market.panel.assembler import assemble_panel_report
from ringdown_market.panel.manifest import PanelRejected, PanelRejectionReason

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC_RULE = FIXTURES / "synthetic_qfast_panel_selection_rule.json"
SYNTHETIC_MANIFEST = FIXTURES / "synthetic_qfast_panel_manifest.json"
SYNTHETIC_BUNDLE = FIXTURES / "synthetic_qfast_panel_bundle.json"


def _profile_bytes() -> bytes:
    return packaged_latency_profile_bytes()


def test_panel_report_binds_supplied_latency_profile() -> None:
    report = json.loads(
        assemble_panel_report(
            SYNTHETIC_MANIFEST.read_bytes(),
            SYNTHETIC_RULE.read_bytes(),
            SYNTHETIC_BUNDLE.read_bytes(),
            latency_profile_bytes=_profile_bytes(),
        )
    )

    assert report["latency_profile"]["kind"] == "PREREGISTERED"
    assert report["latency_profile"]["p95_latency_ms"] == 30000
    assert report["latency_profile"]["promotion_eligible"] is True


def test_panel_report_unchanged_without_latency_profile() -> None:
    report = json.loads(
        assemble_panel_report(
            SYNTHETIC_MANIFEST.read_bytes(),
            SYNTHETIC_RULE.read_bytes(),
            SYNTHETIC_BUNDLE.read_bytes(),
        )
    )

    assert "latency_profile" not in report


def test_panel_rejects_latency_profile_p95_mismatch() -> None:
    payload = json.loads(_profile_bytes())
    payload["p95_latency_ms"] = 9999
    payload["content_sha256"] = latency_profile_content_sha256(payload)

    with pytest.raises(PanelRejected) as caught:
        assemble_panel_report(
            SYNTHETIC_MANIFEST.read_bytes(),
            SYNTHETIC_RULE.read_bytes(),
            SYNTHETIC_BUNDLE.read_bytes(),
            latency_profile_bytes=(json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
        )

    assert caught.value.reason is PanelRejectionReason.LATENCY_PROFILE_NOT_MEASURED


def test_panel_rejects_synthetic_latency_profile() -> None:
    payload = json.loads(_profile_bytes())
    payload["kind"] = "SYNTHETIC"
    payload["p95_latency_ms"] = 30000
    payload["content_sha256"] = latency_profile_content_sha256(payload)

    with pytest.raises(PanelRejected):
        assemble_panel_report(
            SYNTHETIC_MANIFEST.read_bytes(),
            SYNTHETIC_RULE.read_bytes(),
            SYNTHETIC_BUNDLE.read_bytes(),
            latency_profile_bytes=(json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(),
        )
