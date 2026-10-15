"""Small built-in platform tools (``local://platform/...``). Safe, read-only and deterministic."""

from __future__ import annotations

import ast
import operator
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_contracts.interfaces import Tool
from agent_contracts.models import DataClass, RunContext, SideEffect, ToolSpec

_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval(expression: str) -> float:
    """Evaluate arithmetic only: numbers, + - * / // % ** and parentheses. No names, no calls."""

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int | float)
            and not isinstance(node.value, bool)
        ):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and (abs(right) > 64 or abs(left) > 1e6):
                raise ValueError("exponent too large")
            return float(_OPS[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return float(_OPS[type(node.op)](ev(node.operand)))
        raise ValueError(f"unsupported expression element: {type(node).__name__}")

    return ev(ast.parse(expression, mode="eval"))


class CalculatorTool(Tool):
    """Evaluates an arithmetic expression safely (no code execution)."""

    example_arguments: ClassVar[dict[str, Any]] = {"expression": "(2 + 3) * 4"}

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calculator",
            ref="local://platform/calculator",
            version="1.0.0",
            description="Evaluate an arithmetic expression such as '(2 + 3) * 4'. Numbers and + - * / // % ** only.",
            input_schema={
                "type": "object",
                "properties": {"expression": {"type": "string", "minLength": 1, "maxLength": 200}},
                "required": ["expression"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "number"}},
                "required": ["result"],
            },
            side_effect=SideEffect.READ,
            idempotent=True,
            timeout_seconds=2,
            data_classification=DataClass.PUBLIC,
            owner="agent-platform",
        )

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        return {"result": safe_eval(str(arguments["expression"]))}


class ClockTool(Tool):
    """Returns the current date and time in a time zone (default Australia/Melbourne)."""

    example_arguments: ClassVar[dict[str, Any]] = {"timezone": "Australia/Melbourne"}

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="clock",
            ref="local://platform/clock",
            version="1.0.0",
            description="Current date and time in an IANA time zone, e.g. 'Australia/Melbourne'.",
            input_schema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "default": "Australia/Melbourne",
                        "maxLength": 64,
                    }
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"iso": {"type": "string"}, "timezone": {"type": "string"}},
                "required": ["iso", "timezone"],
            },
            side_effect=SideEffect.READ,
            idempotent=False,
            timeout_seconds=2,
            data_classification=DataClass.PUBLIC,
            owner="agent-platform",
        )

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        tz_name = str(arguments.get("timezone", "Australia/Melbourne"))
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {tz_name!r}") from exc
        return {
            "iso": datetime.now(UTC).astimezone(tz).isoformat(timespec="seconds"),
            "timezone": tz_name,
        }


class WordCountTool(Tool):
    """Counts words and characters in a text."""

    example_arguments: ClassVar[dict[str, Any]] = {"text": "one two three"}

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="word_count",
            ref="local://platform/word_count",
            version="1.0.0",
            description="Count the words and characters in a text.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 1_000_000}},
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"words": {"type": "integer"}, "characters": {"type": "integer"}},
                "required": ["words", "characters"],
            },
            side_effect=SideEffect.READ,
            idempotent=True,
            timeout_seconds=2,
            data_classification=DataClass.INTERNAL,
            owner="agent-platform",
        )

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        text = str(arguments["text"])
        return {"words": len(text.split()), "characters": len(text)}


BUILTIN_TOOLS: tuple[type[Tool], ...] = (CalculatorTool, ClockTool, WordCountTool)
