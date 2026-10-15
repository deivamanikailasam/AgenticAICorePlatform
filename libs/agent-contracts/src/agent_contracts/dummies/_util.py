from __future__ import annotations

import json
import re
from typing import Any

WORD = re.compile(r"[a-z0-9]+")


def words(text: str) -> set[str]:
    return set(WORD.findall(text.lower()))


def approx_tokens(text: str) -> int:
    """Rough token estimate (about four characters per token). Good enough for fakes."""
    return max(1, (len(text) + 3) // 4) if text else 0


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
