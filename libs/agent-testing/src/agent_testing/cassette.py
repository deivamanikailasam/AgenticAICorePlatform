"""Record-and-replay of model and tool calls (Step 33).

A cassette is a JSON file of the model and tool calls a run made: each request (with what varies
between runs, such as run ids, left out) and the response it got. Record once against real
services; replay any number of times with no network, no model and no tool side effects, and get
the same run.

* ``RecordingModelClient`` / ``RecordingTool`` wrap the real ones and write every call.
* ``ReplayModelClient`` / ``ReplayTool`` answer from the cassette. A request the cassette doesn't
  hold (because the prompt, tools or arguments changed) raises ``CassetteMismatchError`` naming the
  closest recorded request, so a behaviour change can't pass silently.
* ``transcript()`` is the canonical bytes of the calls made (requests and responses, in order).
  A replayed run is byte-identical to its recording when the two transcripts are equal.

Policy, guardrails, budgets and approvals are not recorded: they run live during replay, so a
replayed run still exercises the platform's own logic.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any, Literal

from agent_contracts.interfaces import ModelClient, Tool
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    ModelCapabilities,
    RunContext,
    ToolSpec,
)

Mode = Literal["record", "replay"]
VERSION = 1


def _plain(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    return dump(mode="json") if callable(dump) else str(value)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_plain
    ).encode()


def fingerprint(kind: str, request: Any) -> str:
    return hashlib.sha256(canonical({"kind": kind, "request": request})).hexdigest()


def model_request(request: ChatRequest) -> dict[str, Any]:
    """What identifies a model call: everything but the per-run metadata (run id, tenant)."""
    body = request.model_dump(mode="json")
    body.pop("metadata", None)
    return body


class CassetteMismatchError(AssertionError):
    """A call the cassette has no recording for: the run's behaviour changed since recording."""


class Cassette:
    def __init__(self, path: str | Path, mode: Mode) -> None:
        self.path = Path(path)
        self.mode: Mode = mode
        self.calls: list[dict[str, Any]] = []  # what this run did, in order
        self._queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._recorded: list[dict[str, Any]] = []
        if mode == "replay":
            doc = json.loads(self.path.read_text())
            if doc.get("version") != VERSION:
                raise ValueError(f"{self.path}: cassette version {doc.get('version')} unsupported")
            self._recorded = doc["calls"]
            for call in self._recorded:
                self._queues[call["key"]].append(call)

    # ------------------------------------------------------------ record

    def add(self, kind: str, request: Any, response: Any) -> None:
        self.calls.append(
            {
                "kind": kind,
                "key": fingerprint(kind, request),
                "request": request,
                "response": response,
            }
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"version": VERSION, "calls": self.calls}
        # key order is kept as recorded: a tool result's order is part of what the model saw
        self.path.write_text(json.dumps(doc, indent=1, ensure_ascii=False, default=_plain) + "\n")

    # ------------------------------------------------------------ replay

    def take(self, kind: str, request: Any) -> Any:
        """The recorded response for this request (same requests are answered in recorded
        order, so parallel calls replay correctly)."""
        key = fingerprint(kind, request)
        queue = self._queues.get(key)
        if not queue:
            raise CassetteMismatchError(self._explain(kind, request))
        call = queue.popleft()
        self.calls.append(call)
        return call["response"]

    def _explain(self, kind: str, request: Any) -> str:
        same_kind = [c for c in self._recorded if c["kind"] == kind]
        msg = f"{self.path.name}: no recorded {kind} call matches this request"
        if not same_kind:
            return msg + f" (the cassette has no {kind} calls)"
        mine = canonical(request).decode()

        def shared_prefix(other: dict[str, Any]) -> int:
            theirs = canonical(other["request"]).decode()
            n = 0
            for a, b in zip(mine, theirs, strict=False):
                if a != b:
                    break
                n += 1
            return n

        nearest = max(same_kind, key=shared_prefix)
        at = shared_prefix(nearest)
        theirs = canonical(nearest["request"]).decode()
        return (
            f"{msg}. Closest recording differs at character {at}:\n"
            f"  recorded: ...{theirs[max(0, at - 40) : at + 80]}\n"
            f"  now:      ...{mine[max(0, at - 40) : at + 80]}\n"
            "Re-record the cassette if the change is intended."
        )

    def unused(self) -> int:
        return sum(len(q) for q in self._queues.values())

    def transcript(self) -> bytes:
        return canonical([{k: c[k] for k in ("kind", "request", "response")} for c in self.calls])


# ---------------------------------------------------------------- models


class RecordingModelClient(ModelClient):
    def __init__(self, inner: ModelClient, cassette: Cassette) -> None:
        self.inner, self.cassette = inner, cassette

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.inner.capabilities(model)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        response = await self.inner.chat(request)
        self.cassette.add("model", model_request(request), response.model_dump(mode="json"))
        return response

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        chunks = []
        async for chunk in self.inner.stream(request):
            chunks.append(chunk.model_dump(mode="json"))
            yield chunk
        self.cassette.add("model.stream", model_request(request), chunks)


class ReplayModelClient(ModelClient):
    def __init__(self, cassette: Cassette, capabilities: ModelCapabilities | None = None) -> None:
        self.cassette = cassette
        self._caps = capabilities or ModelCapabilities(
            tool_calling=True, structured_output=True, streaming=True
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return self._caps

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse.model_validate(self.cassette.take("model", model_request(request)))

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        for chunk in self.cassette.take("model.stream", model_request(request)):
            yield ChatChunk.model_validate(chunk)


# ---------------------------------------------------------------- tools


def _tool_request(spec: ToolSpec, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"tool": spec.name, "version": spec.version, "arguments": dict(arguments)}


class ReplayedToolError(RuntimeError):
    """The recorded tool raised; replay raises the same type name and message."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class RecordingTool(Tool):
    """Wraps a real tool: the call runs and its result (or error) is recorded."""

    def __init__(self, inner: Tool, cassette: Cassette) -> None:
        self.inner, self.cassette = inner, cassette

    @property
    def spec(self) -> ToolSpec:
        return self.inner.spec

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        request = _tool_request(self.spec, arguments)
        try:
            result = await self.inner.invoke(arguments, ctx)
        except Exception as exc:
            self.cassette.add("tool", request, {"error": type(exc).__name__, "message": str(exc)})
            raise
        self.cassette.add(
            "tool", request, {"result": json.loads(json.dumps(result, default=_plain))}
        )
        return result


class ReplayTool(Tool):
    """Stands in for a tool: answers from the cassette, never touches the real system."""

    def __init__(self, spec: ToolSpec, cassette: Cassette) -> None:
        self._spec, self.cassette = spec, cassette

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def invoke(self, arguments: Mapping[str, Any], ctx: RunContext) -> Any:
        recorded = self.cassette.take("tool", _tool_request(self._spec, arguments))
        if "error" in recorded:
            raise ReplayedToolError(recorded["error"], recorded["message"])
        return recorded["result"]
