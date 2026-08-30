"""Evidence packet assembly with point-in-time cutoff gates.

Every packet member carries its provenance receipt, and the packet hash binds
the exact serialized receipts. Publisher time, retrieval time, and content
identity stay separate; SEC acceptance time never substitutes for publisher
time, and nothing observed or published after the evidence cutoff enters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.receipts import SourceReceipt, source_receipt_payload
from ringdown_market.strategy.contracts import canonical_json_bytes, sha256_bytes
from ringdown_market.strategy.models import EvidenceRef, EvidenceRole


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """One packet member: receipt, decision-use role, and evidence identity.

    Retrieval pagination stays explicit; a partial retrieval never enters the
    packet silently.
    """

    evidence_id: str
    role: EvidenceRole
    receipt: SourceReceipt
    pages_retrieved: int = 1
    pages_total: int = 1

    def __post_init__(self) -> None:
        if self.pages_retrieved < 1 or self.pages_total < 1:
            raise ValueError("pagination counters must be positive integers")
        if self.pages_retrieved > self.pages_total:
            raise ValueError("retrieved pages cannot exceed total pages")


@dataclass(frozen=True, slots=True)
class EvidencePacket:
    """Immutable evidence identity bound to exact source receipts."""

    refs: tuple[EvidenceRef, ...]
    receipts: tuple[SourceReceipt, ...]
    packet_sha256: str

    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(ref.evidence_id for ref in self.refs)

    def receipt(self, evidence_id: str) -> SourceReceipt:
        matches = tuple(
            receipt
            for ref, receipt in zip(self.refs, self.receipts, strict=True)
            if ref.evidence_id == evidence_id
        )
        if len(matches) != 1:
            raise CollectorRejected(
                CollectorReason.UNSUPPORTED_INPUT,
                f"evidence.{evidence_id}",
                "evidence ID is absent from the packet",
            )
        return matches[0]


def _cutoff_gate(entry: EvidenceEntry, evidence_cutoff_at: datetime, *, path: str) -> None:
    receipt = entry.receipt
    if receipt.entitlement == "UNVERIFIED":
        raise CollectorRejected(
            CollectorReason.SOURCE_RIGHTS_UNVERIFIED,
            path,
            "source rights must be verified before packet inclusion",
        )
    if receipt.published_at is None and entry.role.is_primary:
        raise CollectorRejected(
            CollectorReason.PUBLICATION_TIME_UNKNOWN,
            path,
            "primary evidence requires a publisher timestamp",
        )
    if receipt.published_at is not None and receipt.published_at > evidence_cutoff_at:
        raise CollectorRejected(
            CollectorReason.RETRIEVED_AFTER_CUTOFF,
            f"{path}.published_at",
            "publication exceeds the evidence cutoff",
        )
    if receipt.retrieved_at > evidence_cutoff_at:
        raise CollectorRejected(
            CollectorReason.RETRIEVED_AFTER_CUTOFF,
            f"{path}.retrieved_at",
            "retrieval exceeds the evidence cutoff",
        )


def build_evidence_packet(
    entries: Sequence[EvidenceEntry],
    *,
    evidence_cutoff_at: datetime,
    permitted_source_classes: Sequence[str],
    required_source_classes: Sequence[str],
) -> EvidencePacket:
    """Assemble one immutable evidence packet or fail closed."""

    if not entries:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "evidence_packet",
            "evidence packet requires at least one member",
        )
    evidence_ids = [entry.evidence_id for entry in entries]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise CollectorRejected(
            CollectorReason.DUPLICATE_OBSERVATION,
            "evidence_packet",
            "evidence IDs must be unique",
        )
    receipt_ids = [entry.receipt.receipt_id for entry in entries]
    if len(receipt_ids) != len(set(receipt_ids)):
        raise CollectorRejected(
            CollectorReason.DUPLICATE_OBSERVATION,
            "evidence_packet",
            "receipt IDs must be unique",
        )
    refs: list[EvidenceRef] = []
    receipts: list[SourceReceipt] = []
    observed_classes: set[str] = set()
    seen_content: dict[str, str] = {}
    for entry in sorted(entries, key=lambda item: item.evidence_id):
        path = f"evidence.{entry.evidence_id}"
        if entry.pages_retrieved != entry.pages_total:
            raise CollectorRejected(
                CollectorReason.PAGINATION_INCOMPLETE,
                path,
                f"retrieved {entry.pages_retrieved} of {entry.pages_total} pages;"
                " partial retrieval never enters silently",
            )
        content_sha256 = entry.receipt.content_sha256
        if content_sha256 in seen_content:
            raise CollectorRejected(
                CollectorReason.DUPLICATE_SOURCE_RECORD,
                path,
                f"content identity duplicates evidence.{seen_content[content_sha256]}",
            )
        seen_content[content_sha256] = entry.evidence_id
        if entry.receipt.source_class not in set(permitted_source_classes):
            raise CollectorRejected(
                CollectorReason.UNPERMITTED_SOURCE_CLASS,
                path,
                f"source class {entry.receipt.source_class} is not permitted by policy",
            )
        _cutoff_gate(entry, evidence_cutoff_at, path=path)
        refs.append(
            EvidenceRef(
                evidence_id=entry.evidence_id,
                role=entry.role,
                source_class=entry.receipt.source_class,
                published_at=entry.receipt.published_at,
                available_at=entry.receipt.retrieved_at,
                content_sha256=entry.receipt.content_sha256,
            )
        )
        receipts.append(entry.receipt)
        observed_classes.add(entry.receipt.source_class)
    missing_required = sorted(set(required_source_classes) - observed_classes)
    if missing_required:
        raise CollectorRejected(
            CollectorReason.FEATURE_DEPENDENCY_MISSING,
            "evidence_packet.source_classes",
            f"required source classes are missing: {', '.join(missing_required)}",
        )
    packet_payload = {receipt.receipt_id: source_receipt_payload(receipt) for receipt in receipts}
    packet_sha256 = sha256_bytes(canonical_json_bytes(packet_payload))
    return EvidencePacket(
        refs=tuple(refs),
        receipts=tuple(receipts),
        packet_sha256=packet_sha256,
    )


def source_refs_for(
    packet: EvidencePacket,
    *,
    source_classes: Sequence[str],
) -> tuple[str, ...]:
    """Return sorted evidence IDs whose source class matches any supplied class."""

    wanted = set(source_classes)
    return tuple(sorted(ref.evidence_id for ref in packet.refs if ref.source_class in wanted))


def receipt_index(packet: EvidencePacket) -> Mapping[str, SourceReceipt]:
    """Return evidence-ID-keyed receipts for downstream feature builders."""

    return {
        ref.evidence_id: receipt for ref, receipt in zip(packet.refs, packet.receipts, strict=True)
    }
