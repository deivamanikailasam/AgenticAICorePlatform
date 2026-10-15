"""Functions a worker can run as durable workflows (``durable_executor.temporal``)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

FUNCTIONS: dict[str, Callable[[Any], Awaitable[Any]]] = {}
