"""TEST DOUBLE of Project 0's rag-core search API — CI and cloud sessions only (ADR 0011).

It implements only the *contract* the platform relies on (an audience-restricted bearer token in,
permission-filtered passages with citations out), so the gateway's on-behalf-of path can be
tested. It is not a retrieval system and is never deployed.

Usage: python -m tests.stack.fakes.rag_core_fake <port> <issuer>
"""

import sys
from typing import Any

import jwt
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

DOCS: list[dict[str, Any]] = [
    {
        "id": "fin-001",
        "title": "Refund policy",
        "snippet": "Refunds over $500 need finance approval.",
        "groups": ["/acme/finance"],
    },
    {
        "id": "fin-002",
        "title": "Q3 forecast",
        "snippet": "Q3 revenue forecast and refund trends.",
        "groups": ["/acme/finance"],
    },
    {
        "id": "eng-001",
        "title": "On-call runbook",
        "snippet": "Refund service restarts are safe.",
        "groups": ["/acme/engineering"],
    },
    {
        "id": "glx-001",
        "title": "Globex refunds",
        "snippet": "Globex refund terms (another tenant).",
        "groups": ["/globex/finance"],
    },
]

port, issuer = int(sys.argv[1]), sys.argv[2].rstrip("/")
jwks = jwt.PyJWKClient(f"{issuer}/protocol/openid-connect/certs")


async def search(request: Request) -> JSONResponse:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"error": "missing token"}, 401)
    token = auth.removeprefix("Bearer ")
    try:
        key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=issuer, audience="rag-core")
    except jwt.PyJWTError as exc:
        return JSONResponse({"error": f"invalid token: {exc}"}, 401)
    body = await request.json()
    groups = set(claims.get("groups", []))
    words = set(str(body.get("query", "")).lower().split())
    hits = [
        d
        for d in DOCS
        if groups & set(d["groups"])
        and words & set((d["title"] + " " + d["snippet"]).lower().replace(".", "").split())
    ]
    results = [
        {
            "id": d["id"],
            "title": d["title"],
            "snippet": d["snippet"],
            "score": 1.0,
            "source": "fake",
            "citation": {"doc_id": d["id"], "title": d["title"]},
        }
        for d in hits[: int(body.get("k", 5))]
    ]
    return JSONResponse({"results": results, "user": claims.get("preferred_username")})


app = Starlette(routes=[Route("/v1/search", search, methods=["POST"])])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
