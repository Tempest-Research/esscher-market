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
    "stale_name",
    [
        "Ring" + "down",
        "RING" + "DOWN",
        "rInG" + "dOwN",
        "Ring</em>" + "down",
    ],
)
def test_stale_public_branding_fails_outside_the_exact_allowlist(stale_name: str) -> None:
    errors: list[str] = []
    legacy_name = HYGIENE.LEGACY_PUBLIC_BRAND

    HYGIENE._check_legacy_brand(Path("docs/stale-name.md"), f"# {stale_name}\n", errors)

    assert errors == [
        f"docs/stale-name.md:1: stale public brand '{legacy_name}'; use Esscher or add an exact "
        "compatibility/migration allowance"
    ]


def test_lowercase_ringdown_casing_is_reserved_for_machine_identifiers() -> None:
    errors: list[str] = []

    HYGIENE._check_legacy_brand(
        Path("docs/technical-identifiers.md"),
        "Use the `ringdown`, `ringdown-market`, and `ringdown_market` interfaces.\n",
        errors,
    )

    assert errors == []


def test_exact_legacy_brand_compatibility_line_is_allowed() -> None:
    path = Path("src/ringdown_market/cli.py")
    allowed_line = next(iter(HYGIENE.LEGACY_BRAND_ALLOWLIST[path]))
    errors: list[str] = []

    HYGIENE._check_legacy_brand(path, f"{allowed_line}\n", errors)

    assert errors == []


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
