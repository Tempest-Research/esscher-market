"""Fail CI when tracked repository content crosses Esscher's safety boundaries."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = {
    Path("README.md"),
    Path("CONTRIBUTING.md"),
    Path("LICENSE"),
    Path("docs/ARCHITECTURE.md"),
    Path("docs/SOURCE_AND_CLAIM_POLICY.md"),
    Path("docs/TEAM_ONBOARDING.md"),
}

FORBIDDEN_NAMES = {
    ".env",
    ".cursorrules",
    "agents.md",
    "claude.md",
    "credentials.json",
    "service-account.json",
    "token.json",
}
FORBIDDEN_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
FORBIDDEN_PARTS = {
    ".hermes",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
TEXT_SUFFIXES = {
    "",
    ".css",
    ".html",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
DIRECT_ALPACA_HOSTS = {
    "api.alpaca.markets",
    "data.alpaca.markets",
    "paper-api.alpaca.markets",
}
BROKER_CODE_ROOTS = {"src", "scripts", "web"}
HYGIENE_CHECKER = Path("scripts/check_repo_hygiene.py")
SECRET_PATTERNS = {
    "GitHub token": re.compile(r"\bgh[opurs]_[A-Za-z0-9]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:EC |OPENSSH |RSA )?PRIVATE KEY-----"),
    "provider secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    # Issue #91 host-credential guards: Alpaca key identifiers and any
    # committed credential-env assignment with a value (keys belong only in
    # host environment files outside the repository).
    "Alpaca API key id": re.compile(r"\b(?:AK|PK)[A-Z0-9]{15,}\b"),
    "credential env assignment": re.compile(
        r"(?:APCA_API_SECRET_KEY|APCA_API_KEY_ID|MINIMAX_API_KEY|KIMI_API_KEY)"
        r"[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9]{8,}"
    ),
}
SYNTHETIC_LIMITATIONS = {
    "NO_BROKER_EXECUTION",
    "NOT_ALPHA_EVIDENCE",
    "NOT_HISTORICAL_DATA",
}
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
LEGACY_PUBLIC_BRAND = "Ring" + "down"
LEGACY_PUBLIC_BRAND_PATTERN = re.compile(r"\bringdown\b", flags=re.IGNORECASE)
INLINE_HTML_TAG = re.compile(r"</?[A-Za-z][^>\n]*>")
LEGACY_BRAND_ALLOWLIST: dict[Path, frozenset[str]] = {
    Path("README.md"): frozenset(
        {
            f'- report display: keep legacy `project: "{LEGACY_PUBLIC_BRAND}"` for '
            'compatibility and use additive `product_name: "Esscher"` for new displays;'
        }
    ),
    Path("CHANGELOG.md"): frozenset(
        {
            f'- Retained the legacy report `project: "{LEGACY_PUBLIC_BRAND}"` value and added '
            '`product_name: "Esscher"` as an additive display alias.'
        }
    ),
    Path("docs/ARCHITECTURE.md"): frozenset(
        {
            f'The deterministic report keeps the legacy `project: "{LEGACY_PUBLIC_BRAND}"` '
            'value and adds `product_name: "Esscher"` for public display. This is an additive '
            "alias; existing schema keys and values remain available."
        }
    ),
    Path("src/ringdown_market/cli.py"): frozenset({f'"project": "{LEGACY_PUBLIC_BRAND}",'}),
    Path("tests/test_cli.py"): frozenset({f'assert report["project"] == "{LEGACY_PUBLIC_BRAND}"'}),
}


def _git_paths(*args: str) -> set[Path]:
    result = subprocess.run(
        ["git", *args, "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return {Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw}


def _candidate_paths() -> tuple[set[Path], set[Path]]:
    tracked = _git_paths("ls-files")
    visible = tracked | _git_paths("ls-files", "--others", "--exclude-standard")
    return tracked, visible


def _read_text(path: Path, errors: list[str]) -> str | None:
    absolute = ROOT / path
    if path.suffix.lower() not in TEXT_SUFFIXES or not absolute.is_file():
        return None
    try:
        return absolute.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"{path.as_posix()}: text file is not valid UTF-8")
        return None


def _check_paths(tracked: set[Path], visible: set[Path], errors: list[str]) -> None:
    missing = sorted(REQUIRED_FILES - tracked)
    errors.extend(f"missing required file: {path.as_posix()}" for path in missing)

    for path in sorted(visible):
        name = path.name.casefold()
        parts = {part.casefold() for part in path.parts}
        if name in FORBIDDEN_NAMES:
            errors.append(f"forbidden local or secret file: {path.as_posix()}")
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden secret-key file type: {path.as_posix()}")
        if parts & FORBIDDEN_PARTS:
            errors.append(f"generated directory must not be committed: {path.as_posix()}")

    for path in sorted(tracked):
        name = path.name.casefold()
        if name == ".env.example":
            continue
        if name.startswith(".env"):
            errors.append(f"tracked environment file: {path.as_posix()}")


def _contains_host(text: str, host: str) -> bool:
    pattern = rf"(?<![a-z0-9.-]){re.escape(host)}(?=$|[^a-z0-9.-])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _check_text(path: Path, text: str, errors: list[str]) -> None:
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            errors.append(f"{path.as_posix()}: possible {label}")

    root = path.parts[0].casefold() if path.parts else ""
    if root in BROKER_CODE_ROOTS and path != HYGIENE_CHECKER:
        for host in DIRECT_ALPACA_HOSTS:
            if _contains_host(text, host):
                errors.append(
                    f"{path.as_posix()}: direct Alpaca host '{host}' bypasses the adapter boundary"
                )


def _check_legacy_brand(path: Path, text: str, errors: list[str]) -> None:
    allowed_lines = LEGACY_BRAND_ALLOWLIST.get(path, frozenset())
    seen = {line: 0 for line in allowed_lines}

    for line_number, line in enumerate(text.splitlines(), start=1):
        visible_line = INLINE_HTML_TAG.sub("", line)
        matches = LEGACY_PUBLIC_BRAND_PATTERN.findall(visible_line)
        # All-lowercase ringdown is reserved for the preserved machine identifiers.
        if not any(match != match.lower() for match in matches):
            continue
        normalized = line.strip()
        if normalized not in allowed_lines:
            errors.append(
                f"{path.as_posix()}:{line_number}: stale public brand "
                f"'{LEGACY_PUBLIC_BRAND}'; use Esscher or add an exact "
                "compatibility/migration allowance"
            )
            continue
        seen[normalized] += 1

    for allowed_line, count in sorted(seen.items()):
        if count != 1:
            errors.append(
                f"{path.as_posix()}: allowlisted legacy brand line occurs {count} times; "
                f"expected exactly once: {allowed_line}"
            )


def _check_markdown_links(path: Path, text: str, errors: list[str]) -> None:
    if path.suffix.lower() != ".md":
        return
    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative = unquote(target.split("#", maxsplit=1)[0])
        if not relative:
            continue
        candidate = (ROOT / path.parent / relative).resolve()
        if not candidate.is_relative_to(ROOT) or not candidate.exists():
            errors.append(f"{path.as_posix()}: broken local link '{raw_target}'")


def _check_fixture(path: Path, text: str, errors: list[str]) -> None:
    if path.parts[:2] != ("tests", "fixtures") or path.suffix != ".json":
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(f"{path.as_posix()}: invalid JSON ({exc.msg})")
        return
    if not isinstance(payload, dict) or "events" not in payload:
        return

    fixture_class = payload.get("fixture_class")
    if fixture_class not in {"POINT_IN_TIME_EVENT_PANEL", "SYNTHETIC_CONTRACT_FIXTURE"}:
        errors.append(f"{path.as_posix()}: missing supported fixture_class")
        return
    if fixture_class == "SYNTHETIC_CONTRACT_FIXTURE":
        limitations = payload.get("limitations")
        if not isinstance(limitations, list):
            errors.append(f"{path.as_posix()}: synthetic fixture needs a limitations list")
            return
        missing = SYNTHETIC_LIMITATIONS - set(limitations)
        if missing:
            joined = ", ".join(sorted(missing))
            errors.append(f"{path.as_posix()}: missing synthetic limitations: {joined}")


def main() -> int:
    errors: list[str] = []
    tracked, visible = _candidate_paths()
    _check_paths(tracked, visible, errors)
    missing_allowlist_paths = set(LEGACY_BRAND_ALLOWLIST) - visible
    errors.extend(
        f"legacy brand allowlist path is not visible: {path.as_posix()}"
        for path in sorted(missing_allowlist_paths)
    )

    for path in sorted(visible):
        text = _read_text(path, errors)
        if text is None:
            continue
        _check_text(path, text, errors)
        _check_legacy_brand(path, text, errors)
        _check_markdown_links(path, text, errors)
        _check_fixture(path, text, errors)

    if errors:
        print("repository hygiene: FAIL")
        for error in sorted(set(errors)):
            print(f"- {error}")
        return 1

    print(f"repository hygiene: PASS ({len(visible)} visible files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
