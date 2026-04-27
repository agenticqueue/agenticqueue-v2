from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

ARTIFACT_GLOB = "plans/v2-rebuild/artifacts/cap-02/*"
BINARY_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
PATTERNS = [
    (re.compile(r"\$argon2id\$[^\s\"'<>]+"), "[ARGON2_REDACTED]"),
    (re.compile(r"\baq2_[A-Za-z0-9_-]{20,}\b"), "[TOKEN_REDACTED]"),
    (
        re.compile(
            r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
            r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b"
        ),
        "[UUID_REDACTED]",
    ),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[TOKEN_REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9_-]{40,}\b"), "[TOKEN_REDACTED]"),
]


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    lines = [line.rstrip() for line in redacted.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _paths(args: list[str]) -> list[Path]:
    values = args or [ARTIFACT_GLOB]
    paths: list[Path] = []
    for value in values:
        matches = glob.glob(value)
        candidates = matches or [value]
        for candidate in candidates:
            path = Path(candidate)
            if path.is_dir():
                paths.extend(child for child in path.rglob("*") if child.is_file())
            elif path.is_file():
                paths.append(path)
    return paths


def redact_file(path: Path) -> None:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    path.write_text(redact_text(text), encoding="utf-8", newline="\n")


def main(argv: list[str]) -> int:
    if not argv and not sys.stdin.isatty():
        sys.stdout.write(redact_text(sys.stdin.read()))
        return 0

    for path in _paths(argv):
        redact_file(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
