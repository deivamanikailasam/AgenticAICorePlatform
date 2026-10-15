"""Shared contract tests. Every implementation of an interface must pass its contract.

Usage in an implementation's test module::

    from agent_contracts.testing import CheckpointerContract

    class TestPostgresCheckpointer(CheckpointerContract):
        def make(self):
            return PostgresCheckpointer(dsn)

Each contract is a plain pytest class; subclasses provide ``make`` (some contracts pass in
collaborators such as a tool registry or workflow table).
"""

from agent_contracts.testing.contracts import *  # noqa: F403
from agent_contracts.testing.contracts import __all__  # noqa: F401
