"""Offline audit helpers for deterministic evidence-bundle comparisons."""

from importlib import import_module
from typing import Any

__all__ = [
    "BundleDiffError",
    "BundleDiffErrorReason",
    "canonical_report_bytes",
    "compare",
    "compare_artifacts",
    "compare_paths",
    "main",
    "write_report",
]


def __getattr__(name: str) -> Any:
    """Load the implementation lazily so ``python -m`` stays warning-free."""

    if name not in __all__:
        raise AttributeError(name)
    module = import_module(".bundle_diff", __name__)
    return getattr(module, name)
