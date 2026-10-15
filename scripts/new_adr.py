"""Create the next ADR from the template: ``just adr "Use X for Y"``."""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def next_number(adr_dir: Path) -> int:
    nums = [int(p.name[:4]) for p in adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")]
    return max(nums, default=0) + 1


def create(title: str, adr_dir: Path = ADR_DIR, today: date | None = None) -> Path:
    template = (adr_dir / "template.md").read_text()
    num = next_number(adr_dir)
    path = adr_dir / f"{num:04d}-{slugify(title)}.md"
    body = (
        template.replace("NNNN", f"{num:04d}")
        .replace("<Title>", title)
        .replace("YYYY-MM-DD", (today or date.today()).isoformat())
    )
    path.write_text(body)
    return path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit('usage: just adr "<title>"')
    print(create(sys.argv[1]))
