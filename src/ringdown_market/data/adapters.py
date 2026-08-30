"""Read-only adapter boundaries for strategy snapshot collection.

No adapter in this package can mutate a broker, open a session, load credentials,
or reach the network. Live read-only collection is a separately recorded gate; the
in-package registry exposes fake deterministic adapters only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from .provenance import BarObservation, CorporateActionReceipt, EstimationPoint, SourceEvidence


class HostConfigRejected(ValueError):
    """Raised when a host capture configuration is unsafe or unsupported."""


CREDENTIAL_KEY_MARKERS = ("key", "secret", "token", "password", "credential", "auth")
SUPPORTED_ADAPTER_REGISTRIES = ("FAKE",)


def validate_capture_host_config(config: Mapping[str, object]) -> str:
    """Validate an explicit capture host configuration; credentials fail closed."""

    if not isinstance(config, Mapping):
        raise HostConfigRejected("host configuration must be a JSON object")

    def _walk(node: Mapping[str, object], path: str) -> None:
        for key, value in node.items():
            if not isinstance(key, str):
                raise HostConfigRejected(f"{path}: configuration keys must be strings")
            lowered = key.lower()
            if any(marker in lowered for marker in CREDENTIAL_KEY_MARKERS):
                raise HostConfigRejected(
                    f"{path}.{key}: credential-like content is forbidden in capture configuration"
                )
            if isinstance(value, Mapping):
                _walk(value, f"{path}.{key}")

    _walk(config, "config")
    registry = config.get("adapter_registry")
    if registry not in SUPPORTED_ADAPTER_REGISTRIES:
        raise HostConfigRejected(
            "adapter_registry must be one of the in-package read-only registries: "
            + ", ".join(SUPPORTED_ADAPTER_REGISTRIES)
        )
    return str(registry)


@runtime_checkable
class IssuerEvidenceAdapter(Protocol):
    """Read-only retrieval of timestamped primary issuer/SEC evidence."""

    def retrieve_evidence(self, event_id: str) -> Sequence[SourceEvidence]: ...


@runtime_checkable
class MarketDataAdapter(Protocol):
    """Read-only retrieval of synchronized adjusted equity bars."""

    def retrieve_opening_bars(self, symbol: str) -> Sequence[BarObservation]: ...

    def retrieve_estimation_series(self, symbol: str) -> Sequence[EstimationPoint]: ...

    def retrieve_corporate_actions(self, symbol: str) -> Sequence[CorporateActionReceipt]: ...


class FakeSnapshotAdapters:
    """Deterministic injected adapters for offline contract tests and inert capture."""

    def __init__(
        self,
        *,
        evidence: Sequence[SourceEvidence],
        opening_bars: Mapping[str, Sequence[BarObservation]],
        estimation_series: Mapping[str, Sequence[EstimationPoint]],
        corporate_actions: Mapping[str, Sequence[CorporateActionReceipt]] | None = None,
    ) -> None:
        self._evidence = tuple(evidence)
        self._opening_bars = {symbol: tuple(bars) for symbol, bars in opening_bars.items()}
        self._estimation_series = {
            symbol: tuple(points) for symbol, points in estimation_series.items()
        }
        self._corporate_actions = {
            symbol: tuple(actions) for symbol, actions in (corporate_actions or {}).items()
        }

    def retrieve_evidence(self, event_id: str) -> Sequence[SourceEvidence]:
        return tuple(record for record in self._evidence if record.event_id == event_id)

    def retrieve_opening_bars(self, symbol: str) -> Sequence[BarObservation]:
        return self._opening_bars.get(symbol, ())

    def retrieve_estimation_series(self, symbol: str) -> Sequence[EstimationPoint]:
        return self._estimation_series.get(symbol, ())

    def retrieve_corporate_actions(self, symbol: str) -> Sequence[CorporateActionReceipt]:
        return self._corporate_actions.get(symbol, ())


def assert_read_only_adapters(*adapters: object) -> None:
    """Fail closed when an adapter exposes any mutation-shaped capability."""

    forbidden = (
        "place_order",
        "submit_order",
        "cancel_order",
        "close_position",
        "mutate",
        "account_id",
        "credentials",
    )
    for adapter in adapters:
        for name in forbidden:
            if hasattr(adapter, name):
                raise HostConfigRejected(f"adapter exposes forbidden capability: {name}")


def collect_snapshot_inputs(
    *,
    event_id: str,
    ticker: str,
    market_proxy: str,
    sector_proxy: str,
    evidence_adapter: IssuerEvidenceAdapter,
    market_adapter: MarketDataAdapter,
) -> dict[str, object]:
    """Gather read-only inputs for one event; performs no mutation or network access."""

    assert_read_only_adapters(evidence_adapter, market_adapter)
    return {
        "evidence": evidence_adapter.retrieve_evidence(event_id),
        "opening_bars": {
            symbol: market_adapter.retrieve_opening_bars(symbol)
            for symbol in (ticker, market_proxy, sector_proxy)
        },
        "estimation_series": market_adapter.retrieve_estimation_series(ticker),
        "corporate_actions": {
            symbol: market_adapter.retrieve_corporate_actions(symbol)
            for symbol in (ticker, market_proxy, sector_proxy)
        },
    }
