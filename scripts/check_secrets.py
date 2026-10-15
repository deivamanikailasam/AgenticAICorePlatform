"""Fail if anything that looks like a secret is tracked by git.

This is a fast, dependency-free first line of defence. It complements, not replaces, the
secret scanning that GitHub and the CI supply-chain job perform. ``*.enc.*`` files must be
SOPS-encrypted (they contain a ``sops`` metadata block).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS: dict[str, re.Pattern[str]] = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "age secret key": re.compile(r"AGE-SECRET-KEY-1[0-9A-Z]{58}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    "Anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{32,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
}
ALLOW_MARKER = "secret-scan: allow"
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".lock"}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [ROOT / p for p in out.split("\0") if p]


def scan(path: Path) -> list[str]:
    if path.suffix in SKIP_SUFFIXES or not path.is_file():
        return []
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        return []
    rel = path.relative_to(ROOT)
    problems: list[str] = []
    if ".enc." in path.name and "sops" not in text:
        problems.append(f"{rel}: named *.enc.* but is not SOPS-encrypted")
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                problems.append(f"{rel}:{lineno}: possible {label}")
    return problems


def main() -> int:
    files = tracked_files()
    problems = [p for f in files for p in scan(f)]
    for p in problems:
        print(f"secret-scan: {p}")
    print(f"secret-scan: {len(files)} file(s), {len(problems)} finding(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
