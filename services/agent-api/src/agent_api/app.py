"""The run API: HTTP + SSE (default) or WebSocket, behind Keycloak.

=========================================  ==============================================
``GET  /``                                 a minimal page driven only by AG-UI events
``GET  /healthz``                          liveness
``GET  /v1/agents``                        agents this API can run
``POST /v1/agents/{agent}/runs``           create a run (``Idempotency-Key`` honoured)
``GET  /v1/runs/{id}``                     state and pending approvals
``GET  /v1/runs/{id}/events?after=N``      AG-UI events over SSE (ends when the run is idle)
``WS   /v1/runs/{id}/ws``                  the same events over a WebSocket
``POST /v1/runs/{id}/approvals``           approve or reject, as the caller (verified token)
``POST /v1/runs/{id}/resume``              answer a non-approval interrupt (owner only)
``POST /v1/runs/{id}/cancel``              cancel (owner only)
``POST /v1/runs/{id}/retry``               continue a failed run from its checkpoints (owner only)
``GET  /v1/threads``                       the caller's threads and their runs
``GET  /v1/threads/{id}/checkpoints``      time travel: a thread's checkpoints
``POST /v1/threads/{id}/fork``             time travel: continue from an earlier checkpoint
``POST /v1/admin/erasure``                 right to erasure for one user (privacy officer)
``GET  /v1/me/export``                     access: the caller's own runs, event logs, memories
``POST /v1/admin/export``                  access for one user (privacy officer)
``POST /v1/admin/retention``               apply governance/data-lifecycle.yaml (officers)
``GET  /v1/memories?agent=A``              inspect: what agent A remembers for the caller
``DELETE /v1/memories/{agent}/{id}``       the caller deletes one of their own memories
``POST /v1/admin/memory/flush``            flush every memory one run wrote (officers)
``GET  /v1/admin/releases``                versions, candidates, flags, online-eval verdicts
=========================================  ==============================================

Every call needs a bearer token verified against Keycloak (issuer, signature, expiry,
audience). The WebSocket takes its token in the first message, so tokens never appear in URLs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from importlib import resources
from pathlib import Path
from typing import Any, Protocol

import yaml
from agent_tools.auth import tenant_of
from mcp.server.auth.provider import AccessToken
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from agent_api.runs import ConflictError, Principal, RunElsewhereError, RunManager

# Step 37: retention policy (owner policy, governance/data-lifecycle.yaml); AGENT_DATA_LIFECYCLE overrides
LIFECYCLE = Path(__file__).resolve().parents[4] / "governance" / "data-lifecycle.yaml"


def load_lifecycle() -> dict[str, Any] | None:
    path = Path(os.environ.get("AGENT_DATA_LIFECYCLE", str(LIFECYCLE)))
    return dict(yaml.safe_load(path.read_text())) if path.exists() else None


class TokenVerifier(Protocol):
    async def verify_token(self, token: str) -> AccessToken | None: ...


def principal_from(token: AccessToken) -> Principal:
    claims = token.claims or {}
    groups = tuple(str(g) for g in claims.get("groups", []))
    return Principal(
        user=str(claims.get("preferred_username") or token.subject or "unknown"),
        tenant=tenant_of(list(groups)),
        groups=groups,
        roles=tuple(str(r) for r in claims.get("realm_access", {}).get("roles", [])),
        token=token.token,
        client_id=token.client_id,
    )


Handler = Callable[[Request, Principal], Awaitable[Response]]

# set on a request one replica passes to the replica driving the run (Step 38); such a request
# is never passed on again
FORWARDED = "x-agent-platform-forwarded"
PASSED_HEADERS = ("authorization", "content-type", "accept", "idempotency-key", "last-event-id")


def build_app(
    manager: RunManager, verifier: TokenVerifier, sweep_seconds: float = 30.0
) -> Starlette:
    async def authenticate(raw: str | None) -> Principal | None:
        if not raw:
            return None
        verified = await verifier.verify_token(raw)
        return principal_from(verified) if verified is not None else None

    def guarded(handler: Handler) -> Callable[[Request], Awaitable[Response]]:
        async def endpoint(request: Request) -> Response:
            header = request.headers.get("authorization", "")
            who = await authenticate(
                header.removeprefix("Bearer ") if header.startswith("Bearer ") else None
            )
            if who is None:
                return JSONResponse(
                    {"error": "a valid bearer token is required"},
                    401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            try:
                return await handler(request, who)
            except RunElsewhereError as moved:
                return await forward(request, moved.peer)
            except KeyError:
                return JSONResponse({"error": "not found"}, 404)
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, 403)
            except ConflictError as exc:
                return JSONResponse({"error": str(exc)}, 409)
            except (LookupError, ValueError) as exc:
                return JSONResponse({"error": str(exc)}, 422)

        return endpoint

    async def forward(request: Request, peer: str) -> Response:
        """Answer from the replica that drives the run (it verifies the token again)."""
        if request.headers.get(FORWARDED):
            return JSONResponse({"error": "the run is moving between replicas; retry"}, 503)
        headers = {k: v for k, v in request.headers.items() if k.lower() in PASSED_HEADERS}
        headers[FORWARDED] = manager.node or "1"
        async with manager.peer_client(peer) as client:
            resp = await client.request(
                request.method,
                request.url.path,
                params=request.query_params,
                content=await request.body(),
                headers=headers,
            )
        return Response(resp.content, resp.status_code, media_type=resp.headers.get("content-type"))

    def owned(handler: Handler) -> Handler:
        """Run-scoped calls go to the replica driving the run (Step 38)."""

        async def endpoint(request: Request, who: Principal) -> Response:
            peer = await manager.locate(request.path_params["run_id"])
            if peer is not None:
                return await forward(request, peer)
            return await handler(request, who)

        return endpoint

    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except json.JSONDecodeError as exc:
            raise ValueError(f"request body is not JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    async def agents(request: Request, who: Principal) -> Response:
        return JSONResponse(
            {
                "agents": [
                    {
                        "name": s.name,
                        "version": s.version,
                        "pattern": s.pattern.value,
                        "description": s.description,
                    }
                    for s in manager.agents.values()
                ]
            }
        )

    async def create(request: Request, who: Principal) -> Response:
        data = await body(request)
        run, created = await manager.create(
            request.path_params["agent"],
            dict(data.get("input") or {}),
            who,
            request.headers.get("idempotency-key"),
            data.get("thread_id"),
        )
        return JSONResponse(run.summary(), 201 if created else 200)

    async def state(request: Request, who: Principal) -> Response:
        run = manager.get(request.path_params["run_id"], who)
        return JSONResponse(run.summary())

    async def run_events(
        run_id: str, who: Principal, after: int
    ) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        """Events from this replica's run, or from the store when another replica drives it."""
        if await manager.locate(run_id) is None:
            return manager.events(manager.get(run_id, who), after)
        await manager.visible_record(run_id, who)
        return manager.tail(run_id, who, after)

    async def events(request: Request, who: Principal) -> Response:
        after = int(request.query_params.get("after", request.headers.get("last-event-id", "-1")))
        source = await run_events(request.path_params["run_id"], who, after)

        async def stream() -> AsyncIterator[str]:
            async for index, event in source:
                yield f"id: {index}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def approvals(request: Request, who: Principal) -> Response:
        """``{"approved": true|false}`` or, with Step 28, ``{"decision": "approve" | "reject" |
        "escalate", "arguments": {...edited...}, "comment": "..."}``."""
        run = manager.get(request.path_params["run_id"], who)
        data = await body(request)
        if "decision" in data:
            action = str(data["decision"])
        elif isinstance(data.get("approved"), bool):
            action = "approve" if data["approved"] else "reject"
        else:
            raise ValueError("send 'decision' (approve, reject, escalate) or 'approved'")
        edited = data.get("arguments")
        if edited is not None and not isinstance(edited, dict):
            raise ValueError("'arguments' must be an object")
        outcome = await manager.decide(run, who, action, edited, data.get("comment"))
        return JSONResponse({**run.summary(), "outcome": outcome}, 202)

    async def inbox(request: Request, who: Principal) -> Response:
        return JSONResponse({"approvals": await manager.inbox(who)})

    async def reviews(request: Request, who: Principal) -> Response:
        return JSONResponse({"reviews": manager.review_queue(who)})

    async def review(request: Request, who: Principal) -> Response:
        data = await body(request)
        item = await manager.review(
            request.path_params["run_id"],
            who,
            str(data.get("verdict")),
            str(data.get("note") or ""),
        )
        return JSONResponse(item)

    async def takeover(request: Request, who: Principal) -> Response:
        run = manager.get(request.path_params["run_id"], who)
        data = await body(request)
        handover = await manager.take_over(
            run, who, data.get("answer"), str(data.get("note") or "")
        )
        return JSONResponse({**run.summary(), "handover": handover})

    async def resume(request: Request, who: Principal) -> Response:
        run = manager.get(request.path_params["run_id"], who)
        await manager.resume(run, who, await body(request))
        return JSONResponse(run.summary(), 202)

    async def retry(request: Request, who: Principal) -> Response:
        run = manager.get(request.path_params["run_id"], who)
        await manager.retry(run, who)
        return JSONResponse(run.summary(), 202)

    async def cancel(request: Request, who: Principal) -> Response:
        run = manager.get(request.path_params["run_id"], who)
        await manager.cancel(run, who)
        return JSONResponse(run.summary())

    async def threads(request: Request, who: Principal) -> Response:
        return JSONResponse({"threads": await manager.threads(who)})

    async def checkpoints(request: Request, who: Principal) -> Response:
        thread = request.path_params["thread_id"]
        return JSONResponse({"checkpoints": await manager.checkpoints(thread, who)})

    async def fork(request: Request, who: Principal) -> Response:
        data = await body(request)
        if not isinstance(data.get("checkpoint_id"), str):
            raise ValueError("'checkpoint_id' is required")
        run = await manager.fork(request.path_params["thread_id"], data["checkpoint_id"], who)
        return JSONResponse(run.summary(), 201)

    async def erasure(request: Request, who: Principal) -> Response:
        data = await body(request)
        if not isinstance(data.get("user"), str) or not data["user"]:
            raise ValueError("'user' is required")
        tenant = str(data.get("tenant") or who.tenant)
        return JSONResponse({"erased": await manager.erase_subject(tenant, data["user"], who)})

    async def my_export(request: Request, who: Principal) -> Response:
        return JSONResponse(await manager.export_subject(who.tenant, who.user, who))

    async def export(request: Request, who: Principal) -> Response:
        data = await body(request)
        if not isinstance(data.get("user"), str) or not data["user"]:
            raise ValueError("'user' is required")
        tenant = str(data.get("tenant") or who.tenant)
        return JSONResponse(await manager.export_subject(tenant, data["user"], who))

    async def retention(request: Request, who: Principal) -> Response:
        policy = await asyncio.to_thread(load_lifecycle)
        if policy is None:
            return JSONResponse({"error": "no retention policy configured"}, 503)
        return JSONResponse({"pruned": await manager.apply_retention(policy, who)})

    async def releases(request: Request, who: Principal) -> Response:
        """Step 38: what serves, the candidate and its online-eval verdict (operators)."""
        if not set(who.roles) & {"platform-operator", RunManager.TAKEOVER_ROLE}:
            raise PermissionError("releases need the platform-operator or operator role")
        return JSONResponse({"releases": await manager.releases.report()})

    async def memories(request: Request, who: Principal) -> Response:
        agent = request.query_params.get("agent", "")
        return JSONResponse({"memories": await manager.memories(agent, who)})

    async def forget(request: Request, who: Principal) -> Response:
        await manager.forget(request.path_params["agent"], request.path_params["item_id"], who)
        return Response(status_code=204)

    async def flush(request: Request, who: Principal) -> Response:
        data = await body(request)
        if not isinstance(data.get("run_id"), str) or not isinstance(data.get("agent"), str):
            raise ValueError("'agent' and 'run_id' are required")
        flushed = await manager.flush_run(data["agent"], data["run_id"], who)
        return JSONResponse({"flushed": flushed})

    async def socket(ws: WebSocket) -> None:
        await ws.accept()
        try:
            hello = await ws.receive_json()
            who = await authenticate(str(hello.get("token") or ""))
            if who is None:
                await ws.close(code=4401, reason="a valid token is required")
                return
            try:
                source = await run_events(
                    ws.path_params["run_id"], who, int(hello.get("after", -1))
                )
            except KeyError:
                await ws.close(code=4404, reason="not found")
                return
            async for index, event in source:
                await ws.send_json({"id": index, **event})
            await ws.close(code=1000)
        except WebSocketDisconnect:
            return

    async def page(request: Request) -> Response:
        html = resources.files("agent_api").joinpath("static/index.html").read_text()
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; connect-src 'self'"
            },
        )

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await manager.recover()  # Step 18: runs survive a restart

        async def timers() -> None:  # Step 28: approval SLAs escalate, then expire
            while True:
                await asyncio.sleep(sweep_seconds)
                with contextlib.suppress(Exception):
                    await manager.sweep_approvals()

        async def leases() -> None:  # Step 38: keep this replica's runs, take over orphans
            while True:
                await asyncio.sleep(manager.LEASE_SECONDS / 3)
                with contextlib.suppress(Exception):
                    await manager.keep_leases()
                    await manager.adopt_orphans()

        tasks = [asyncio.create_task(timers())]
        if manager.node is not None:
            tasks.append(asyncio.create_task(leases()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/", page),
            Route("/healthz", health),
            Route("/v1/agents", guarded(agents)),
            Route("/v1/agents/{agent}/runs", guarded(create), methods=["POST"]),
            Route("/v1/runs/{run_id}", guarded(owned(state))),
            Route("/v1/runs/{run_id}/events", guarded(events)),
            Route("/v1/runs/{run_id}/approvals", guarded(owned(approvals)), methods=["POST"]),
            Route("/v1/approvals", guarded(inbox)),
            Route("/v1/reviews", guarded(reviews)),
            Route("/v1/reviews/{run_id}", guarded(review), methods=["POST"]),
            Route("/v1/runs/{run_id}/takeover", guarded(owned(takeover)), methods=["POST"]),
            Route("/v1/runs/{run_id}/resume", guarded(owned(resume)), methods=["POST"]),
            Route("/v1/runs/{run_id}/retry", guarded(owned(retry)), methods=["POST"]),
            Route("/v1/runs/{run_id}/cancel", guarded(owned(cancel)), methods=["POST"]),
            Route("/v1/threads", guarded(threads)),
            Route("/v1/threads/{thread_id}/checkpoints", guarded(checkpoints)),
            Route("/v1/threads/{thread_id}/fork", guarded(fork), methods=["POST"]),
            Route("/v1/admin/erasure", guarded(erasure), methods=["POST"]),
            Route("/v1/me/export", guarded(my_export)),
            Route("/v1/admin/export", guarded(export), methods=["POST"]),
            Route("/v1/admin/retention", guarded(retention), methods=["POST"]),
            Route("/v1/memories", guarded(memories)),
            Route("/v1/memories/{agent}/{item_id}", guarded(forget), methods=["DELETE"]),
            Route("/v1/admin/memory/flush", guarded(flush), methods=["POST"]),
            Route("/v1/admin/releases", guarded(releases)),
            WebSocketRoute("/v1/runs/{run_id}/ws", socket),
        ],
    )
