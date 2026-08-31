"""Offline audit helpers for Esscher evidence artifacts."""

from importlib import import_module
from typing import Any

_BUNDLE_DIFF_EXPORTS: dict[str, str] = {
    "BundleDiffError": "BundleDiffError",
    "BundleDiffErrorReason": "BundleDiffErrorReason",
    "canonical_report_bytes": "canonical_report_bytes",
    "bundle_diff_canonical_report_bytes": "canonical_report_bytes",
    "compare": "compare",
    "compare_artifacts": "compare_artifacts",
    "compare_paths": "compare_paths",
    "main": "main",
    "write_report": "write_report",
}
_SOURCE_HEALTH_EXPORTS: dict[str, str] = {
    "Finding": "Finding",
    "FindingSeverity": "FindingSeverity",
    "SourceHealthCode": "SourceHealthCode",
    "SourceHealthReport": "SourceHealthReport",
    "SourceHealthStatus": "SourceHealthStatus",
    "source_health_canonical_report_bytes": "canonical_report_bytes",
    "check_manifest": "check_manifest",
    "check_path": "check_path",
}

__all__ = [*_BUNDLE_DIFF_EXPORTS, *_SOURCE_HEALTH_EXPORTS]


def __getattr__(name: str) -> Any:
    """Load the implementation lazily so ``python -m`` stays warning-free."""

    source_health_name = _SOURCE_HEALTH_EXPORTS.get(name)
    if source_health_name is not None:
        module = import_module(".source_health", __name__)
        return getattr(module, source_health_name)
    bundle_diff_name = _BUNDLE_DIFF_EXPORTS.get(name)
    if bundle_diff_name is not None:
        module = import_module(".bundle_diff", __name__)
        return getattr(module, bundle_diff_name)
    raise AttributeError(name)
