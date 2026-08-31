"""Validate the self-contained PR #52 integration provenance manifest."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")
PUBLIC_REVIEW_ANCHOR = re.compile(r"^(?:pullrequestreview|issuecomment)-[0-9]+$")
EXPECTED_PRS = {34, 35, 37, 38, 39, 51, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62}
REQUIRED_ENTRY_KEYS = {
    "number",
    "url",
    "state",
    "version_impact",
    "relationship",
    "reviewed_source",
    "ancestry_closure",
    "integration_provenance",
    "conflict_or_replay_receipt",
    "targeted_verification",
    "review",
}


class ManifestError(ValueError):
    """Raised when provenance data is incomplete, inconsistent, or non-canonical."""


def _require_sha(value: object, path: str) -> None:
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise ManifestError(f"{path} must be a 40-character lowercase Git object ID")


def _require_text(value: object, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{path} must be non-empty text")


def _require_public_review_or_comment_receipt(value: object, *, url: object, path: str) -> None:
    _require_text(value, path)
    if not isinstance(url, str):
        raise ManifestError(f"{path} requires a source PR URL")
    prefix = f"{url}#"
    anchor = value.removeprefix(prefix) if isinstance(value, str) else ""
    if value == anchor or PUBLIC_REVIEW_ANCHOR.fullmatch(anchor) is None:
        raise ManifestError(f"{path} must be a public review or comment receipt on its source PR")


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{path} must be an object")
    return value


def validate_manifest(data: object) -> None:
    root = _mapping(data, "manifest")
    if root.get("schema") != "esscher.integration_manifest" or root.get("schema_version") != 2:
        raise ManifestError("schema must be esscher.integration_manifest version 2")

    integration = _mapping(root.get("integration"), "integration")
    if integration.get("pull_request") != 52:
        raise ManifestError("integration.pull_request must be 52")
    _require_text(integration.get("url"), "integration.url")
    _require_text(integration.get("audit_source"), "integration.audit_source")
    _require_text(integration.get("audited_at"), "integration.audited_at")
    _require_sha(integration.get("manifest_parent_integration_head"), "integration.parent_head")
    _require_sha(integration.get("manifest_parent_integration_tree"), "integration.parent_tree")

    entries = root.get("source_prs")
    if not isinstance(entries, list) or len(entries) != len(EXPECTED_PRS):
        raise ManifestError("source_prs must contain exactly the 16 integrated source records")
    if integration.get("audited_source_pr_count") != len(entries):
        raise ManifestError("integration.audited_source_pr_count must equal source_prs length")

    numbers = [entry.get("number") for entry in entries if isinstance(entry, dict)]
    if (
        len(numbers) != len(entries)
        or set(numbers) != EXPECTED_PRS
        or len(set(numbers)) != len(numbers)
    ):
        raise ManifestError("source_prs must contain each expected PR exactly once")

    parent_head = integration["manifest_parent_integration_head"]
    parent_tree = integration["manifest_parent_integration_tree"]
    for entry in entries:
        record = _mapping(entry, "source_pr")
        missing = REQUIRED_ENTRY_KEYS - set(record)
        if missing:
            raise ManifestError(f"PR {record.get('number')}: missing keys {sorted(missing)}")
        number = record["number"]
        if not isinstance(number, int):
            raise ManifestError("source_pr.number must be an integer")
        _require_text(record["url"], f"PR {number}.url")
        if record["state"] not in {"MERGED", "CLOSED"}:
            raise ManifestError(f"PR {number}.state must be MERGED or CLOSED")
        if record["version_impact"] not in {"major", "minor", "patch", "none"}:
            raise ManifestError(f"PR {number}.version_impact is invalid")
        if record["relationship"] != "normal_merge" and not record["relationship"].startswith(
            "already_integrated_by_ancestry"
        ):
            raise ManifestError(f"PR {number}.relationship is invalid")

        reviewed = _mapping(record["reviewed_source"], f"PR {number}.reviewed_source")
        _require_sha(reviewed.get("head"), f"PR {number}.reviewed_source.head")
        _require_sha(reviewed.get("tree"), f"PR {number}.reviewed_source.tree")
        source_merge_commit = reviewed.get("source_pr_merge_commit")
        source_merge_tree = reviewed.get("source_pr_merge_tree")
        if source_merge_commit is not None:
            _require_sha(source_merge_commit, f"PR {number}.source_pr_merge_commit")
            _require_sha(source_merge_tree, f"PR {number}.source_pr_merge_tree")
        elif source_merge_tree is not None:
            raise ManifestError(f"PR {number}.source_pr_merge_tree requires a merge commit")

        closure = _mapping(record["ancestry_closure"], f"PR {number}.ancestry_closure")
        if closure.get("checked_against_head") != parent_head:
            raise ManifestError(f"PR {number} has a mismatched ancestry parent head")
        if closure.get("checked_against_tree") != parent_tree:
            raise ManifestError(f"PR {number} has a mismatched ancestry parent tree")
        if closure.get("source_head_is_ancestor") is not True:
            raise ManifestError(f"PR {number} must record verified ancestry closure")

        provenance = _mapping(
            record["integration_provenance"], f"PR {number}.integration_provenance"
        )
        if record["relationship"] == "normal_merge":
            if provenance.get("integration_parent_head") != parent_head:
                raise ManifestError(f"PR {number} has a mismatched integration parent head")
            if provenance.get("integration_parent_tree") != parent_tree:
                raise ManifestError(f"PR {number} has a mismatched integration parent tree")
            _require_sha(provenance.get("integration_commit"), f"PR {number}.integration_commit")
            _require_sha(provenance.get("integration_tree"), f"PR {number}.integration_tree")
        else:
            if provenance.get("closure") != "ALREADY_INTEGRATED_BY_ANCESTRY":
                raise ManifestError(f"PR {number} must declare its ancestry closure")
            if provenance.get("source_head_is_ancestor_of") != parent_head:
                raise ManifestError(f"PR {number} has a mismatched closure parent head")
            if provenance.get("integration_parent_tree") != parent_tree:
                raise ManifestError(f"PR {number} has a mismatched closure parent tree")

        conflict = _mapping(record["conflict_or_replay_receipt"], f"PR {number}.conflict_receipt")
        _require_text(conflict.get("kind"), f"PR {number}.conflict_receipt.kind")
        _require_text(conflict.get("receipt"), f"PR {number}.conflict_receipt.receipt")
        if number in {57, 58, 60} and conflict["kind"] == "not_required":
            raise ManifestError(f"PR {number} requires its explicit repair/replay receipt")

        verification = _mapping(
            record["targeted_verification"], f"PR {number}.targeted_verification"
        )
        _require_text(verification.get("command"), f"PR {number}.verification.command")
        _require_text(verification.get("result"), f"PR {number}.verification.result")
        _require_text(verification.get("receipt"), f"PR {number}.verification.receipt")
        if verification.get("reviewed_source_head") != reviewed["head"]:
            raise ManifestError(f"PR {number}.verification must bind the reviewed source head")

        review = _mapping(record["review"], f"PR {number}.review")
        _require_text(review.get("task"), f"PR {number}.review.task")
        _require_text(review.get("verdict"), f"PR {number}.review.verdict")
        _require_public_review_or_comment_receipt(
            review.get("receipt"),
            url=record["url"],
            path=f"PR {number}.review.receipt",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest", nargs="?", type=Path, default=Path("docs/INTEGRATION_MANIFEST.json")
    )
    args = parser.parse_args()
    try:
        with args.manifest.open(encoding="utf-8") as handle:
            validate_manifest(json.load(handle))
    except (OSError, ValueError, ManifestError) as error:
        print(f"integration manifest invalid: {error}", file=sys.stderr)
        return 1
    print("integration manifest valid: 16 audited source PR records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
