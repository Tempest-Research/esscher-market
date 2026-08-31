"""Regression gate for the self-contained integration provenance receipt."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_integration_manifest.py"
MANIFEST = ROOT / "docs" / "INTEGRATION_MANIFEST.json"


def test_integration_manifest_is_complete_and_machine_validated() -> None:
    spec = importlib.util.spec_from_file_location("integration_manifest_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with MANIFEST.open(encoding="utf-8") as handle:
        module.validate_manifest(json.load(handle))


def test_integration_manifest_rejects_a_review_receipt_outside_its_source_pr() -> None:
    spec = importlib.util.spec_from_file_location("integration_manifest_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with MANIFEST.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    manifest["source_prs"][0]["review"]["receipt"] = (
        "https://github.com/Tempest-Research/esscher-market/pull/999#pullrequestreview-1"
    )

    with pytest.raises(module.ManifestError, match="public review or comment receipt"):
        module.validate_manifest(manifest)
