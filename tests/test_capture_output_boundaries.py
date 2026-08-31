"""Regression coverage for capture output-path containment."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ringdown_market.sourcedata import capture
from ringdown_market.sourcedata.reasons import CollectorRejected

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "sourcedata" / "synthetic_snapshot_inputs_v1.json"
CAPTURE_OUTPUT_NAMES = (
    "strategy_snapshot.json",
    "feature_receipt.json",
    "candidate_manifest.json",
    "data_feasibility_manifest.json",
    "source_receipts.jsonl",
    "corporate_action_receipts.jsonl",
    "capture_identity.json",
)


def _capture_args(output_dir: Path) -> list[str]:
    return [
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


def _symlink_or_skip(path: Path, target: Path, *, directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links are unavailable on this platform: {error}")


def _junction_or_skip(path: Path, target: Path) -> None:
    if os.name != "nt" or not hasattr(Path, "is_junction"):
        pytest.skip("junction detection is Windows-specific")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(path), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not path.is_junction():
        pytest.skip("junction creation is unavailable to this test process")


@pytest.mark.parametrize("output_name", CAPTURE_OUTPUT_NAMES)
def test_capture_rejects_every_destination_symlink_without_touching_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_name: str
) -> None:
    """Every actual output must reject a link before any capture bytes escape."""

    output_dir = tmp_path / "capture-output"
    output_dir.mkdir()
    sentinel = tmp_path / f"outside-{output_name}"
    sentinel.write_bytes(b"outside sentinel must remain unchanged")
    _symlink_or_skip(output_dir / output_name, sentinel)
    before = sentinel.read_bytes()

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert capture.main(_capture_args(output_dir)) == 2

    assert sentinel.read_bytes() == before
    assert not any(
        (output_dir / name).exists() and not (output_dir / name).is_symlink()
        for name in CAPTURE_OUTPUT_NAMES
    )


def test_capture_rejects_symlinked_output_directory_without_writing_through_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The requested output directory itself cannot be an indirection boundary."""

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    output_link = tmp_path / "capture-output"
    _symlink_or_skip(output_link, outside_dir, directory=True)

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert capture.main(_capture_args(output_link)) == 2

    assert tuple(outside_dir.iterdir()) == ()


def test_capture_rejects_symlinked_parent_component_without_writing_through_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-link leaf is still unsafe when an existing parent redirects it."""

    outside_parent = tmp_path / "outside-parent"
    outside_parent.mkdir()
    redirected_parent = tmp_path / "redirected-parent"
    _symlink_or_skip(redirected_parent, outside_parent, directory=True)
    output_dir = redirected_parent / "capture-output"
    output_dir.mkdir()

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert capture.main(_capture_args(output_dir)) == 2

    assert tuple(output_dir.iterdir()) == ()


def test_capture_rejects_reparse_points_when_platform_exposes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows junction/reparse point must be treated as an output indirection."""

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    output_dir = tmp_path / "capture-output"
    _junction_or_skip(output_dir, outside_dir)

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert capture.main(_capture_args(output_dir)) == 2
    assert tuple(outside_dir.iterdir()) == ()


def test_capture_rejects_junction_parent_component_when_platform_exposes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows parent junction must not redirect a non-link output leaf."""

    outside_parent = tmp_path / "outside-parent"
    outside_parent.mkdir()
    redirected_parent = tmp_path / "redirected-parent"
    _junction_or_skip(redirected_parent, outside_parent)
    output_dir = redirected_parent / "capture-output"
    output_dir.mkdir()

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert capture.main(_capture_args(output_dir)) == 2
    assert tuple((outside_parent / "capture-output").iterdir()) == ()


def test_capture_replaces_existing_regular_outputs_without_leaving_staging_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary preserves normal repeat capture behavior without following links."""

    output_dir = tmp_path / "capture-output"
    output_dir.mkdir()
    for output_name in CAPTURE_OUTPUT_NAMES:
        (output_dir / output_name).write_bytes(b"stale output")

    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert capture.main(_capture_args(output_dir)) == 0

    assert {path.name for path in output_dir.iterdir()} == set(CAPTURE_OUTPUT_NAMES)
    assert all(
        (output_dir / output_name).read_bytes() != b"stale output"
        for output_name in CAPTURE_OUTPUT_NAMES
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handles are required")
def test_windows_capture_pins_output_directory_against_junction_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-check junction swap cannot redirect the first published artifact."""

    output_dir = tmp_path / "capture-output"
    outside_dir = tmp_path / "outside"
    output_dir.mkdir()
    outside_dir.mkdir()
    original_replace = capture.os.replace
    swap_attempted = False
    swap_blocked = False

    def replace_after_swap(
        source: os.PathLike[str] | str, destination: os.PathLike[str] | str
    ) -> None:
        nonlocal swap_attempted, swap_blocked
        if not swap_attempted:
            swap_attempted = True
            try:
                output_dir.rmdir()
            except PermissionError as error:
                assert error.winerror == 32
                swap_blocked = True
            else:
                _junction_or_skip(output_dir, outside_dir)
        original_replace(source, destination)

    monkeypatch.setattr(capture.os, "replace", replace_after_swap)
    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")

    assert capture.main(_capture_args(output_dir)) == 0
    assert swap_attempted
    assert swap_blocked
    assert tuple(outside_dir.iterdir()) == ()
    assert {path.name for path in output_dir.iterdir()} == set(CAPTURE_OUTPUT_NAMES)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handles are required")
def test_windows_capture_pins_existing_parent_against_junction_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinned output handle also prevents its existing parent from being moved."""

    output_parent = tmp_path / "capture-parent"
    output_dir = output_parent / "capture-output"
    outside_parent = tmp_path / "outside-parent"
    moved_parent = tmp_path / "moved-parent"
    output_dir.mkdir(parents=True)
    outside_parent.mkdir()
    original_replace = capture.os.replace
    swap_attempted = False
    swap_blocked = False

    def replace_after_parent_swap(
        source: os.PathLike[str] | str, destination: os.PathLike[str] | str
    ) -> None:
        nonlocal swap_attempted, swap_blocked
        if not swap_attempted:
            swap_attempted = True
            try:
                output_parent.rename(moved_parent)
            except PermissionError as error:
                assert error.winerror == 5
                swap_blocked = True
            else:
                _junction_or_skip(output_parent, outside_parent)
        original_replace(source, destination)

    monkeypatch.setattr(capture.os, "replace", replace_after_parent_swap)
    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")

    assert capture.main(_capture_args(output_dir)) == 0
    assert swap_attempted
    assert swap_blocked
    assert tuple(outside_parent.iterdir()) == ()
    assert {path.name for path in output_dir.iterdir()} == set(CAPTURE_OUTPUT_NAMES)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handles are required")
def test_windows_pin_rejects_parent_junction_resolved_during_acquisition(tmp_path: Path) -> None:
    """The native pin detects a parent redirect that races after lexical validation."""

    outside_parent = tmp_path / "outside-parent"
    outside_output = outside_parent / "capture-output"
    outside_output.mkdir(parents=True)
    redirected_parent = tmp_path / "redirected-parent"
    _junction_or_skip(redirected_parent, outside_parent)

    with (
        pytest.raises(CollectorRejected, match="resolved to a different directory"),
        capture._pin_windows_output_directory(redirected_parent / "capture-output"),
    ):
        pass


def test_capture_rejects_destination_replaced_with_link_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bounded leaf replacement race must fail before bytes reach the sentinel."""

    output_dir = tmp_path / "capture-output"
    output_dir.mkdir()
    destination = output_dir / "strategy_snapshot.json"
    destination.write_bytes(b"ordinary pre-existing output")
    sentinel = tmp_path / "outside-sentinel"
    sentinel.write_bytes(b"outside sentinel must remain unchanged")
    original_lstat = capture._lstat
    destination_checks = 0

    def replace_after_initial_check(path: Path) -> os.stat_result:
        nonlocal destination_checks
        status = original_lstat(path)
        if path == destination:
            destination_checks += 1
            if destination_checks == 2:
                destination.unlink()
                try:
                    destination.symlink_to(sentinel)
                except (NotImplementedError, OSError) as error:
                    pytest.skip(f"symbolic links are unavailable on this platform: {error}")
                return original_lstat(path)
        return status

    monkeypatch.setattr(capture, "_lstat", replace_after_initial_check)
    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert capture.main(_capture_args(output_dir)) == 2

    assert destination_checks == 2
    assert sentinel.read_bytes() == b"outside sentinel must remain unchanged"


def test_capture_rejects_output_directory_replaced_with_link_before_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bounded parent replacement race must be caught before publication."""

    output_dir = tmp_path / "capture-output"
    output_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    original_lstat = capture._lstat
    output_directory_checks = 0

    def replace_after_initial_check(path: Path) -> os.stat_result:
        nonlocal output_directory_checks
        status = original_lstat(path)
        if path == output_dir:
            output_directory_checks += 1
            if output_directory_checks == 2:
                output_dir.rmdir()
                try:
                    output_dir.symlink_to(outside_dir, target_is_directory=True)
                except (NotImplementedError, OSError) as error:
                    pytest.skip(f"symbolic links are unavailable on this platform: {error}")
                return original_lstat(path)
        return status

    monkeypatch.setattr(capture, "_lstat", replace_after_initial_check)
    monkeypatch.setenv("ESSCHER_CAPTURE_AUTHORIZED", "yes")
    assert capture.main(_capture_args(output_dir)) == 2

    assert output_directory_checks == 2
    assert tuple(outside_dir.iterdir()) == ()
