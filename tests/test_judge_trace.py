import importlib
import importlib.util
import json
import re
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest

from ringdown_market.cli import main

ROOT = Path(__file__).parents[1]


class _TagAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.tags.append((tag, dict(attrs)))


def _relative_luminance(color: str) -> float:
    raw = color.removeprefix("#")
    rgb = [int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in rgb
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_cli_exposes_read_only_judge_trace_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "render-judge-trace" in help_text
    assert "offline" in help_text.lower()
    assert "read-only" in help_text.lower()


def test_packaged_inputs_are_byte_identical_to_merged_sources() -> None:
    package_spec = importlib.util.find_spec("ringdown_market.demo")
    assert package_spec is not None
    spec = importlib.util.find_spec("ringdown_market.demo.judge_trace")
    assert spec is not None
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")

    inputs = judge_trace.load_packaged_trace_inputs()

    assert (
        inputs.evidence_bytes
        == (ROOT / "data" / "earnings-replays" / "events" / "KR-2026Q2-EARNINGS.json").read_bytes()
    )
    assert (
        inputs.accepted_bytes
        == (ROOT / "tests" / "contract_fixtures" / "scheduled_terminal_flat_v1.json").read_bytes()
    )
    assert (
        inputs.rejected_bytes
        == (
            ROOT / "tests" / "contract_fixtures" / "scheduled_rejected_before_mutation_v1.json"
        ).read_bytes()
    )
    assert (
        inputs.manual_bytes
        == (
            ROOT / "tests" / "contract_fixtures" / "scheduled_manual_reconciliation_v1.json"
        ).read_bytes()
    )


def test_renderer_produces_byte_stable_offline_evidence_trace() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    inputs = judge_trace.load_packaged_trace_inputs()

    first = judge_trace.render_judge_trace(inputs)
    second = judge_trace.render_judge_trace(inputs)

    assert first == second
    page = first.decode("utf-8")
    assert page.startswith("<!doctype html>")
    assert "Esscher · Frozen evidence → reconciled receipt" in page
    assert "PAPER" in page
    assert "INDICATIVE_DATA" in page
    assert "STATIC · READ-ONLY · NO NETWORK" in page
    assert "KR-2026Q2-EARNINGS" in page
    assert "2026-09-11T12:00:00Z" in page
    assert "2026-08-14T20:15:00Z" in page
    assert "a3111df14b3b1e4ece862d0907d85de7a187a7ab8c5cb7818641533f702e5589" in page
    assert 'data-source="evidence#/records/1/source_url"' in page
    assert 'data-source="evidence#/decision"' in page
    assert "NOT PRESENT IN MERGED INPUT" in page
    assert "data-only and cannot generate an execution permit" in page
    assert "default-src &#x27;none&#x27;" in page
    assert "<script" not in page
    assert 'href="http' not in page
    assert 'src="http' not in page


def test_renderer_shows_contract_valid_terminal_rejected_and_manual_outcomes() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    page = judge_trace.render_judge_trace(judge_trace.load_packaged_trace_inputs()).decode("utf-8")

    assert "Official Alpaca MCP PAPER lifecycle" in page
    assert "Accepted contract path · terminal flat" in page
    assert "ACCEPTED_TERMINAL_FLAT" in page
    assert "CLOSED_FLAT" in page
    assert "PAPER_REALIZED_PNL" in page
    assert 'data-source="accepted#/artifact/receipt/paper_pnl/gross_realized_pnl"' in page
    assert (
        '<dd data-source="accepted#/artifact/receipt/paper_pnl/currency">'
        '<span class="missing">NOT PRESENT IN MERGED INPUT</span>'
    ) in page
    assert "P&amp;L unit / currency" in page
    assert "Contract example only · no broker execution occurred." in page
    assert "Open-order hash · synthetic fixture" in page
    assert ">35<" in page

    assert "Rejected before mutation" in page
    assert "REJECTED_BEFORE_MUTATION" in page
    assert "NOT_ATTEMPTED" in page
    assert 'data-source="rejected#/artifact/receipt"' in page

    assert "Ambiguous · manual reconciliation" in page
    assert "AMBIGUOUS_MANUAL_RECONCILIATION" in page
    assert "MANUAL_RECONCILIATION" in page
    assert "NO_FURTHER_MUTATION" in page
    assert 'data-source="manual#/artifact/receipt"' in page

    assert page.count("SYNTHETIC_CONTRACT_FIXTURE") >= 3
    assert page.count("NO_BROKER_EXECUTION") >= 3
    assert "No terminal receipt exists; P&amp;L remains unavailable." in page


def test_untrusted_text_is_escaped_and_never_becomes_an_html_attribute() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    inputs = judge_trace.load_packaged_trace_inputs()
    evidence = json.loads(inputs.evidence_bytes)
    evidence["issuer"] = "</dd><img src=x onerror=alert(1)>"
    evidence["records"][1]["source_url"] = 'javascript:alert(2)" autofocus onfocus="alert(3)'
    attacked = replace(
        inputs,
        evidence_bytes=json.dumps(evidence, ensure_ascii=False).encode("utf-8"),
    )

    page = judge_trace.render_judge_trace(attacked).decode("utf-8")

    assert "&lt;/dd&gt;&lt;img src=x onerror=alert(1)&gt;" in page
    audit = _TagAudit()
    audit.feed(page)
    assert all(tag not in {"img", "script"} for tag, _ in audit.tags)
    assert all(
        not ({"onerror", "onfocus", "autofocus"} & set(attributes)) for _, attributes in audit.tags
    )
    for _, attributes in audit.tags:
        for attribute in ("href", "src"):
            value = attributes.get(attribute)
            assert value is None or not value.lower().startswith("javascript:")


def test_renderer_rejects_a_fixture_that_weakens_the_safety_labels() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    inputs = judge_trace.load_packaged_trace_inputs()
    accepted = json.loads(inputs.accepted_bytes)
    accepted["limitations"].remove("NO_BROKER_EXECUTION")
    weakened = replace(
        inputs,
        accepted_bytes=json.dumps(accepted).encode("utf-8"),
    )

    with pytest.raises(judge_trace.JudgeTraceInputError, match="accepted lifecycle boundary"):
        judge_trace.render_judge_trace(weakened)


def test_renderer_rejects_a_nonpaper_lifecycle_artifact() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    inputs = judge_trace.load_packaged_trace_inputs()
    accepted = json.loads(inputs.accepted_bytes)
    accepted["artifact"]["run_mode"] = "LIVE"
    nonpaper = replace(
        inputs,
        accepted_bytes=json.dumps(accepted).encode("utf-8"),
    )

    with pytest.raises(
        judge_trace.JudgeTraceInputError,
        match="accepted lifecycle artifact boundary",
    ):
        judge_trace.render_judge_trace(nonpaper)


def test_renderer_rejects_an_unsupported_evidence_contract() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    inputs = judge_trace.load_packaged_trace_inputs()
    evidence = json.loads(inputs.evidence_bytes)
    evidence["schema_version"] = 1
    unsupported = replace(
        inputs,
        evidence_bytes=json.dumps(evidence).encode("utf-8"),
    )

    with pytest.raises(judge_trace.JudgeTraceInputError, match="evidence boundary"):
        judge_trace.render_judge_trace(unsupported)


def test_renderer_rejects_an_accepted_outcome_without_a_terminal_receipt() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    inputs = judge_trace.load_packaged_trace_inputs()
    accepted = json.loads(inputs.accepted_bytes)
    del accepted["artifact"]["receipt"]
    incomplete = replace(
        inputs,
        accepted_bytes=json.dumps(accepted).encode("utf-8"),
    )

    with pytest.raises(
        judge_trace.JudgeTraceInputError,
        match="accepted lifecycle state",
    ):
        judge_trace.render_judge_trace(incomplete)


def test_renderer_rejects_a_manual_stop_that_claims_a_receipt() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    inputs = judge_trace.load_packaged_trace_inputs()
    manual = json.loads(inputs.manual_bytes)
    manual["artifact"]["receipt"] = {"paper_pnl": "invented"}
    invented = replace(
        inputs,
        manual_bytes=json.dumps(manual).encode("utf-8"),
    )

    with pytest.raises(
        judge_trace.JudgeTraceInputError,
        match="manual lifecycle state",
    ):
        judge_trace.render_judge_trace(invented)


def test_renderer_rejects_a_receipt_outside_the_paper_boundary() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    inputs = judge_trace.load_packaged_trace_inputs()
    accepted = json.loads(inputs.accepted_bytes)
    accepted["artifact"]["receipt"]["run_mode"] = "LIVE"
    nonpaper = replace(
        inputs,
        accepted_bytes=json.dumps(accepted).encode("utf-8"),
    )

    with pytest.raises(
        judge_trace.JudgeTraceInputError,
        match="terminal receipt boundary",
    ):
        judge_trace.render_judge_trace(nonpaper)


def test_cli_writes_a_byte_stable_self_contained_trace(tmp_path: Path) -> None:
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"

    assert main(["render-judge-trace", "--output", str(first)]) == 0
    assert main(["render-judge-trace", "--output", str(second)]) == 0

    assert first.read_bytes() == second.read_bytes()
    page = first.read_text(encoding="utf-8")
    assert '<html lang="en">' in page
    assert 'href="#main"' in page
    assert 'id="main"' in page
    assert "prefers-reduced-motion: reduce" in page
    assert ":focus-visible" in page
    assert "aria-labelledby" in page


def test_all_normal_text_palette_tokens_meet_wcag_aa_contrast() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    page = judge_trace.render_judge_trace(judge_trace.load_packaged_trace_inputs()).decode("utf-8")
    root_tokens = page.split(":root {", 1)[1].split("}", 1)[0]
    colors = dict(re.findall(r"--([a-z-]+): (#[0-9a-f]{6});", root_tokens))

    for foreground in ("ink", "muted", "dim", "mint", "amber", "red", "blue"):
        assert _contrast_ratio(colors[foreground], colors["panel"]) >= 4.5


def test_page_explains_the_full_read_only_contract_path_in_plain_english() -> None:
    judge_trace = importlib.import_module("ringdown_market.demo.judge_trace")
    page = judge_trace.render_judge_trace(judge_trace.load_packaged_trace_inputs()).decode("utf-8")

    assert "Frozen evidence. Missing links. Receipt contracts." in page
    assert "Intended contract path · decision and permit artifacts remain missing" in page
    assert "Frozen #2 trace · stops after evidence" in page
    assert "CAUSAL LINK NOT ESTABLISHED" in page
    assert "Separate #13 contract fixtures · not traced outcomes" in page
    assert 'content: "→"' not in page
    assert "Decision · MISSING" in page
    assert "Permit · MISSING" in page
    assert "Evidence → Decision → Permit →" not in page
    assert "Official Alpaca MCP PAPER lifecycle" in page
    assert "Reconciled receipt" in page
    assert "The renderer copies values; it never computes a signal or permit." in page
    assert (
        "PAPER observations are operational evidence—not alpha, executable historical fills, "
        "or expected profitability."
    ) in page
