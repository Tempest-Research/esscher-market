"""Fail CI when tracked repository content crosses Ringdown's safety boundaries."""

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
    "AGENTS.md",
    "CLAUDE.md",
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
SECRET_PATTERNS = {
    "GitHub token": re.compile(r"\bgh[opurs]_[A-Za-z0-9]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:EC |OPENSSH |RSA )?PRIVATE KEY-----"),
    "provider secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}
SYNTHETIC_LIMITATIONS = {
    "NO_BROKER_EXECUTION",
    "NOT_ALPHA_EVIDENCE",
    "NOT_HISTORICAL_DATA",
}
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


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
    missing = sorted(REQUIRED_FILES - visible)
    errors.extend(f"missing required file: {path.as_posix()}" for path in missing)

    for path in sorted(visible):
        parts = set(path.parts)
        if path.name in FORBIDDEN_NAMES:
            errors.append(f"forbidden local or secret file: {path.as_posix()}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden secret-key file type: {path.as_posix()}")
        if parts & FORBIDDEN_PARTS:
            errors.append(f"generated directory must not be committed: {path.as_posix()}")

    for path in sorted(tracked):
        if path.name == ".env.example":
            continue
        if path.name.startswith(".env"):
            errors.append(f"tracked environment file: {path.as_posix()}")


def _check_text(path: Path, text: str, errors: list[str]) -> None:
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            errors.append(f"{path.as_posix()}: possible {label}")

    if path.parts[:2] == ("src", "ringdown_market"):
        for host in DIRECT_ALPACA_HOSTS:
            if host in text:
                errors.append(
                    f"{path.as_posix()}: direct Alpaca host '{host}' bypasses the adapter boundary"
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

    for path in sorted(visible):
        text = _read_text(path, errors)
        if text is None:
            continue
        _check_text(path, text, errors)
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
