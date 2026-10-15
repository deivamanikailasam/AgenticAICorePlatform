"""Write (or check) docs/reference/agent-spec.schema.json and docs/reference/agent-yaml.md."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_spec.reference import render_reference, schema

REF = Path(__file__).resolve().parent.parent / "docs" / "reference"
OUTPUTS = {
    REF / "agent-spec.schema.json": lambda: json.dumps(schema(), indent=2, sort_keys=True) + "\n",
    REF / "agent-yaml.md": render_reference,
}


def main(argv: list[str]) -> int:
    check = bool(argv) and argv[0] == "--check"
    stale = []
    for path, render in OUTPUTS.items():
        text = render()
        if check:
            if not path.exists() or path.read_text() != text:
                stale.append(path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            print(f"spec-reference: wrote {path.name}")
    if check:
        print(f"spec-reference: {'stale: ' + ', '.join(stale) if stale else 'up to date'}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
