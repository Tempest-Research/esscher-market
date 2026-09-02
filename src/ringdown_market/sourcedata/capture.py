"""Inert capture command for the strategy snapshot collector.

The command never contains credentials and never starts a network, broker,
or MCP session. It runs only with explicit host authorization and replays the
frozen synthetic adapters in this slice; the live read-only boundary is not
pinned and fails closed until a separate recorded gate pins the exact Alpaca
MCP server version and tool schemas.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Sequence
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path

from ringdown_market.contracts.source_matrix import CONDITIONS, source_matrix_bytes
from ringdown_market.sourcedata.compiler import (
    EARNINGS_CANDIDATE,
    MACRO_CANDIDATE,
    CaptureConfiguration,
    CompiledSnapshot,
    compile_macro_snapshot,
    compile_strategy_snapshot,
    compiled_strategy_input,
)
from ringdown_market.sourcedata.fakes import (
    FixtureEvidenceSource,
    FixtureMacroEvidenceSource,
    FixtureMacroMarketDataSource,
    FixtureMacroReleaseSource,
    FixtureMarketDataSource,
    build_candidate_manifest,
    build_macro_candidate_manifest,
    load_fixture,
    load_macro_fixture,
)
from ringdown_market.sourcedata.feasibility import feasibility_manifest_bytes
from ringdown_market.sourcedata.lineage_gate import evaluate_lineage, lineage_receipt_bytes
from ringdown_market.sourcedata.reasons import CollectorReason, CollectorRejected
from ringdown_market.sourcedata.receipts import (
    corporate_action_receipt_bytes,
    source_receipt_bytes,
)
from ringdown_market.sourcedata.rights_gate import evaluate_capture_rights

HOST_AUTHORIZATION_VARIABLE = "ESSCHER_CAPTURE_AUTHORIZED"
HOST_AUTHORIZATION_VALUE = "yes"
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")
_CAPTURE_OUTPUT_NAMES = (
    "strategy_snapshot.json",
    "feature_receipt.json",
    "candidate_manifest.json",
    "data_feasibility_manifest.json",
    "source_receipts.jsonl",
    "corporate_action_receipts.jsonl",
    "lineage_receipts.jsonl",
    "capture_identity.json",
)

_WINDOWS_FILE_READ_ATTRIBUTES = 0x80
_WINDOWS_DELETE = 0x10000
_WINDOWS_FILE_SHARE_READ = 0x1
_WINDOWS_FILE_SHARE_WRITE = 0x2
_WINDOWS_CREATE_NEW = 1
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_DELETE_ON_CLOSE = 0x04000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_ATTRIBUTE_NORMAL = 0x80
_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x10
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WINDOWS_FILE_BASIC_INFO = 0


class _WindowsFileBasicInfo(ctypes.Structure):
    """Subset of FILE_BASIC_INFO returned by GetFileInformationByHandleEx."""

    _fields_ = [
        ("creation_time", ctypes.c_longlong),
        ("last_access_time", ctypes.c_longlong),
        ("last_write_time", ctypes.c_longlong),
        ("change_time", ctypes.c_longlong),
        ("file_attributes", wintypes.DWORD),
    ]


def _capture_timestamp(value: str) -> datetime:
    """Parse an explicit zero-offset UTC capture clock without host-local coercion."""

    if _UTC_TIMESTAMP.fullmatch(value) is None:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "capture_at",
            "capture time must use an explicit UTC Z or +00:00 offset",
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CollectorRejected(
            CollectorReason.UNSUPPORTED_INPUT,
            "capture_at",
            "capture time must be an ISO-8601 UTC timestamp",
        ) from error
    return parsed.astimezone(UTC)


def _configuration(
    args: argparse.Namespace, fixture, manifest_builder, *, lineage_receipt_sha256: str
) -> CaptureConfiguration:
    capture_at = _capture_timestamp(args.capture_at)
    return CaptureConfiguration(
        candidate_manifest_bytes=manifest_builder(fixture),
        event_id=args.event_id,
        capture_at=capture_at,
        market_publisher=str(fixture["market_publisher"]),
        market_entitlement=str(fixture["market_entitlement"]),
        market_redistribution=str(fixture["market_redistribution"]),
        lineage_receipt_sha256=lineage_receipt_sha256,
    )


def run_capture(configuration: CaptureConfiguration, candidate: str, fixture) -> CompiledSnapshot:
    """Run one offline capture over the frozen synthetic adapters."""

    if candidate == MACRO_CANDIDATE:
        evidence = FixtureMacroEvidenceSource(fixture)
        macro = FixtureMacroReleaseSource(fixture)
        market = FixtureMacroMarketDataSource(fixture)
        return compile_macro_snapshot(configuration, evidence.sessions, macro, market)
    evidence = FixtureEvidenceSource(fixture)
    market = FixtureMarketDataSource(fixture)
    return compile_strategy_snapshot(configuration, evidence, market)


def _unsafe_output_path(detail: str) -> CollectorRejected:
    """Return the stable rejection used for every capture-output boundary."""

    return CollectorRejected(CollectorReason.UNSAFE_OUTPUT_PATH, "output_dir", detail)


def _lstat(path: Path) -> os.stat_result:
    """Inspect a path itself, never the object a link would resolve to."""

    return os.lstat(path)


def _is_link_or_reparse_point(status: os.stat_result) -> bool:
    """Treat POSIX links and Windows reparse points as output indirections."""

    return stat.S_ISLNK(status.st_mode) or bool(
        getattr(status, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _absolute_output_directory(output_dir: Path) -> Path:
    """Normalize only lexical path components so links remain visible to lstat."""

    if ".." in output_dir.parts:
        raise _unsafe_output_path("output directory must not contain a parent traversal")
    return Path(os.path.abspath(output_dir))


def _validate_output_directory(output_dir: Path, *, strict_leaf: bool = True) -> Path:
    """Require a real, pre-existing directory with no redirecting destination.

    Ancestor components that are directory links or reparse points are followed
    and admitted only when they resolve to a directory; links resolving to
    non-directories remain rejected.  The destination leaf itself keeps the
    strict no-follow check so the published output directory can never be an
    indirection boundary.
    """

    absolute = _absolute_output_directory(output_dir)
    component = Path(absolute.anchor)
    parts = absolute.parts[1:]
    last_index = len(parts)
    for index, part in enumerate(parts, start=1):
        component = component / part
        try:
            status = _lstat(component)
        except OSError as error:
            raise _unsafe_output_path(f"cannot inspect output component '{component}'") from error
        if _is_link_or_reparse_point(status):
            if strict_leaf and index == last_index:
                raise _unsafe_output_path(
                    f"output component '{component}' is a link or reparse point"
                )
            try:
                followed = os.stat(component)
            except OSError as error:
                raise _unsafe_output_path(
                    f"output component '{component}' does not resolve to an existing target"
                ) from error
            if not stat.S_ISDIR(followed.st_mode):
                raise _unsafe_output_path(
                    f"output component '{component}' is a link to a non-directory"
                )
            continue
        if not stat.S_ISDIR(status.st_mode):
            raise _unsafe_output_path(f"output component '{component}' is not a directory")
    return absolute


def _windows_kernel32():
    """Bind the small Win32 surface needed to hold an output directory in place."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _normalize_windows_path(value: str) -> str:
    """Compare DOS and extended-length paths without permitting a target redirect."""

    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _windows_final_path(kernel32, handle: int) -> str:
    """Read the kernel-resolved location of an already-open directory handle."""

    length = kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not length:
        raise OSError(ctypes.get_last_error(), "cannot inspect pinned output directory")
    buffer = ctypes.create_unicode_buffer(length + 1)
    if not kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
        raise OSError(ctypes.get_last_error(), "cannot read pinned output directory path")
    return buffer.value


def _open_windows_output_directory(kernel32, output_dir: Path) -> int:
    """Open a real output directory and deny replacement until the handle closes."""

    handle = kernel32.CreateFileW(
        str(output_dir),
        _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_DELETE,
        _WINDOWS_FILE_SHARE_READ | _WINDOWS_FILE_SHARE_WRITE,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "cannot pin output directory")

    opened = False
    try:
        information = _WindowsFileBasicInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            _WINDOWS_FILE_BASIC_INFO,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise OSError(ctypes.get_last_error(), "cannot inspect pinned output directory")
        if not information.file_attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
            raise _unsafe_output_path("pinned output path is not a directory")
        if information.file_attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
            raise _unsafe_output_path("pinned output path is a link or reparse point")
        actual_path = _normalize_windows_path(_windows_final_path(kernel32, handle))
        expected_path = _normalize_windows_path(os.path.realpath(str(output_dir)))
        if actual_path != expected_path:
            raise _unsafe_output_path("output path resolved to a different directory while pinning")
        opened = True
        return handle
    finally:
        if not opened:
            kernel32.CloseHandle(handle)


def _open_windows_pin_file(kernel32, output_dir: Path) -> int:
    """Create an unreplaceable empty child in the resolved output directory."""

    resolved_output_dir = Path(os.path.realpath(str(output_dir)))
    path = resolved_output_dir / f".esscher-capture-{uuid.uuid4().hex}.pin"
    handle = kernel32.CreateFileW(
        str(path),
        _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_DELETE,
        _WINDOWS_FILE_SHARE_READ | _WINDOWS_FILE_SHARE_WRITE,
        None,
        _WINDOWS_CREATE_NEW,
        _WINDOWS_FILE_ATTRIBUTE_NORMAL
        | _WINDOWS_FILE_FLAG_DELETE_ON_CLOSE
        | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "cannot create output-directory pin")

    opened = False
    try:
        information = _WindowsFileBasicInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle,
            _WINDOWS_FILE_BASIC_INFO,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise OSError(ctypes.get_last_error(), "cannot inspect output-directory pin")
        if information.file_attributes & (
            _WINDOWS_FILE_ATTRIBUTE_DIRECTORY | _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise _unsafe_output_path("output-directory pin is not a regular file")
        actual_path = _normalize_windows_path(_windows_final_path(kernel32, handle))
        expected_path = _normalize_windows_path(os.path.realpath(str(path)))
        if actual_path != expected_path:
            raise _unsafe_output_path("output-directory pin resolved outside the validated path")
        opened = True
        return handle
    finally:
        if not opened:
            kernel32.CloseHandle(handle)


@contextlib.contextmanager
def _pin_windows_output_directory(output_dir: Path):
    """Keep the output directory and ancestor tree fixed while publishing.

    A DELETE handle closes the acquisition race but conflicts with child renames.
    While it is held, create a delete-on-close child in the resolved directory;
    that child keeps the directory and its ancestors in place during publication.
    """

    kernel32 = _windows_kernel32()
    directory_handle = _open_windows_output_directory(kernel32, output_dir)
    pin_handle: int | None = None
    try:
        pin_handle = _open_windows_pin_file(kernel32, output_dir)
        kernel32.CloseHandle(directory_handle)
        directory_handle = None
        yield
    finally:
        if directory_handle is not None:
            kernel32.CloseHandle(directory_handle)
        if pin_handle is not None:
            kernel32.CloseHandle(pin_handle)


def _validate_output_destinations(
    output_dir: Path, names: Sequence[str], *, require_existing: bool = False
) -> None:
    """Reject non-regular existing artifact paths before they can be replaced."""

    for name in names:
        destination = output_dir / name
        try:
            status = _lstat(destination)
        except FileNotFoundError:
            if require_existing:
                raise _unsafe_output_path(
                    f"published output '{name}' disappeared during capture"
                ) from None
            continue
        except OSError as error:
            raise _unsafe_output_path(f"cannot inspect capture output '{name}'") from error
        if _is_link_or_reparse_point(status):
            raise _unsafe_output_path(f"capture output '{name}' is a link or reparse point")
        if not stat.S_ISREG(status.st_mode):
            raise _unsafe_output_path(f"capture output '{name}' is not a regular file")


def _supports_secure_directory_fds() -> bool:
    """Return whether the platform can publish files through a no-follow dirfd."""

    return (
        os.name != "nt"
        and os.open in os.supports_dir_fd
        # os.replace accepts dirfds on POSIX but is not listed in this capability set.
        and os.rename in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and bool(getattr(os, "O_DIRECTORY", 0))
        and bool(getattr(os, "O_NOFOLLOW", 0))
    )


def _open_secure_output_directory(output_dir: Path) -> int:
    """Open POSIX components, following only directory links before the leaf.

    Ancestor components may be directory symlinks: an ``O_DIRECTORY`` open
    admits them only when they resolve to a directory.  The output directory
    itself keeps ``O_NOFOLLOW`` so the destination leaf can never be a link.
    """

    follow_flags = os.O_RDONLY | os.O_DIRECTORY
    nofollow_flags = follow_flags | os.O_NOFOLLOW
    descriptor = os.open(output_dir.anchor, nofollow_flags)
    try:
        parts = output_dir.parts[1:]
        for index, part in enumerate(parts):
            flags = nofollow_flags if index == len(parts) - 1 else follow_flags
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _validate_destination_at(
    directory_fd: int, name: str, *, require_existing: bool = False
) -> None:
    """Validate one destination through an already no-follow directory handle."""

    try:
        status = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if require_existing:
            raise _unsafe_output_path(
                f"published output '{name}' disappeared during capture"
            ) from None
        return
    except OSError as error:
        raise _unsafe_output_path(f"cannot inspect capture output '{name}'") from error
    if _is_link_or_reparse_point(status):
        raise _unsafe_output_path(f"capture output '{name}' is a link or reparse point")
    if not stat.S_ISREG(status.st_mode):
        raise _unsafe_output_path(f"capture output '{name}' is not a regular file")


def _write_file_descriptor(descriptor: int, contents: bytes) -> None:
    """Write already-open staging output without resolving its path again."""

    with os.fdopen(descriptor, "wb") as output:
        output.write(contents)
        output.flush()


def _write_outputs_with_directory_fd(
    directory_fd: int, outputs: Sequence[tuple[str, bytes]]
) -> None:
    """Stage and atomically publish outputs through a pinned POSIX directory."""

    staged: list[str] = []
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        for name, contents in outputs:
            _validate_destination_at(directory_fd, name)
            temporary_name = f".{name}.{uuid.uuid4().hex}.tmp"
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
            staged.append(temporary_name)
            _write_file_descriptor(descriptor, contents)
        for temporary_name, (name, _) in zip(staged, outputs, strict=True):
            _validate_destination_at(directory_fd, name)
            os.replace(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            _validate_destination_at(directory_fd, name, require_existing=True)
    except OSError as error:
        raise _unsafe_output_path("unable to stage or publish capture output safely") from error
    finally:
        for temporary_name in staged:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=directory_fd)


def _write_temporary_output(path: Path, contents: bytes) -> None:
    """Create a fresh staging file without following an existing leaf link."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    _write_file_descriptor(descriptor, contents)


def _write_outputs_with_path_checks(output_dir: Path, outputs: Sequence[tuple[str, bytes]]) -> None:
    """Use checked staging where directory file descriptors are unavailable."""

    staging_directory = _validate_output_directory(output_dir.parent, strict_leaf=False)
    staged: list[Path] = []
    try:
        for name, contents in outputs:
            temporary_path = staging_directory / f".{output_dir.name}.{name}.{uuid.uuid4().hex}.tmp"
            staged.append(temporary_path)
            _write_temporary_output(temporary_path, contents)
        for temporary_path, (name, _) in zip(staged, outputs, strict=True):
            _validate_output_directory(output_dir)
            _validate_output_destinations(output_dir, (name,))
            os.replace(temporary_path, output_dir / name)
            _validate_output_directory(output_dir)
            _validate_output_destinations(output_dir, (name,), require_existing=True)
    except OSError as error:
        raise _unsafe_output_path("unable to stage or publish capture output safely") from error
    finally:
        for temporary_path in staged:
            with contextlib.suppress(FileNotFoundError):
                temporary_path.unlink()


def _write_capture_outputs(output_dir: Path, outputs: Sequence[tuple[str, bytes]]) -> None:
    """Write every canonical artifact without crossing an output indirection."""

    names = tuple(name for name, _ in outputs)
    if names != _CAPTURE_OUTPUT_NAMES:
        raise _unsafe_output_path("capture output set does not match the frozen canonical names")
    safe_output_dir = _validate_output_directory(output_dir)
    _validate_output_destinations(safe_output_dir, names)
    if _supports_secure_directory_fds():
        safe_output_dir = _validate_output_directory(safe_output_dir)
        _validate_output_destinations(safe_output_dir, names)
        try:
            directory_fd = _open_secure_output_directory(safe_output_dir)
        except OSError as error:
            raise _unsafe_output_path(
                "cannot pin output directory without following links"
            ) from error
        try:
            _write_outputs_with_directory_fd(directory_fd, outputs)
        finally:
            os.close(directory_fd)
        return
    if os.name == "nt":
        try:
            with _pin_windows_output_directory(safe_output_dir):
                _write_outputs_with_path_checks(safe_output_dir, outputs)
        except OSError as error:
            raise _unsafe_output_path(
                "cannot pin output directory against Windows replacement"
            ) from error
        return
    _write_outputs_with_path_checks(safe_output_dir, outputs)


def _build_feasibility(candidate: str, fixture, compiled, capture_at):
    """Build the candidate-specific Gate B feasibility manifest."""

    from ringdown_market.sourcedata.fakes import load_feasibility_declarations
    from ringdown_market.sourcedata.feasibility import build_feasibility_for_candidate
    from ringdown_market.strategy.policy import load_strategy_policy

    fallback = MACRO_CANDIDATE if candidate == EARNINGS_CANDIDATE else None
    return build_feasibility_for_candidate(
        policy=load_strategy_policy(),
        candidate_id=candidate,
        declarations=load_feasibility_declarations(fixture),
        source_receipts=compiled.source_receipts,
        evaluated_at=capture_at,
        producer_build_sha256=compiled.snapshot.producer_build_sha256,
        fallback_candidate_id=fallback,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ringdown_market.sourcedata.capture",
        description="Compile one deterministic point-in-time strategy snapshot offline.",
    )
    parser.add_argument("--event-id", required=True, help="frozen candidate event ID")
    parser.add_argument(
        "--capture-at",
        required=True,
        help="explicit host retrieval clock (UTC ISO-8601)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="existing directory receiving the canonical artifacts",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        required=True,
        help="explicit frozen development fixture path (never loaded from the package)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="request the live read-only boundary (not pinned in this slice)",
    )

    parser.add_argument(
        "--condition-satisfied",
        dest="conditions_satisfied",
        action="append",
        default=[],
        metavar="CONDITION",
        help="declare one frozen source-matrix condition as satisfied for this capture",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; fails closed without explicit host authorization."""

    args = _build_parser().parse_args(argv)
    if os.environ.get(HOST_AUTHORIZATION_VARIABLE) != HOST_AUTHORIZATION_VALUE:
        print(
            str(
                CollectorRejected(
                    CollectorReason.HOST_CONFIGURATION_MISSING,
                    HOST_AUTHORIZATION_VARIABLE,
                    "capture requires explicit host authorization"
                    f" ({HOST_AUTHORIZATION_VARIABLE}={HOST_AUTHORIZATION_VALUE})",
                )
            ),
            file=sys.stderr,
        )
        return 2
    if args.live:
        print(
            str(
                CollectorRejected(
                    CollectorReason.LIVE_BOUNDARY_NOT_PINNED,
                    "live",
                    "the official Alpaca MCP read-only server version and tool"
                    " schemas must be pinned before any live capture",
                )
            ),
            file=sys.stderr,
        )
        return 2
    satisfied: set[str] = set()
    for condition in args.conditions_satisfied:
        if condition not in CONDITIONS:
            print(
                str(
                    CollectorRejected(
                        CollectorReason.SOURCE_RIGHTS_LIMITATION_UNMET,
                        "condition_satisfied",
                        f"unknown source-matrix condition '{condition}'",
                    )
                ),
                file=sys.stderr,
            )
            return 2
        satisfied.add(condition)
    candidate = MACRO_CANDIDATE if args.event_id.startswith("BLS-") else EARNINGS_CANDIDATE
    matrix_bytes = source_matrix_bytes()
    try:
        rights_report = evaluate_capture_rights(
            candidate_id=candidate,
            matrix_bytes=matrix_bytes,
            satisfied_conditions=frozenset(satisfied),
        )
        lineage_report = evaluate_lineage(
            event_id=args.event_id,
            matrix_bytes=matrix_bytes,
        )
    except CollectorRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    lineage_receipt_bytes_value = lineage_receipt_bytes(lineage_report.resolution)
    lineage_receipt_sha256 = hashlib.sha256(lineage_receipt_bytes_value).hexdigest()
    if candidate == MACRO_CANDIDATE:
        fixture = load_macro_fixture(args.fixture)
        manifest_builder = build_macro_candidate_manifest
    else:
        fixture = load_fixture(args.fixture)
        manifest_builder = build_candidate_manifest
    try:
        configuration = _configuration(
            args,
            fixture,
            manifest_builder,
            lineage_receipt_sha256=lineage_receipt_sha256,
        )
        compiled = run_capture(configuration, candidate, fixture)
        joined = compiled_strategy_input(compiled)
        feasibility_manifest = _build_feasibility(
            candidate, fixture, compiled, configuration.capture_at
        )
    except CollectorRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    joined_identity = {
        "snapshot_sha256": joined.snapshot_sha256,
        "feature_receipt_sha256": joined.feature_receipt_sha256,
        "candidate_manifest_sha256": joined.candidate_manifest_sha256,
        "source_matrix_sha256": rights_report.source_matrix_sha256,
        "security_lineage_sha256": lineage_report.security_lineage_sha256,
    }
    receipts = b"".join(
        source_receipt_bytes(receipt) + b"\n" for receipt in compiled.source_receipts
    )
    action_receipts = b"".join(
        corporate_action_receipt_bytes(receipt) + b"\n" for receipt in compiled.action_receipts
    )
    lineage_receipt = lineage_receipt_bytes_value + b"\n"
    outputs = (
        ("strategy_snapshot.json", compiled.strategy_snapshot_bytes),
        ("feature_receipt.json", compiled.feature_receipt_bytes),
        ("candidate_manifest.json", compiled.candidate_manifest_bytes),
        ("data_feasibility_manifest.json", feasibility_manifest_bytes(feasibility_manifest)),
        ("source_receipts.jsonl", receipts),
        ("corporate_action_receipts.jsonl", action_receipts),
        ("lineage_receipts.jsonl", lineage_receipt),
        (
            "capture_identity.json",
            json.dumps(joined_identity, sort_keys=True, indent=1).encode("utf-8") + b"\n",
        ),
    )
    try:
        _write_capture_outputs(args.output_dir, outputs)
    except CollectorRejected as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"captured {joined.snapshot.event_id}: snapshot_sha256={joined.snapshot_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
