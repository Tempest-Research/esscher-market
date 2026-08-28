from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_hygiene_module() -> ModuleType:
    script = Path(__file__).parents[1] / "scripts" / "check_repo_hygiene.py"
    spec = importlib.util.spec_from_file_location("repo_hygiene", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load repository hygiene checker")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HYGIENE = _load_hygiene_module()
REQUIRED_FILES = set(HYGIENE.REQUIRED_FILES)


@pytest.mark.parametrize(
    "candidate",
    [
        Path(".ENV"),
        Path("Credentials.json"),
        Path("TOKEN.JSON"),
        Path("agents.md"),
        Path("secret.PEM"),
    ],
)
def test_forbidden_paths_are_case_insensitive(candidate: Path) -> None:
    errors: list[str] = []
    tracked = REQUIRED_FILES | {candidate}

    HYGIENE._check_paths(tracked, tracked, errors)

    assert any(candidate.as_posix() in error for error in errors)


def test_generated_directory_names_are_case_insensitive() -> None:
    candidate = Path("Build/output.txt")
    errors: list[str] = []
    tracked = REQUIRED_FILES | {candidate}

    HYGIENE._check_paths(tracked, tracked, errors)

    assert f"generated directory must not be committed: {candidate.as_posix()}" in errors


def test_required_files_must_be_tracked_not_only_present() -> None:
    missing = Path("README.md")
    errors: list[str] = []

    HYGIENE._check_paths(REQUIRED_FILES - {missing}, REQUIRED_FILES, errors)

    assert f"missing required file: {missing.as_posix()}" in errors


@pytest.mark.parametrize(
    "path",
    [
        Path("src/ringdown_market/direct.py"),
        Path("scripts/direct.py"),
        Path("web/direct.ts"),
    ],
)
def test_direct_alpaca_hosts_fail_in_production_code(path: Path) -> None:
    errors: list[str] = []

    HYGIENE._check_text(
        path,
        "POST https://PAPER-API.ALPACA.MARKETS/v2/orders",
        errors,
    )

    direct_host_errors = [error for error in errors if "direct Alpaca host" in error]
    assert direct_host_errors == [
        f"{path.as_posix()}: direct Alpaca host 'paper-api.alpaca.markets' "
        "bypasses the adapter boundary"
    ]


@pytest.mark.parametrize(
    "path",
    [
        Path("docs/API_NOTES.md"),
        Path("tests/test_adapter.py"),
        HYGIENE.HYGIENE_CHECKER,
    ],
)
def test_policy_text_and_checker_may_name_forbidden_hosts(path: Path) -> None:
    errors: list[str] = []

    HYGIENE._check_text(path, "https://paper-api.alpaca.markets/v2/orders", errors)

    assert errors == []
