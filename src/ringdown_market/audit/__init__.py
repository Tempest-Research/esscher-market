"""Offline audit helpers for Esscher evidence artifacts."""

from importlib import import_module
from typing import Any

_BUNDLE_DIFF_NAMES: tuple[str, ...] = (
    "BundleDiffError",
    "BundleDiffErrorReason",
    "canonical_report_bytes",
    "compare",
    "compare_artifacts",
    "compare_paths",
    "main",
    "write_report",
)
_SOURCE_HEALTH_NAMES: tuple[str, ...] = (
    "Finding",
    "FindingSeverity",
    "SourceHealthCode",
    "SourceHealthReport",
    "SourceHealthStatus",
    "check_manifest",
    "check_path",
)

__all__ = [*_BUNDLE_DIFF_NAMES, *_SOURCE_HEALTH_NAMES]


def __getattr__(name: str) -> Any:
    """Load the implementation lazily so ``python -m`` stays warning-free."""

    if name in _SOURCE_HEALTH_NAMES:
        module = import_module(".source_health", __name__)
        return getattr(module, name)
    if name in _BUNDLE_DIFF_NAMES:
        module = import_module(".bundle_diff", __name__)
        return getattr(module, name)
    raise AttributeError(name)
