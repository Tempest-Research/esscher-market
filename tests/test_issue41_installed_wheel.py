"""Installed-wheel regression for the issue-41 offline capture boundary."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tomllib
import venv
import zipfile
from importlib.metadata import distribution
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


def _write_wheel_member(archive: zipfile.ZipFile, name: str, contents: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, contents)


def _build_local_wheel(wheel_dir: Path) -> tuple[Path, str]:
    """Assemble a deterministic wheel from the configured pure-Python package."""

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = config["project"]
    packages = config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == ["src/ringdown_market"]

    name = project["name"]
    version = project["version"]
    requires_python = project["requires-python"]
    dependencies = project["dependencies"]
    scripts = project["scripts"]
    metadata = "\n".join(
        (
            "Metadata-Version: 2.3",
            f"Name: {name}",
            f"Version: {version}",
            f"Requires-Python: {requires_python}",
            *(f"Requires-Dist: {dependency}" for dependency in dependencies),
            "License-File: LICENSE",
            "",
        )
    )
    wheel_metadata = "\n".join(
        (
            "Wheel-Version: 1.0",
            "Generator: test_issue41_installed_wheel",
            "Root-Is-Purelib: true",
            "Tag: py3-none-any",
            "",
        )
    )
    entry_points = "\n".join(
        (
            "[console_scripts]",
            *(f"{name} = {target}" for name, target in sorted(scripts.items())),
            "",
        )
    )

    normalized_name = name.replace("-", "_")
    dist_info = f"{normalized_name}-{version}.dist-info"
    wheel_path = wheel_dir / f"{normalized_name}-{version}-py3-none-any.whl"
    source_root = REPO_ROOT / "src"
    package_root = source_root / "ringdown_market"
    members = [
        (path.relative_to(source_root).as_posix(), path.read_bytes())
        for path in sorted(package_root.rglob("*"))
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}
    ]
    members.extend(
        (
            (f"{dist_info}/METADATA", metadata.encode()),
            (f"{dist_info}/WHEEL", wheel_metadata.encode()),
            (f"{dist_info}/entry_points.txt", entry_points.encode()),
            (f"{dist_info}/licenses/LICENSE", (REPO_ROOT / "LICENSE").read_bytes()),
        )
    )

    records = [
        f"{name},sha256={base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).rstrip(b'=').decode()},{len(contents)}"
        for name, contents in members
    ]
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for name, contents in members:
            _write_wheel_member(archive, name, contents)
        record = ("\n".join(records) + f"\n{dist_info}/RECORD,,\n").encode()
        _write_wheel_member(archive, f"{dist_info}/RECORD", record)
    return wheel_path, version


def _venv_executable(venv_dir: Path, name: str) -> Path:
    directory = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv_dir / directory / f"{name}{suffix}"


def _venv_python(venv_dir: Path) -> Path:
    return _venv_executable(venv_dir, "python")


def _stage_tzdata(python: Path, *, cwd: Path) -> Path:
    """Keep the wheel environment isolated while providing its declared timezone data."""

    source = Path(distribution("tzdata").locate_file("tzdata"))
    target_site = Path(
        _run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
            cwd=cwd,
        ).stdout.strip()
    )
    shutil.copytree(source, target_site / "tzdata")
    return target_site


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
    wheel_dir.mkdir()
    wheel, version = _build_local_wheel(wheel_dir)
    assert wheel.is_file()
    repeat_wheel_dir = tmp_path / "repeat-wheel"
    repeat_wheel_dir.mkdir()
    repeat_wheel, repeat_version = _build_local_wheel(repeat_wheel_dir)
    assert version == repeat_version
    assert (
        hashlib.sha256(wheel.read_bytes()).digest()
        == hashlib.sha256(repeat_wheel.read_bytes()).digest()
    )

    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    python = _venv_python(venv_dir)
    assert python.is_file()
    env = {
        **os.environ,
        "ESSCHER_CAPTURE_AUTHORIZED": "yes",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_FIND_LINKS": "",
        "PIP_NO_INDEX": "1",
        "PYTHONPATH": "",
    }
    target_site = _stage_tzdata(python, cwd=tmp_path)
    _run(
        [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
        cwd=tmp_path,
        env=env,
    )
    installed_path = Path(
        _run(
            [
                str(python),
                "-c",
                (
                    "import pathlib, ringdown_market; "
                    "print(pathlib.Path(ringdown_market.__file__).resolve())"
                ),
            ],
            cwd=tmp_path,
            env=env,
        ).stdout.strip()
    )
    assert installed_path.is_relative_to(target_site)
    cli = _venv_executable(venv_dir, "ringdown")
    assert cli.is_file()
    assert (
        _run([str(cli), "--version"], cwd=tmp_path, env=env).stdout.strip() == f"ringdown {version}"
    )

    output_dir = tmp_path / "capture-output"
    output_dir.mkdir()
    capture_command = [
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
    ]
    first_capture = _run(capture_command, cwd=tmp_path, env=env)

    assert "captured KR-2026Q2-EARNINGS" in first_capture.stdout
    assert {path.name for path in output_dir.iterdir()} == EXPECTED_ARTIFACTS
    first_artifacts = {path.name: path.read_bytes() for path in output_dir.iterdir()}
    second_capture = _run(capture_command, cwd=tmp_path, env=env)
    assert "captured KR-2026Q2-EARNINGS" in second_capture.stdout
    assert {path.name: path.read_bytes() for path in output_dir.iterdir()} == first_artifacts
