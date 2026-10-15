import pytest
from agent_contracts.interfaces import Planner
from agent_contracts.registry import Registry


class _NotAComponent:
    pass


def test_register_rejects_unknown_slot_and_wrong_type() -> None:
    reg = Registry()
    with pytest.raises(ValueError, match="unknown slot"):
        reg.add("teleporter", "x", Planner)
    with pytest.raises(TypeError):
        reg.add("planner", "x", _NotAComponent)  # type: ignore[arg-type]


def test_decorator_and_lookup_error_lists_options() -> None:
    reg = Registry()
    reg._discovered = True  # isolate from installed entry points

    @reg.register("planner", "mine")
    class Mine(Planner):
        async def plan(self, goal, tools, ctx):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    assert reg.get("planner", "mine") is Mine
    with pytest.raises(KeyError, match="registered: mine"):
        reg.get("planner", "other")


def test_conflicting_registration_rejected() -> None:
    reg = Registry()

    class A(Planner):
        async def plan(self, goal, tools, ctx):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    class B(A):
        pass

    reg.add("planner", "x", A)
    reg.add("planner", "x", A)  # same class twice is fine
    with pytest.raises(ValueError, match="already registered"):
        reg.add("planner", "x", B)
