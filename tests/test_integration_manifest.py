"""Regression gate for the self-contained integration provenance receipt."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
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


def test_integration_manifest_pins_the_public_nonrecursive_provenance_anchor() -> None:
    spec = importlib.util.spec_from_file_location("integration_manifest_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with MANIFEST.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    forged = deepcopy(manifest)
    forged_head = "0" * 40
    forged_tree = "1" * 40
    anchor = forged["integration"]["public_provenance_anchor"]
    anchor["head"] = forged_head
    anchor["tree"] = forged_tree
    anchor["url"] = f"https://github.com/Tempest-Research/esscher-market/commit/{forged_head}"
    for entry in forged["source_prs"]:
        entry["ancestry_closure"]["checked_against_anchor_head"] = forged_head
        entry["ancestry_closure"]["checked_against_anchor_tree"] = forged_tree
        provenance = entry["integration_provenance"]
        if entry["relationship"] == "normal_merge":
            provenance["provenance_anchor_head"] = forged_head
        else:
            provenance["source_head_is_ancestor_of_anchor"] = forged_head
        provenance["provenance_anchor_tree"] = forged_tree

    with pytest.raises(module.ManifestError, match="must pin the public nonrecursive anchor"):
        module.validate_manifest(forged)


def test_integration_manifest_binds_each_normal_merge_to_its_reviewed_source_merge() -> None:
    spec = importlib.util.spec_from_file_location("integration_manifest_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with MANIFEST.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    forged = deepcopy(manifest)
    normal_merge = next(
        entry for entry in forged["source_prs"] if entry["relationship"] == "normal_merge"
    )
    normal_merge["integration_provenance"]["integration_tree"] = "0" * 40

    with pytest.raises(module.ManifestError, match="must match the reviewed source PR merge"):
        module.validate_manifest(forged)
