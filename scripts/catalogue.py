"""Generate docs/reference/components.md from the registry, or check it is up to date."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "docs" / "reference" / "components.md"
HEADER = """# Component catalogue

Generated from the component registry by `just catalogue` — do not edit by hand. CI fails if
this file is stale. `implemented` means the component passes its contract tests; `stub` means a
documented adapter with a contract test and an ADR on when to complete it. The spec's
"Catalogue of switchable components" is the build target; this file is the truth.

"""


def render() -> str:
    out = subprocess.run(
        [sys.executable, "-m", "agent_contracts.registry", "--markdown"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return HEADER + out


def main(argv: list[str]) -> int:
    text = render()
    if argv and argv[0] == "--check":
        if not TARGET.exists() or TARGET.read_text() != text:
            print("catalogue: docs/reference/components.md is stale; run `just catalogue`")
            return 1
        print("catalogue: up to date")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text)
    print(f"catalogue: wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
