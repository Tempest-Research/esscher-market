"""Render one deterministic, offline evidence-to-receipt judge trace."""

# The self-contained HTML/CSS template stays legible as presentation markup.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from typing import Final
from urllib.parse import urlsplit


class JudgeTraceInputError(ValueError):
    """Raised when a packaged trace input is not strict JSON."""


@dataclass(frozen=True, slots=True)
class FrozenTraceInputs:
    """Exact packaged bytes accepted by the read-only trace renderer."""

    evidence_bytes: bytes
    accepted_bytes: bytes
    rejected_bytes: bytes
    manual_bytes: bytes


_MISSING: Final = object()
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP: Final = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
_PAPER_PNL_DECIMAL_TEXT_MAX_LENGTH: Final = 128
_SYNTHETIC_LIMITATIONS: Final = [
    "NOT_HISTORICAL_DATA",
    "NOT_ALPHA_EVIDENCE",
    "NO_BROKER_EXECUTION",
]


def load_packaged_trace_inputs() -> FrozenTraceInputs:
    """Load byte-identical copies of the merged #2 and #13 artifacts."""

    fixtures = files("esscher.demo").joinpath("fixtures")
    return FrozenTraceInputs(
        evidence_bytes=fixtures.joinpath("KR-2026Q2-EARNINGS.json").read_bytes(),
        accepted_bytes=fixtures.joinpath("scheduled_terminal_flat_v1.json").read_bytes(),
        rejected_bytes=fixtures.joinpath("scheduled_rejected_before_mutation_v1.json").read_bytes(),
        manual_bytes=fixtures.joinpath("scheduled_manual_reconciliation_v1.json").read_bytes(),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise JudgeTraceInputError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise JudgeTraceInputError(f"non-standard JSON constant: {value}")


def _parse_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JudgeTraceInputError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise JudgeTraceInputError(f"{label} root must be an object")
    return value


def _require_synthetic_boundary(value: dict[str, object], label: str) -> None:
    if (
        value.get("fixture_class") != "SYNTHETIC_CONTRACT_FIXTURE"
        or value.get("limitations") != _SYNTHETIC_LIMITATIONS
    ):
        raise JudgeTraceInputError(f"{label} boundary is invalid")


def _require_artifact_boundary(value: dict[str, object], label: str) -> None:
    artifact = value.get("artifact")
    if (
        not isinstance(artifact, dict)
        or artifact.get("run_mode") != "PAPER"
        or artifact.get("data_class") != "INDICATIVE_DATA"
        or artifact.get("claims")
        != ["PAPER_OPERATIONAL_RESULT", "INDICATIVE_DATA", "NOT_ALPHA_EVIDENCE"]
    ):
        raise JudgeTraceInputError(f"{label} artifact boundary is invalid")


def _require_evidence_boundary(value: dict[str, object]) -> None:
    if (
        value.get("schema") != "ringdown.point_in_time_evidence_manifest"
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
        or value.get("data_class") != "POINT_IN_TIME_EVENT_PANEL"
        or value.get("data_qualifiers")
        != [
            "INDICATIVE_DATA",
            "NOT_ALPHA_EVIDENCE",
            "NO_OUTCOME_VALUES",
            "NO_BROKER_EXECUTION",
        ]
        or "decision" in value
        or "permit" in value
    ):
        raise JudgeTraceInputError("evidence boundary is invalid")


def _is_normalized_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or _UTC_TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _is_public_https_url(value: object) -> bool:
    if not _is_normalized_text(value):
        return False
    assert isinstance(value, str)
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _require_evidence_provenance(value: dict[str, object]) -> None:
    context = value.get("event_context")
    records = value.get("records")
    limitations = value.get("limitations")
    if (
        not _is_normalized_text(value.get("event_id"))
        or not _is_normalized_text(value.get("issuer"))
        or not isinstance(context, dict)
        or not isinstance(records, list)
        or len(records) <= 1
        or not isinstance(records[1], dict)
        or not isinstance(limitations, list)
        or len(limitations) <= 1
        or not _is_normalized_text(limitations[1])
    ):
        raise JudgeTraceInputError("evidence provenance is invalid")

    cutoff = _utc_timestamp(value.get("decision_cutoff"))
    scheduled = _utc_timestamp(context.get("scheduled_event_at"))
    record = records[1]
    published = _utc_timestamp(record.get("published_at"))
    if (
        cutoff is None
        or scheduled is None
        or scheduled != cutoff
        or published is None
        or published > cutoff
        or not isinstance(record.get("content_sha256"), str)
        or _SHA256.fullmatch(record["content_sha256"]) is None
        or not _is_public_https_url(record.get("source_url"))
    ):
        raise JudgeTraceInputError("evidence provenance is invalid")


def _require_accepted_state(value: dict[str, object]) -> None:
    artifact = value.get("artifact")
    if (
        value.get("scenario") != "ACCEPTED_TERMINAL_FLAT"
        or not isinstance(artifact, dict)
        or artifact.get("schema") != "ringdown.scheduled_run_result"
        or artifact.get("disposition") != "EXECUTED_TO_TERMINAL"
        or artifact.get("lifecycle") != "CLOSED_FLAT"
        or artifact.get("broker_mutation") != "BOUNDED_PAPER_PIPELINE"
        or not isinstance(artifact.get("receipt"), dict)
    ):
        raise JudgeTraceInputError("accepted lifecycle state is invalid")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _canonical_receipt_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.isoformat() != value:
        return None
    return parsed


def _is_canonical_decimal_text(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= _PAPER_PNL_DECIMAL_TEXT_MAX_LENGTH:
        return False
    unsigned = value[1:] if value.startswith("-") else value
    whole, separator, fraction = unsigned.partition(".")
    if (
        not whole
        or not whole.isascii()
        or not whole.isdecimal()
        or (len(whole) > 1 and whole.startswith("0"))
        or (separator and (not fraction or not fraction.isascii() or not fraction.isdecimal()))
        or "." in fraction
    ):
        return False
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return False
    return parsed.is_finite() and format(parsed.normalize(), "f") == value


def _require_terminal_receipt(value: dict[str, object]) -> None:
    artifact = value["artifact"]
    assert isinstance(artifact, dict)
    receipt = artifact["receipt"]
    assert isinstance(receipt, dict)
    required = {
        "schema",
        "schema_version",
        "run_mode",
        "data_class",
        "claims",
        "event_run_id",
        "open_permit_id",
        "close_permit_id",
        "capability_sha256",
        "open_request_sha256",
        "close_request_sha256",
        "open_order_sha256",
        "close_order_sha256",
        "lifecycle_outcome",
        "final_flat_observed_at",
        "paper_pnl",
        "receipt_sha256",
    }
    digest_fields = (
        "capability_sha256",
        "open_request_sha256",
        "close_request_sha256",
        "open_order_sha256",
        "close_order_sha256",
        "receipt_sha256",
    )
    paper_pnl = receipt.get("paper_pnl")
    if (
        set(receipt) != required
        or receipt.get("schema") != "ringdown.paper_receipt_bundle"
        or type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
        or receipt.get("run_mode") != "PAPER"
        or receipt.get("data_class") != "INDICATIVE_DATA"
        or receipt.get("claims") != ["PAPER_OPERATIONAL_OBSERVATION", "NOT_ALPHA_EVIDENCE"]
        or receipt.get("event_run_id") != artifact.get("event_run_id")
        or receipt.get("lifecycle_outcome") != artifact.get("lifecycle")
        or not all(_is_sha256(receipt.get(field)) for field in digest_fields)
        or not _is_normalized_text(receipt.get("event_run_id"))
        or not _is_normalized_text(receipt.get("open_permit_id"))
        or not _is_normalized_text(receipt.get("close_permit_id"))
        or not isinstance(paper_pnl, dict)
        or set(paper_pnl)
        != {
            "classification",
            "gross_realized_pnl",
            "broker_fees",
            "net_realized_pnl",
            "open_filled_at",
            "close_filled_at",
            "unavailable_reason",
        }
        or paper_pnl.get("classification") != "PAPER_REALIZED_PNL"
        or not _is_canonical_decimal_text(paper_pnl.get("gross_realized_pnl"))
        or paper_pnl.get("broker_fees") is not None
        or paper_pnl.get("net_realized_pnl") is not None
        or paper_pnl.get("unavailable_reason") is not None
    ):
        raise JudgeTraceInputError("terminal receipt boundary is invalid")
    final_flat_observed_at = _canonical_receipt_datetime(receipt["final_flat_observed_at"])
    open_filled_at = _canonical_receipt_datetime(paper_pnl["open_filled_at"])
    close_filled_at = _canonical_receipt_datetime(paper_pnl["close_filled_at"])
    if (
        final_flat_observed_at is None
        or open_filled_at is None
        or close_filled_at is None
        or close_filled_at < open_filled_at
        or final_flat_observed_at < close_filled_at
    ):
        raise JudgeTraceInputError("terminal receipt boundary is invalid")
    unsigned = dict(receipt)
    receipt_sha256 = unsigned.pop("receipt_sha256")
    canonical = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if receipt_sha256 != hashlib.sha256(canonical).hexdigest():
        raise JudgeTraceInputError("terminal receipt boundary is invalid")


def _require_error_state(
    value: dict[str, object],
    label: str,
    *,
    scenario: str,
    disposition: str,
    lifecycle: str,
    error_code: str,
    broker_mutation: str,
) -> None:
    artifact = value.get("artifact")
    if (
        value.get("scenario") != scenario
        or not isinstance(artifact, dict)
        or artifact.get("schema") != "ringdown.scheduled_run_error"
        or artifact.get("disposition") != disposition
        or artifact.get("lifecycle") != lifecycle
        or artifact.get("error_code") != error_code
        or artifact.get("broker_mutation") != broker_mutation
        or "receipt" in artifact
    ):
        raise JudgeTraceInputError(f"{label} state is invalid")


def _at(root: object, *path: str | int) -> object:
    value = root
    for part in path:
        if isinstance(part, str) and isinstance(value, dict):
            if part not in value:
                return _MISSING
            value = value[part]
            continue
        if isinstance(part, int) and isinstance(value, list) and 0 <= part < len(value):
            value = value[part]
            continue
        return _MISSING
    return value


def _text(value: object) -> str:
    if value is _MISSING:
        return '<span class="missing">NOT PRESENT IN MERGED INPUT</span>'
    if value is None:
        return '<span class="null">null · explicitly unavailable</span>'
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    else:
        rendered = str(value)
    return html.escape(rendered, quote=False)


def _attribute(value: str) -> str:
    return html.escape(value, quote=True)


def _fact(label: str, value: object, source: str, *, wide: bool = False) -> str:
    width = " fact--wide" if wide else ""
    return (
        f'<div class="fact{width}">'
        f"<dt>{html.escape(label, quote=False)}</dt>"
        f'<dd data-source="{_attribute(source)}">{_text(value)}</dd>'
        f'<span class="source">{html.escape(source, quote=False)}</span>'
        "</div>"
    )


def _csp_meta() -> str:
    policy = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    return f'<meta http-equiv="Content-Security-Policy" content="{_attribute(policy)}">'


def render_judge_trace(inputs: FrozenTraceInputs) -> bytes:
    """Render deterministic HTML without network or execution capability."""

    evidence = _parse_object(inputs.evidence_bytes, "evidence")
    accepted = _parse_object(inputs.accepted_bytes, "accepted lifecycle")
    rejected = _parse_object(inputs.rejected_bytes, "rejected lifecycle")
    manual = _parse_object(inputs.manual_bytes, "manual lifecycle")
    _require_evidence_boundary(evidence)
    _require_evidence_provenance(evidence)
    _require_synthetic_boundary(accepted, "accepted lifecycle")
    _require_synthetic_boundary(rejected, "rejected lifecycle")
    _require_synthetic_boundary(manual, "manual lifecycle")
    _require_artifact_boundary(accepted, "accepted lifecycle")
    _require_artifact_boundary(rejected, "rejected lifecycle")
    _require_artifact_boundary(manual, "manual lifecycle")
    _require_accepted_state(accepted)
    _require_terminal_receipt(accepted)
    _require_error_state(
        rejected,
        "rejected lifecycle",
        scenario="REJECTED_BEFORE_MUTATION",
        disposition="REJECTED_BEFORE_MUTATION",
        lifecycle="REJECTED",
        error_code="MANIFEST_OR_STATE_REJECTED",
        broker_mutation="NOT_ATTEMPTED",
    )
    _require_error_state(
        manual,
        "manual lifecycle",
        scenario="AMBIGUOUS_MANUAL_RECONCILIATION",
        disposition="MANUAL_RECONCILIATION_REQUIRED",
        lifecycle="MANUAL_RECONCILIATION",
        error_code="AMBIGUOUS_OR_PARTIAL_BROKER_STATE",
        broker_mutation="NO_FURTHER_MUTATION",
    )

    facts = "".join(
        (
            _fact("Event", _at(evidence, "event_id"), "evidence#/event_id"),
            _fact("Issuer", _at(evidence, "issuer"), "evidence#/issuer"),
            _fact(
                "Scheduled event",
                _at(evidence, "event_context", "scheduled_event_at"),
                "evidence#/event_context/scheduled_event_at",
            ),
            _fact(
                "Decision cutoff",
                _at(evidence, "decision_cutoff"),
                "evidence#/decision_cutoff",
            ),
            _fact(
                "Source publication",
                _at(evidence, "records", 1, "published_at"),
                "evidence#/records/1/published_at",
            ),
            _fact(
                "Source content hash",
                _at(evidence, "records", 1, "content_sha256"),
                "evidence#/records/1/content_sha256",
                wide=True,
            ),
            _fact(
                "Source URL · text only",
                _at(evidence, "records", 1, "source_url"),
                "evidence#/records/1/source_url",
                wide=True,
            ),
        )
    )
    decision_fact = _fact(
        "Frozen research decision",
        _at(evidence, "decision"),
        "evidence#/decision",
        wide=True,
    )
    permit_fact = _fact(
        "Execution permit",
        _at(evidence, "permit"),
        "evidence#/permit",
        wide=True,
    )
    limitations = _fact(
        "Evidence limitation",
        _at(evidence, "limitations", 1),
        "evidence#/limitations/1",
        wide=True,
    )
    accepted_facts = "".join(
        (
            _fact(
                "Fixture class",
                _at(accepted, "fixture_class"),
                "accepted#/fixture_class",
            ),
            _fact("Scenario", _at(accepted, "scenario"), "accepted#/scenario"),
            _fact(
                "Disposition",
                _at(accepted, "artifact", "disposition"),
                "accepted#/artifact/disposition",
            ),
            _fact(
                "Lifecycle",
                _at(accepted, "artifact", "lifecycle"),
                "accepted#/artifact/lifecycle",
            ),
            _fact(
                "Mutation boundary",
                _at(accepted, "artifact", "broker_mutation"),
                "accepted#/artifact/broker_mutation",
            ),
            _fact(
                "Event run identity",
                _at(accepted, "artifact", "event_run_id"),
                "accepted#/artifact/event_run_id",
                wide=True,
            ),
            _fact(
                "Open permit",
                _at(accepted, "artifact", "receipt", "open_permit_id"),
                "accepted#/artifact/receipt/open_permit_id",
            ),
            _fact(
                "Close permit",
                _at(accepted, "artifact", "receipt", "close_permit_id"),
                "accepted#/artifact/receipt/close_permit_id",
            ),
            _fact(
                "Open MCP request",
                _at(accepted, "artifact", "receipt", "open_request_sha256"),
                "accepted#/artifact/receipt/open_request_sha256",
                wide=True,
            ),
            _fact(
                "Open-order hash · synthetic fixture",
                _at(accepted, "artifact", "receipt", "open_order_sha256"),
                "accepted#/artifact/receipt/open_order_sha256",
                wide=True,
            ),
            _fact(
                "Close MCP request",
                _at(accepted, "artifact", "receipt", "close_request_sha256"),
                "accepted#/artifact/receipt/close_request_sha256",
                wide=True,
            ),
            _fact(
                "Close-order hash · synthetic fixture",
                _at(accepted, "artifact", "receipt", "close_order_sha256"),
                "accepted#/artifact/receipt/close_order_sha256",
                wide=True,
            ),
            _fact(
                "Final-flat timestamp · synthetic fixture",
                _at(accepted, "artifact", "receipt", "final_flat_observed_at"),
                "accepted#/artifact/receipt/final_flat_observed_at",
            ),
            _fact(
                "PAPER P&L classification",
                _at(accepted, "artifact", "receipt", "paper_pnl", "classification"),
                "accepted#/artifact/receipt/paper_pnl/classification",
            ),
            _fact(
                "Gross PAPER P&L",
                _at(accepted, "artifact", "receipt", "paper_pnl", "gross_realized_pnl"),
                "accepted#/artifact/receipt/paper_pnl/gross_realized_pnl",
            ),
            _fact(
                "P&L unit / currency",
                _at(accepted, "artifact", "receipt", "paper_pnl", "currency"),
                "accepted#/artifact/receipt/paper_pnl/currency",
            ),
            _fact(
                "Net PAPER P&L",
                _at(accepted, "artifact", "receipt", "paper_pnl", "net_realized_pnl"),
                "accepted#/artifact/receipt/paper_pnl/net_realized_pnl",
            ),
            _fact(
                "Limitations",
                _at(accepted, "limitations"),
                "accepted#/limitations",
                wide=True,
            ),
        )
    )
    rejected_facts = "".join(
        (
            _fact(
                "Fixture class",
                _at(rejected, "fixture_class"),
                "rejected#/fixture_class",
            ),
            _fact("Scenario", _at(rejected, "scenario"), "rejected#/scenario"),
            _fact(
                "Disposition",
                _at(rejected, "artifact", "disposition"),
                "rejected#/artifact/disposition",
            ),
            _fact(
                "Lifecycle",
                _at(rejected, "artifact", "lifecycle"),
                "rejected#/artifact/lifecycle",
            ),
            _fact(
                "Reason code",
                _at(rejected, "artifact", "error_code"),
                "rejected#/artifact/error_code",
            ),
            _fact(
                "Mutation boundary",
                _at(rejected, "artifact", "broker_mutation"),
                "rejected#/artifact/broker_mutation",
            ),
            _fact(
                "Receipt",
                _at(rejected, "artifact", "receipt"),
                "rejected#/artifact/receipt",
                wide=True,
            ),
            _fact(
                "Limitations",
                _at(rejected, "limitations"),
                "rejected#/limitations",
                wide=True,
            ),
        )
    )
    manual_facts = "".join(
        (
            _fact(
                "Fixture class",
                _at(manual, "fixture_class"),
                "manual#/fixture_class",
            ),
            _fact("Scenario", _at(manual, "scenario"), "manual#/scenario"),
            _fact(
                "Disposition",
                _at(manual, "artifact", "disposition"),
                "manual#/artifact/disposition",
            ),
            _fact(
                "Lifecycle",
                _at(manual, "artifact", "lifecycle"),
                "manual#/artifact/lifecycle",
            ),
            _fact(
                "Reason code",
                _at(manual, "artifact", "error_code"),
                "manual#/artifact/error_code",
            ),
            _fact(
                "Mutation boundary",
                _at(manual, "artifact", "broker_mutation"),
                "manual#/artifact/broker_mutation",
            ),
            _fact(
                "Receipt",
                _at(manual, "artifact", "receipt"),
                "manual#/artifact/receipt",
                wide=True,
            ),
            _fact(
                "Limitations",
                _at(manual, "limitations"),
                "manual#/limitations",
                wide=True,
            ),
        )
    )

    style = """
:root {
  color-scheme: dark;
  --ink: #eef4ef;
  --muted: #a9b7b2;
  --dim: #81938c;
  --paper: #091411;
  --panel: #10221d;
  --panel-2: #152c25;
  --line: #2c4a40;
  --mint: #78e0b2;
  --amber: #f4c96b;
  --red: #ff8c7a;
  --blue: #8fc7ff;
  --shadow: 0 26px 80px rgba(0, 0, 0, .32);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; background: var(--paper); }
body {
  margin: 0;
  color: var(--ink);
  background:
    linear-gradient(rgba(120, 224, 178, .035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(120, 224, 178, .035) 1px, transparent 1px),
    radial-gradient(circle at 16% 0%, #18342b 0, transparent 36rem),
    var(--paper);
  background-size: 32px 32px, 32px 32px, auto, auto;
  font-family: Georgia, 'Times New Roman', serif;
  line-height: 1.55;
}
.skip-link {
  position: fixed; top: .75rem; left: .75rem; z-index: 20;
  transform: translateY(-180%); padding: .65rem 1rem;
  background: var(--mint); color: #05100d; border-radius: .35rem;
}
.skip-link:focus { transform: translateY(0); }
a { color: var(--mint); }
a:focus-visible, summary:focus-visible {
  outline: 3px solid var(--amber); outline-offset: 4px;
}
.shell { width: min(1180px, calc(100% - 2rem)); margin: 0 auto; }
.masthead { padding: 5.5rem 0 3rem; border-bottom: 1px solid var(--line); }
.eyebrow, .badge, .source, .step-no, dt, .status {
  font-family: 'Cascadia Mono', Consolas, monospace;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.eyebrow { color: var(--mint); font-size: .76rem; }
h1 { max-width: 900px; margin: .75rem 0 1rem; font-size: clamp(2.5rem, 7vw, 5.8rem); line-height: .94; letter-spacing: -.045em; }
.lede { max-width: 770px; color: var(--muted); font-size: 1.15rem; }
.badges { display: flex; flex-wrap: wrap; gap: .55rem; margin-top: 1.5rem; }
.badge { border: 1px solid var(--line); border-radius: 999px; padding: .4rem .7rem; font-size: .68rem; background: rgba(9, 20, 17, .66); }
.badge--paper { color: var(--amber); border-color: #705d2e; }
.badge--data { color: var(--blue); border-color: #35566f; }
.badge--static { color: var(--mint); border-color: #32664f; }
main { padding: 2.25rem 0 5rem; }
.orientation {
  display: grid; grid-template-columns: 1.2fr .8fr; gap: 1rem;
  margin-bottom: 1rem;
}
.card { background: rgba(16, 34, 29, .9); border: 1px solid var(--line); border-radius: .75rem; box-shadow: var(--shadow); }
.card__inner { padding: clamp(1.25rem, 3vw, 2rem); }
h2 { margin: 0 0 .75rem; font-size: clamp(1.55rem, 3vw, 2.4rem); letter-spacing: -.025em; }
.route { margin: 0; padding-left: 1.35rem; color: var(--muted); }
.route li + li { margin-top: .45rem; }
.alert { border-left: 4px solid var(--amber); }
.alert strong { color: var(--amber); }
.journey { margin-bottom: 1rem; border-left: 4px solid var(--blue); }
.journey p { max-width: 860px; color: var(--muted); }
.journey p:last-child { margin-bottom: 0; }
.contract-groups { display: grid; grid-template-columns: 1.05fr .95fr; gap: 1rem; margin: 1.25rem 0; }
.contract-group { padding: 1rem; border: 1px solid var(--line); border-radius: .55rem; background: rgba(9, 20, 17, .44); }
.contract-group h3 { margin: 0 0 .85rem; font-size: 1rem; }
.contract-flow { display: grid; gap: .7rem; margin: 0; padding: 0; list-style: none; }
.contract-flow--boundary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.contract-flow--fixtures { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.flow-stage { min-width: 0; padding: 1rem .8rem; border: 1px solid var(--line); border-radius: .45rem; background: var(--panel-2); }
.flow-stage strong { display: block; font-size: .9rem; }
.flow-stage span { display: block; margin-top: .4rem; color: var(--mint); font-family: 'Cascadia Mono', Consolas, monospace; font-size: .62rem; letter-spacing: .06em; }
.flow-stage--missing { border-color: #705d2e; background: #2b2515; }
.flow-stage--missing span { color: var(--amber); font-weight: 700; }
.flow-stage--fixture { border-color: #35566f; background: #142736; }
.flow-stage--fixture span { color: var(--blue); }
.broken-link { margin: .8rem 0 0 !important; padding: .55rem .7rem; border: 1px dashed var(--amber); color: var(--amber) !important; font-family: 'Cascadia Mono', Consolas, monospace; font-size: .68rem; letter-spacing: .06em; text-align: center; }
.fixture-note { margin: .8rem 0 0 !important; color: var(--blue) !important; font-size: .78rem; }
.trace { margin-top: 1rem; }
.trace__head { padding: 1.25rem 1.5rem; border-bottom: 1px solid var(--line); display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; }
.trace__head p { margin: 0; color: var(--muted); }
.steps { display: grid; }
.step { display: grid; grid-template-columns: 6.4rem minmax(0, 1fr); border-bottom: 1px solid var(--line); }
.step:last-child { border-bottom: 0; }
.step-no { padding: 1.5rem; color: var(--mint); font-size: .73rem; border-right: 1px solid var(--line); }
.step-body { padding: 1.5rem; }
.step-body h3 { margin: 0 0 .35rem; font-size: 1.25rem; }
.step-body > p { margin: 0 0 1rem; color: var(--muted); max-width: 760px; }
.facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .7rem; margin: 0; }
.fact { min-width: 0; padding: .9rem; background: var(--panel-2); border: 1px solid var(--line); border-radius: .45rem; }
.fact--wide { grid-column: 1 / -1; }
dt { color: var(--dim); font-size: .66rem; }
dd { margin: .38rem 0 .65rem; overflow-wrap: anywhere; font-family: 'Cascadia Mono', Consolas, monospace; font-size: .87rem; }
.source { display: block; color: var(--mint); font-size: .57rem; overflow-wrap: anywhere; }
.missing { color: var(--amber); font-weight: 700; }
.null { color: var(--red); }
.boundary { color: var(--amber); }
.lifecycle { margin-top: 1rem; }
.lifecycle__head { padding: clamp(1.25rem, 3vw, 2rem); border-bottom: 1px solid var(--line); }
.lifecycle__head p { max-width: 780px; margin: 0; color: var(--muted); }
.scenarios { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; padding: 1rem; }
.scenario { min-width: 0; border: 1px solid var(--line); border-radius: .65rem; overflow: hidden; background: rgba(9, 20, 17, .5); }
.scenario--accepted { grid-column: 1 / -1; border-top: 4px solid var(--mint); }
.scenario--rejected { border-top: 4px solid var(--red); }
.scenario--manual { border-top: 4px solid var(--amber); }
.scenario__head { padding: 1.2rem; border-bottom: 1px solid var(--line); }
.scenario__head h3 { margin: .15rem 0 .35rem; font-size: 1.35rem; }
.scenario__head p { margin: 0; color: var(--muted); }
.scenario__body { padding: 1rem; }
.scenario__note { margin: 0 0 1rem; padding: .8rem; background: var(--panel-2); border-left: 3px solid var(--line); color: var(--muted); }
footer { padding: 1.5rem 0 2.5rem; color: var(--dim); border-top: 1px solid var(--line); font-size: .82rem; }
@media (max-width: 760px) {
  .orientation { grid-template-columns: 1fr; }
  .contract-groups, .contract-flow--boundary, .contract-flow--fixtures { grid-template-columns: 1fr; }
  .step { grid-template-columns: 1fr; }
  .step-no { border-right: 0; border-bottom: 1px solid var(--line); padding: .75rem 1.25rem; }
  .facts { grid-template-columns: 1fr; }
  .fact--wide { grid-column: auto; }
  .trace__head { align-items: flex-start; flex-direction: column; }
  .scenarios { grid-template-columns: 1fr; }
  .scenario--accepted { grid-column: auto; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
@media print {
  :root { color-scheme: light; --ink: #101915; --muted: #3e4c47; --dim: #586761; --paper: #fff; --panel: #fff; --panel-2: #f1f5f2; --line: #b9c6c0; }
  body { background: #fff; }
  .card { box-shadow: none; }
}
""".strip()

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{_csp_meta()}
<title>Esscher · Frozen evidence → reconciled receipt</title>
<style>{style}</style>
</head>
<body>
<a class="skip-link" href="#main">Skip to the trace</a>
<header class="masthead">
  <div class="shell">
    <p class="eyebrow">Esscher / immutable audit view</p>
    <h1>Frozen evidence. Missing links. Receipt contracts.</h1>
    <p class="lede">A compact, offline explanation of what the merged artifacts prove—and where the causal chain is deliberately still missing.</p>
    <div class="badges" aria-label="Permanent evidence boundaries">
      <span class="badge badge--paper">PAPER</span>
      <span class="badge badge--data">INDICATIVE_DATA</span>
      <span class="badge badge--static">STATIC · READ-ONLY · NO NETWORK</span>
    </div>
  </div>
</header>
<main id="main" class="shell" tabindex="-1">
  <section class="orientation" aria-labelledby="read-title">
    <article class="card"><div class="card__inner">
      <h2 id="read-title">The 90-second route</h2>
      <ol class="route">
        <li>Read the frozen source and cutoff.</li>
        <li>Check whether a decision and permit actually exist.</li>
        <li>Inspect separately accepted lifecycle outcomes below—never infer a missing link.</li>
      </ol>
    </div></article>
    <aside class="card alert"><div class="card__inner">
      <h2>Truth before theatre</h2>
      <p><strong>The merged #2 manifest is data-only.</strong> It does not contain a research decision or permit, so this view leaves both visibly missing rather than joining unrelated fixtures.</p>
    </div></aside>
  </section>

  <section class="card journey" aria-labelledby="journey-title"><div class="card__inner">
    <p class="eyebrow">Plain-English contract path</p>
    <h2 id="journey-title">Intended contract path · decision and permit artifacts remain missing</h2>
    <div class="contract-groups">
      <section class="contract-group" aria-labelledby="frozen-boundary-title">
        <h3 id="frozen-boundary-title">Frozen #2 trace · stops after evidence</h3>
        <ol class="contract-flow contract-flow--boundary">
          <li class="flow-stage"><strong>Evidence</strong><span>FROZEN · PRESENT</span></li>
          <li class="flow-stage flow-stage--missing"><strong>Decision</strong><span>Decision · MISSING</span></li>
          <li class="flow-stage flow-stage--missing"><strong>Permit</strong><span>Permit · MISSING</span></li>
        </ol>
        <p class="broken-link">CAUSAL LINK NOT ESTABLISHED</p>
      </section>
      <section class="contract-group" aria-labelledby="fixture-boundary-title">
        <h3 id="fixture-boundary-title">Separate #13 contract fixtures · not traced outcomes</h3>
        <ol class="contract-flow contract-flow--fixtures">
          <li class="flow-stage flow-stage--fixture"><strong>Official Alpaca MCP PAPER lifecycle</strong><span>CONTRACT EXAMPLE</span></li>
          <li class="flow-stage flow-stage--fixture"><strong>Reconciled receipt</strong><span>CONTRACT EXAMPLE</span></li>
        </ol>
        <p class="fixture-note">These fixtures demonstrate contract behavior; neither is linked to the frozen KR event.</p>
      </section>
    </div>
    <p>Evidence records what was available before the cutoff. A separately frozen research verdict may approve, reject, or abstain. Only an exact eligible verdict can compile a bounded PAPER permit. The sole official MCP adapter submits or reads back deterministic package identities, closes or cancels as one bounded lifecycle, and accepts a receipt only after final-flat broker observation.</p>
    <p>The renderer copies values; it never computes a signal or permit. PAPER observations are operational evidence—not alpha, executable historical fills, or expected profitability.</p>
  </div></section>

  <section class="card trace" aria-labelledby="trace-title">
    <header class="trace__head">
      <div><p class="eyebrow">Frozen path / exact source ledger</p><h2 id="trace-title">Evidence boundary</h2></div>
      <p>Every value shows its source JSON pointer.</p>
    </header>
    <div class="steps">
      <article class="step">
        <div class="step-no">01 / Evidence</div>
        <div class="step-body">
          <h3>Point-in-time schedule and provenance</h3>
          <p>The frozen event manifest records what was knowable before the scheduled cutoff. URLs are rendered as inert text.</p>
          <dl class="facts">{facts}{limitations}</dl>
        </div>
      </article>
      <article class="step">
        <div class="step-no">02 / Decision</div>
        <div class="step-body">
          <h3>No verdict is inferred</h3>
          <p>A renderer cannot rescore evidence or convert eligibility into a research verdict.</p>
          <dl class="facts">{decision_fact}</dl>
        </div>
      </article>
      <article class="step">
        <div class="step-no">03 / Permit</div>
        <div class="step-body">
          <h3>No execution authority is invented</h3>
          <p>The accepted contract allows only an exact frozen v1 decision/evidence/input tuple to create a bounded PAPER permit. This v2 replay manifest is data-only.</p>
          <dl class="facts">{permit_fact}</dl>
        </div>
      </article>
    </div>
  </section>

  <section class="card lifecycle" aria-labelledby="lifecycle-title">
    <header class="lifecycle__head">
      <p class="eyebrow">Contract-valid outcomes / synthetic fixtures</p>
      <h2 id="lifecycle-title">Official Alpaca MCP PAPER lifecycle</h2>
      <p>These three #13 artifacts demonstrate accepted, fail-closed, and ambiguous control flow. They are separate synthetic contract fixtures—not outcomes for the frozen KR event above.</p>
    </header>
    <div class="scenarios">
      <article class="scenario scenario--accepted" aria-labelledby="accepted-title">
        <header class="scenario__head">
          <p class="status">Accepted / terminal</p>
          <h3 id="accepted-title">Accepted contract path · terminal flat</h3>
          <p>Permit identities lead to hashed request and broker-observation milestones, then a final-flat receipt.</p>
        </header>
        <div class="scenario__body">
          <p class="scenario__note">Contract example only · no broker execution occurred.</p>
          <dl class="facts">{accepted_facts}</dl>
        </div>
      </article>
      <article class="scenario scenario--rejected" aria-labelledby="rejected-title">
        <header class="scenario__head">
          <p class="status">Rejected / no mutation</p>
          <h3 id="rejected-title">Rejected before mutation</h3>
          <p>The manifest boundary fails before a host plan or broker mutation can begin.</p>
        </header>
        <div class="scenario__body">
          <p class="scenario__note">No terminal receipt exists; P&amp;L remains unavailable.</p>
          <dl class="facts">{rejected_facts}</dl>
        </div>
      </article>
      <article class="scenario scenario--manual" aria-labelledby="manual-title">
        <header class="scenario__head">
          <p class="status">Ambiguous / stopped</p>
          <h3 id="manual-title">Ambiguous · manual reconciliation</h3>
          <p>Partial or ambiguous broker truth blocks further mutation and cannot be repaired with guessed state.</p>
        </header>
        <div class="scenario__body">
          <p class="scenario__note">No terminal receipt exists; P&amp;L remains unavailable.</p>
          <dl class="facts">{manual_facts}</dl>
        </div>
      </article>
    </div>
  </section>
</main>
<footer><div class="shell">Generated deterministically from byte-identical packaged copies of merged #2 and #13 artifacts. No broker call, credential, account identifier, script, or outbound request exists in this file.</div></footer>
</body>
</html>
"""
    return page.encode("utf-8")
