"""The component registry: how every switchable part is found by configuration.

Implementations register in one of two ways:

* in code, with ``@register("tool_executor", "mcp")``;
* in packaging, with an entry point in group ``agent_platform.components`` named
  ``<slot>.<name>`` (``"tool_executor.mcp" = "agent_tools.mcp:McpToolExecutor"``).

``agent.yaml`` and ``platform.yaml`` then pick implementations by name, so switching a
component never needs an application code change (design principle 4).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, TypeVar

from agent_contracts.interfaces import INTERFACES, Component

ENTRY_POINT_GROUP = "agent_platform.components"
C = TypeVar("C", bound=type[Component])


@dataclass(frozen=True)
class Registration:
    slot: str
    name: str
    target: str  # "module:qualname"
    status: str = "implemented"  # implemented | stub
    summary: str = ""


class Registry:
    def __init__(self) -> None:
        self._classes: dict[tuple[str, str], type[Component]] = {}
        self._meta: dict[tuple[str, str], Registration] = {}
        self._discovered = False

    # ------------------------------------------------------------ registration

    def add(self, slot: str, name: str, cls: type[Component]) -> None:
        interface = INTERFACES.get(slot)
        if interface is None:
            raise ValueError(f"unknown slot {slot!r}; known: {sorted(INTERFACES)}")
        if not (isinstance(cls, type) and issubclass(cls, interface)):
            raise TypeError(f"{cls!r} does not implement {interface.__name__}")
        key = (slot, name)
        existing = self._classes.get(key)
        if existing is not None and existing is not cls:
            raise ValueError(f"{slot}.{name} already registered to {existing.__qualname__}")
        self._classes[key] = cls
        doc = (cls.__doc__ or "").strip().splitlines()
        self._meta[key] = Registration(
            slot=slot,
            name=name,
            target=f"{cls.__module__}:{cls.__qualname__}",
            status=getattr(cls, "component_status", "implemented"),
            summary=doc[0] if doc else "",
        )

    def register(self, slot: str, name: str) -> Callable[[C], C]:
        def decorate(cls: C) -> C:
            self.add(slot, name, cls)
            return cls

        return decorate

    # ------------------------------------------------------------ discovery

    def discover(self, force: bool = False) -> None:
        """Load every entry point once. Broken entry points fail loudly."""
        if self._discovered and not force:
            return
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            slot, _, name = ep.name.partition(".")
            if not name:
                raise ValueError(f"entry point {ep.name!r} must be named '<slot>.<name>'")
            self.add(slot, name, ep.load())
        self._discovered = True

    # ------------------------------------------------------------ lookup

    def get(self, slot: str, name: str) -> type[Component]:
        self.discover()
        try:
            return self._classes[(slot, name)]
        except KeyError:
            options = ", ".join(sorted(n for s, n in self._classes if s == slot)) or "none"
            raise KeyError(f"no {slot} named {name!r}; registered: {options}") from None

    def create(self, slot: str, name: str, /, **config: Any) -> Component:
        return self.get(slot, name)(**config)

    def names(self, slot: str) -> list[str]:
        self.discover()
        return sorted(n for s, n in self._classes if s == slot)

    def registrations(self) -> Iterator[Registration]:
        self.discover()
        for key in sorted(self._meta):
            yield self._meta[key]


registry = Registry()
register = registry.register


def render_table(regs: list[Registration]) -> str:
    rows = [("slot", "name", "status", "implementation")]
    rows += [(r.slot, r.name, r.status, r.target) for r in regs]
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    return "\n".join(
        "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in rows
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-components", description=__doc__.splitlines()[0])
    parser.add_argument("--slot", help="only this slot")
    parser.add_argument("--markdown", action="store_true", help="emit the catalogue table")
    args = parser.parse_args(argv)
    regs = [r for r in registry.registrations() if not args.slot or r.slot == args.slot]
    if args.markdown:
        print("| Slot | Name | Status | Implementation | Summary |\n|---|---|---|---|---|")
        for r in regs:
            print(f"| {r.slot} | {r.name} | {r.status} | `{r.target}` | {r.summary} |")
    else:
        print(render_table(regs))
        missing = sorted(set(INTERFACES) - {r.slot for r in regs})
        print(
            f"\n{len(regs)} components across "
            f"{len(INTERFACES) - len(missing)} of {len(INTERFACES)} slots"
        )
        if missing:
            print("slots with no implementation: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
