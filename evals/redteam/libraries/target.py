"""The run API as a red-team target for promptfoo and garak (Step 35).

Standard library only: promptfoo runs it with any Python, and garak runs it inside its own
isolated environment. Each prompt is one real run of a real agent through the platform (Keycloak
token, OPA, guardrails, approvals, audit), so the attack libraries test the whole platform with
whatever model the gateway serves: the stand-in's canned answers in CI (mechanics only), the
local Qwen models on the Mac (real susceptibility).

Configuration (environment):

* ``AGENT_REDTEAM_API``     run API base URL (default ``http://127.0.0.1:8091``)
* ``AGENT_REDTEAM_AGENT``   agent to attack (default ``support-handoff``)
* ``AGENT_REDTEAM_INPUT``   JSON input template; ``{prompt}`` is replaced by the attack text,
  JSON-escaped (default ``{"task": "{prompt}"}``)
* ``AGENT_REDTEAM_TOKEN``   a bearer token, or else a password grant with
  ``P0_KEYCLOAK_URL`` / ``P0_KEYCLOAK_REALM``, ``AGENT_REDTEAM_CLIENT`` (default
  ``mcp-inspector``), ``AGENT_REDTEAM_USER`` and ``AGENT_REDTEAM_PASSWORD``
* ``AGENT_REDTEAM_TIMEOUT`` seconds to wait for one run (default 120)

The reply is the run's output text; a run that stops is described in brackets so detectors see
what the platform did: ``[approval requested: post_payment {...}]``, ``[run failed: ...]``,
``[run stopped: ...]``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

_TOKEN: dict[str, Any] = {}
TERMINAL = {"succeeded", "failed", "stopped", "cancelled", "interrupted"}


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"red-team target: set {name}")
    return value


def _http(method: str, url: str, body: Any = None, headers: dict[str, str] | None = None) -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})  # noqa: S310
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - configured URL
        return bytes(resp.read())


def token() -> str:
    if os.environ.get("AGENT_REDTEAM_TOKEN"):
        return os.environ["AGENT_REDTEAM_TOKEN"]
    if _TOKEN.get("expires", 0) > time.time() + 30:
        return str(_TOKEN["value"])
    kc = _env("P0_KEYCLOAK_URL", "http://127.0.0.1:8080").rstrip("/")
    realm = _env("P0_KEYCLOAK_REALM", "rag-platform")
    form = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": _env("AGENT_REDTEAM_CLIENT", "mcp-inspector"),
            "username": _env("AGENT_REDTEAM_USER"),
            "password": _env("AGENT_REDTEAM_PASSWORD"),
        }
    ).encode()
    req = urllib.request.Request(  # noqa: S310
        f"{kc}/realms/{realm}/protocol/openid-connect/token", data=form, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        doc = json.loads(resp.read())
    _TOKEN.update(value=doc["access_token"], expires=time.time() + int(doc.get("expires_in", 60)))
    return str(doc["access_token"])


def inputs(prompt: str) -> dict[str, Any]:
    template = _env("AGENT_REDTEAM_INPUT", '{"task": "{prompt}"}')
    escaped = json.dumps(prompt)[1:-1]
    return dict(json.loads(template.replace("{prompt}", escaped)))


def _events(api: str, run_id: str, auth: dict[str, str]) -> list[dict[str, Any]]:
    raw = _http("GET", f"{api}/v1/runs/{run_id}/events?after=0", headers=auth).decode()
    out = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                continue
    return out


def describe(status: str, run: dict[str, Any], events: list[dict[str, Any]]) -> str:
    if status == "interrupted":
        asked = []
        for item in run.get("interrupts") or []:
            action = item.get("action") or {}
            for call in action.get("calls") or [action]:
                if call.get("tool"):
                    asked.append(f"{call['tool']} {json.dumps(call.get('arguments') or {})}")
        return f"[approval requested: {'; '.join(asked) or 'unknown action'}]"
    finished = next((e for e in reversed(events) if e.get("type") == "RUN_FINISHED"), None)
    if status == "succeeded" and finished is not None:
        output = (finished.get("result") or {}).get("output")
        if isinstance(output, dict) and "answer" in output:
            output = output["answer"]
        return output if isinstance(output, str) else json.dumps(output, default=str)
    error = next((e.get("message") for e in events if e.get("type") == "RUN_ERROR"), "")
    output = ((finished or {}).get("result") or {}).get("output")
    if not error and isinstance(output, dict):
        rounds = (output.get("partial") or {}).get("rounds") or []
        last = rounds[-1].get("outcome") if rounds else ""
        error = "; ".join(str(x) for x in (output.get("stopped"), last) if x)
    return f"[run {status}: {error}]"


def run(prompt: str) -> str:
    api = _env("AGENT_REDTEAM_API", "http://127.0.0.1:8091").rstrip("/")
    agent = _env("AGENT_REDTEAM_AGENT", "support-handoff")
    auth = {"Authorization": f"Bearer {token()}"}
    created = json.loads(
        _http("POST", f"{api}/v1/agents/{agent}/runs", {"input": inputs(prompt)}, auth)
    )
    run_id = created["run_id"]
    deadline = time.time() + float(_env("AGENT_REDTEAM_TIMEOUT", "120"))
    state: dict[str, Any] = created
    while time.time() < deadline:
        state = json.loads(_http("GET", f"{api}/v1/runs/{run_id}", headers=auth))
        if state.get("status") in TERMINAL:
            break
        time.sleep(0.5)
    else:
        return f"[run timed out after {_env('AGENT_REDTEAM_TIMEOUT', '120')}s]"
    return describe(str(state["status"]), state, _events(api, run_id, auth))


# ---------------------------------------------------------------- promptfoo (python provider)


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"output": run(prompt)}
    except Exception as exc:  # promptfoo records the error against the test case
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------- garak (function generator)


def generate(prompt: Any, **kwargs: Any) -> list[str]:
    text = prompt if isinstance(prompt, str) else str(getattr(prompt, "text", prompt))
    return [run(text)]
