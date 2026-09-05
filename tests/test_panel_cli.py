from __future__ import annotations

import json
from pathlib import Path

import pytest

from esscher.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC_RULE = FIXTURES / "synthetic_qfast_panel_selection_rule.json"
SYNTHETIC_MANIFEST = FIXTURES / "synthetic_qfast_panel_manifest.json"
SYNTHETIC_BUNDLE = FIXTURES / "synthetic_qfast_panel_bundle.json"


def _assemble_args(output: Path) -> list[str]:
    return [
        "assemble-panel",
        "--manifest",
        str(SYNTHETIC_MANIFEST),
        "--selection-rule",
        str(SYNTHETIC_RULE),
        "--bundle",
        str(SYNTHETIC_BUNDLE),
        "--output",
        str(output),
    ]


def test_assemble_panel_command_writes_a_deterministic_report(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    assert main(_assemble_args(first)) == 0
    assert main(_assemble_args(second)) == 0

    assert first.read_bytes() == second.read_bytes()
    report = json.loads(first.read_text(encoding="utf-8"))
    assert report["schema"] == "ringdown.qfast_panel_report"
    assert report["claims"] == ["NO_BROKER_EXECUTION", "NOT_ALPHA_EVIDENCE"]


def test_assemble_panel_rejection_is_fail_closed_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = tmp_path / "tampered-bundle.json"
    payload = json.loads(SYNTHETIC_BUNDLE.read_text(encoding="utf-8"))
    payload["events"][0], payload["events"][1] = payload["events"][1], payload["events"][0]
    tampered.write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "report.json"
    args = _assemble_args(output)
    args[args.index(str(SYNTHETIC_BUNDLE))] = str(tampered)

    assert main(args) == 2
    assert not output.exists()
    assert "IDENTITY_MISMATCH at" in capsys.readouterr().err
