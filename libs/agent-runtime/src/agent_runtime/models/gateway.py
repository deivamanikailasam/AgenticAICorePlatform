"""``ModelClient`` over the Project 0 LiteLLM gateway (OpenAI-compatible API).

* Callers name a **role** (``planner``) or an alias; roles resolve through the spec's
  ``models:`` then ``platform.yaml``. Each role uses its own LiteLLM virtual key so the gateway
  attributes cost and enforces the role's budget.
* Tool calling and JSON-schema structured output, with a capability check before the call and
  one repair attempt when the reply does not match the schema.
* Streaming over SSE with usage in the final chunk.
* Retries with exponential backoff on network errors, 429 and 5xx.
* Usage and cost per call: tokens from the response, cost from LiteLLM's
  ``x-litellm-response-cost`` header (USD), converted to AUD with the rate in ``platform.yaml``.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal

import httpx
import jsonschema
from agent_contracts.interfaces import ModelClient
from agent_contracts.models import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    Message,
    ModelCapabilities,
    Role,
    ToolCall,
    Usage,
)
from agent_spec.platform import PlatformConfig, load_platform

from agent_runtime.models.errors import CapabilityError, GatewayError, StructuredOutputError
from agent_runtime.models.ledger import UsageLedger

RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
FinishReason = Literal["stop", "tool_calls", "length", "error"]
FINISH: dict[str, FinishReason] = {
    "stop": "stop",
    "tool_calls": "tool_calls",
    "length": "length",
    "function_call": "tool_calls",
}


def _to_openai(message: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.name and message.role is not Role.TOOL:
        out["name"] = message.name
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in message.tool_calls
        ]
    if message.tool_call_id:
        out["tool_call_id"] = message.tool_call_id
    return out


def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> tuple[ToolCall, ...]:
    calls = []
    for c in raw or []:
        fn = c.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {
                "_unparseable": fn.get("arguments", "")
            }  # surfaced to the repair logic (Step 13)
        calls.append(
            ToolCall(
                id=c.get("id") or ToolCall(name="x").id, name=fn.get("name", ""), arguments=args
            )
        )
    return tuple(calls)


class GatewayModelClient(ModelClient):
    """Chat, tools, structured output and streaming through the Project 0 LiteLLM gateway."""

    def __init__(
        self,
        platform: PlatformConfig | None = None,
        spec_models: Mapping[str, str] | None = None,
        keys: Mapping[str, str] | None = None,
        ledger: UsageLedger | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_seconds: float = 0.5,
    ) -> None:
        self.platform = platform or load_platform()
        self.spec_models = dict(spec_models or {})
        self._keys = dict(keys) if keys is not None else None
        self.ledger = ledger or UsageLedger()
        self._backoff = backoff_seconds
        self._client = httpx.AsyncClient(
            base_url=self.platform.gateway.url.rstrip("/"),
            timeout=self.platform.gateway.timeout_seconds,
            transport=transport,
        )

    # ------------------------------------------------------------ resolution

    def resolve(self, role_or_alias: str) -> tuple[str, str]:
        """Return ``(role, alias)``. Unknown names are treated as aliases under the default role."""
        known_roles = {**self.platform.models.roles, **self.spec_models}
        role = role_or_alias if role_or_alias in known_roles else "default"
        return role, self.platform.alias_for(role_or_alias, self.spec_models)

    def _key(self, role: str) -> str:
        """Per-role virtual key: explicit ``keys`` (tests) or ``LITELLM_KEY_<ROLE>`` env vars."""
        if self._keys is not None:
            for name in (role, "default"):
                if self._keys.get(name):
                    return self._keys[name]
        else:
            prefix = self.platform.gateway.key_env_prefix
            for name in (f"{prefix}{role.upper()}", f"{prefix}DEFAULT", "LITELLM_API_KEY"):
                if os.environ.get(name):
                    return os.environ[name]
        raise GatewayError(
            f"no gateway key for role '{role}' (set {self.platform.gateway.key_env_prefix}{role.upper()})"
        )

    def capabilities(self, model: str) -> ModelCapabilities:
        return self.platform.capabilities(self.resolve(model)[1])

    def _check(self, request: ChatRequest, alias: str) -> None:
        caps = self.platform.capabilities(alias)
        if request.tools and not caps.tool_calling:
            raise CapabilityError(f"model '{alias}' does not support tool calling")
        if request.response_schema is not None and not caps.structured_output:
            raise CapabilityError(f"model '{alias}' does not support structured output")

    # ------------------------------------------------------------ HTTP

    def _body(self, request: ChatRequest, alias: str, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": alias,
            "messages": [_to_openai(m) for m in request.messages],
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        if request.response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "output",
                    "schema": request.response_schema,
                    "strict": True,
                },
            }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.metadata:
            body["metadata"] = dict(request.metadata)
            if "tenant_id" in request.metadata:
                body["user"] = request.metadata["tenant_id"]
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    async def _post(
        self, body: dict[str, Any], role: str, path: str = "/v1/chat/completions"
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._key(role)}"}
        attempts = self.platform.gateway.max_retries + 1
        last: str = ""
        for attempt in range(attempts):
            try:
                resp = await self._client.post(path, json=body, headers=headers)
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code < 400:
                    return resp
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                if resp.status_code not in RETRYABLE:
                    raise GatewayError(f"gateway refused the request ({last})", resp.status_code)
            if attempt < attempts - 1:
                await asyncio.sleep(self._backoff * 2**attempt)
        raise GatewayError(f"gateway unavailable after {attempts} attempts ({last})")

    def _usage(self, raw: Mapping[str, Any] | None, cost_header: str | None) -> Usage:
        raw = raw or {}
        usd = float(cost_header) if cost_header else 0.0
        return Usage(
            input_tokens=int(raw.get("prompt_tokens", 0)),
            output_tokens=int(raw.get("completion_tokens", 0)),
            cost_aud=round(usd * self.platform.currency.usd_to_aud, 8),
        )

    # ------------------------------------------------------------ ModelClient

    async def _once(self, request: ChatRequest, role: str, alias: str) -> ChatResponse:
        resp = await self._post(self._body(request, alias, stream=False), role)
        doc = resp.json()
        choice = doc["choices"][0]
        msg = choice.get("message", {})
        message = Message(
            role=Role.ASSISTANT,
            content=msg.get("content") or "",
            tool_calls=_parse_tool_calls(msg.get("tool_calls")),
        )
        usage = self._usage(doc.get("usage"), resp.headers.get("x-litellm-response-cost"))
        self.ledger.record(request.metadata.get("run_id", "-"), role, alias, usage)
        finish = FINISH.get(choice.get("finish_reason") or "stop", "stop")
        return ChatResponse(
            message=message, model=doc.get("model", alias), usage=usage, finish_reason=finish
        )

    async def embeddings(
        self, texts: list[str], model: str = "embedder", run_id: str = "-"
    ) -> list[list[float]]:
        """``/v1/embeddings`` for a role or alias (Step 15); usage and cost go to the ledger."""
        role, alias = self.resolve(model)
        resp = await self._post({"model": alias, "input": texts}, role, "/v1/embeddings")
        doc = resp.json()
        rows = sorted(doc.get("data", []), key=lambda r: r.get("index", 0))
        if len(rows) != len(texts):
            raise GatewayError(f"gateway returned {len(rows)} embeddings for {len(texts)} texts")
        self.ledger.record(
            run_id,
            role,
            alias,
            self._usage(doc.get("usage"), resp.headers.get("x-litellm-response-cost")),
        )
        return [[float(x) for x in r["embedding"]] for r in rows]

    async def chat(self, request: ChatRequest) -> ChatResponse:
        role, alias = self.resolve(request.model)
        self._check(request, alias)
        response = await self._once(request, role, alias)
        if request.response_schema is None or response.message.tool_calls:
            return response
        error = self._schema_error(response.message.content, request.response_schema)
        if error is None:
            return response
        repair = request.model_copy(
            update={
                "messages": (
                    *request.messages,
                    response.message,
                    Message(
                        role=Role.USER,
                        content=f"Your reply did not match the required JSON schema: {error}. "
                        "Reply again with only a JSON value that matches the schema.",
                    ),
                )
            }
        )
        second = await self._once(repair, role, alias)
        error = self._schema_error(second.message.content, request.response_schema)
        if error is not None:
            raise StructuredOutputError(
                f"model '{alias}' did not return valid JSON for the schema: {error}"
            )
        return second.model_copy(update={"usage": response.usage + second.usage})

    @staticmethod
    def _schema_error(text: str, schema: Mapping[str, Any]) -> str | None:
        try:
            jsonschema.validate(json.loads(text), schema)
        except json.JSONDecodeError as exc:
            return f"not JSON ({exc.msg})"
        except jsonschema.ValidationError as exc:
            return exc.message
        return None

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        role, alias = self.resolve(request.model)
        self._check(request, alias)
        body = self._body(request, alias, stream=True)
        headers = {"Authorization": f"Bearer {self._key(role)}"}
        usage: Usage | None = None
        finish: FinishReason | None = None
        partial: dict[int, dict[str, Any]] = {}
        async with self._client.stream(
            "POST", "/v1/chat/completions", json=body, headers=headers
        ) as resp:
            if resp.status_code >= 400:
                text = (await resp.aread()).decode()[:200]
                raise GatewayError(
                    f"gateway refused the stream (HTTP {resp.status_code}: {text})",
                    resp.status_code,
                )
            cost = resp.headers.get("x-litellm-response-cost")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                doc = json.loads(data)
                if doc.get("usage"):
                    usage = self._usage(doc["usage"], cost)
                for choice in doc.get("choices", []):
                    delta = choice.get("delta", {})
                    for tc in delta.get("tool_calls") or []:
                        slot = partial.setdefault(
                            tc.get("index", 0),
                            {"id": tc.get("id"), "function": {"name": "", "arguments": ""}},
                        )
                        fn = tc.get("function", {})
                        slot["function"]["name"] += fn.get("name") or ""
                        slot["function"]["arguments"] += fn.get("arguments") or ""
                        slot["id"] = slot["id"] or tc.get("id")
                    if delta.get("content"):
                        yield ChatChunk(delta=delta["content"])
                    if choice.get("finish_reason"):
                        finish = FINISH.get(choice["finish_reason"], "stop")
        final_usage = usage or Usage()
        self.ledger.record(request.metadata.get("run_id", "-"), role, alias, final_usage)
        yield ChatChunk(
            tool_calls=_parse_tool_calls(list(partial.values())),
            finish_reason=finish or "stop",
            usage=final_usage,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
