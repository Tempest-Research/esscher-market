"""Installed-wheel regression for the issue-41 offline capture boundary."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "sourcedata" / "synthetic_snapshot_inputs_v1.json"
EXPECTED_ARTIFACTS = {
    "candidate_manifest.json",
    "capture_identity.json",
    "corporate_action_receipts.jsonl",
    "data_feasibility_manifest.json",
    "feature_receipt.json",
    "source_receipts.jsonl",
    "strategy_snapshot.json",
}


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_installed_wheel_runs_capture_with_explicit_fixture(tmp_path: Path) -> None:
    """A wheel must use the caller fixture, not an absent repository test path."""

    wheel_dir = tmp_path / "wheel"
    _run(["uv", "build", "--wheel", "--out-dir", str(wheel_dir)], cwd=REPO_ROOT)
    wheels = tuple(wheel_dir.glob("ringdown_market-*.whl"))
    assert len(wheels) == 1

    venv_dir = tmp_path / "venv"
    _run(
        ["uv", "venv", "--seed", "--python", sys.executable, str(venv_dir)],
        cwd=tmp_path,
    )
    python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert python.is_file()
    _run(["uv", "pip", "install", "--python", str(python), str(wheels[0])], cwd=tmp_path)

    output_dir = tmp_path / "capture-output"
    output_dir.mkdir()
    env = {**os.environ, "ESSCHER_CAPTURE_AUTHORIZED": "yes", "PYTHONPATH": ""}
    result = _run(
        [
            str(python),
            "-m",
            "ringdown_market.sourcedata.capture",
            "--event-id",
            "KR-2026Q2-EARNINGS",
            "--fixture",
            str(FIXTURE_PATH),
            "--capture-at",
            "2026-09-11T13:35:10Z",
            "--output-dir",
            str(output_dir),
            "--condition-satisfied",
            "HUMAN_VERIFIED_CAPTURE",
            "--condition-satisfied",
            "PER_RECORD_PRIMARY_PROVENANCE",
            "--condition-satisfied",
            "GATE_A_EQUITY_ENTITLEMENT_RECEIPT",
        ],
        cwd=tmp_path,
        env=env,
    )

    assert "captured KR-2026Q2-EARNINGS" in result.stdout
    assert {path.name for path in output_dir.iterdir()} == EXPECTED_ARTIFACTS
