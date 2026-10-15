"""Contracts for every switchable part of the Agentic AI Core Platform.

Everything a pattern, runtime or service depends on is defined here as an interface plus
Pydantic models. Implementations live elsewhere and register themselves with
:func:`agent_contracts.registry.register` or a Python entry point.
"""

from agent_contracts.registry import Registry, register, registry

__all__ = ["Registry", "register", "registry"]
