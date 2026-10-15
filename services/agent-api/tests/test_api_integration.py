"""Step 17 "Done when", against the stand-in stack: the run API with Keycloak-verified tokens and
the real OPA policy runs the invoice-triage agent (demo ERP over MCP), and the minimal web page
shows the live tool calls and an approval button using only AG-UI events (headless Chromium)."""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
KC = os.environ.get("P0_KEYCLOAK_URL", "http://127.0.0.1:8080")
REALM = os.environ.get("P0_KEYCLOAK_REALM", "rag-platform")
ISSUER = f"{KC}/realms/{REALM}"
INVOICE = {
    "id": "INV-1001",
    "supplier": "Acme Office Supplies Pty Ltd",
    "subtotal": 480.0,
    "gst": 48.0,
    "total": 528.0,
    "currency": "AUD",
    "po_number": "PO-7781",
}


def token(username: str) -> str:
    resp = httpx.post(
        f"{ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "mcp-inspector",
            "username": username,
            "password": "standin-only",
        },
    )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    work = tmp_path_factory.mktemp("agent-api")
    agents = work / "agents" / "invoice-triage"
    agents.mkdir(parents=True)
    spec = (ROOT / "agents/invoice-triage/agent.yaml").read_text()
    (agents / "agent.yaml").write_text(spec.replace("durability: checkpoint", "durability: memory"))
    platform = yaml.safe_load((ROOT / "platform.yaml").read_text())
    erp_data = work / "erp.json"
    platform["mcp"]["servers"].append(
        {
            "name": "erp",
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "invoice_triage.erp_server"],
            "env": {"ERP_DATA": str(erp_data)},
            "side_effects": {"get_invoice": "read", "post_payment": "irreversible"},
            "allowed_tools": ["get_invoice", "post_payment"],
        }
    )
    (work / "platform.yaml").write_text(yaml.safe_dump(platform))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    env = {
        **os.environ,
        "AGENT_API_PORT": str(port),
        "AGENT_API_AGENTS_DIR": str(work / "agents"),
        "AGENT_API_AUDIT_FILE": str(work / "audit.jsonl"),
        "AGENT_PLATFORM_CONFIG": str(work / "platform.yaml"),
    }
    proc = subprocess.Popen([sys.executable, "-m", "agent_api"], env=env, cwd=ROOT)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                httpx.get(f"{url}/healthz", timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.2)
        yield url, erp_data
    finally:
        proc.terminate()
        proc.wait(10)


def events(url: str, run_id: str, who: str, after: int = -1) -> list[tuple[int, dict[str, Any]]]:
    resp = httpx.get(
        f"{url}/v1/runs/{run_id}/events",
        params={"after": after},
        headers={"Authorization": f"Bearer {token(who)}"},
        timeout=60,
    )
    resp.raise_for_status()
    out = []
    for frame in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        out.append((int(lines["id"]), json.loads(lines["data"])))
    return out


def test_a_run_pauses_for_approval_and_pays_after_a_verified_approver_decides(
    api: tuple[str, Path],
) -> None:
    url, erp = api
    created = httpx.post(
        f"{url}/v1/agents/invoice-triage/runs",
        json={"input": {"invoice": INVOICE}},
        headers={"Authorization": f"Bearer {token('alice')}", "Idempotency-Key": "it-1"},
        timeout=60,
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    first = events(url, run_id, "alice")
    interrupt = first[-1][1]["outcome"]["interrupts"][0]
    assert interrupt["metadata"]["action"]["arguments"] == {
        "invoice_id": "INV-1001",
        "amount_aud": 528.0,
        "currency": "AUD",
    }
    denied = httpx.post(
        f"{url}/v1/runs/{run_id}/approvals",
        json={"approved": True},
        headers={"Authorization": f"Bearer {token('alice')}"},
    )
    assert denied.status_code == 403
    approved = httpx.post(
        f"{url}/v1/runs/{run_id}/approvals",
        json={"approved": True},
        headers={"Authorization": f"Bearer {token('jo')}"},
    )
    assert approved.status_code == 202, approved.text
    rest = events(url, run_id, "alice", after=first[-1][0])
    result = next(e for _, e in rest if e["type"] == "TOOL_CALL_RESULT")
    assert json.loads(result["content"])["invoice_id"] == "INV-1001"
    assert rest[-1][1]["outcome"]["type"] == "success"
    assert [p["invoice_id"] for p in json.loads(erp.read_text())["payments"]] == ["INV-1001"]


def _chromium() -> str | None:
    # this container's preinstalled Chromium, or the CI runner's Google Chrome
    found = (
        os.environ.get("AGENT_CHROMIUM")
        or shutil.which("chromium")
        or shutil.which("google-chrome")
    )
    return found or (
        "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None
    )


def test_the_page_shows_live_tool_calls_and_an_approval_button(api: tuple[str, Path]) -> None:
    from playwright.sync_api import expect, sync_playwright

    url, erp = api
    invoice = {**INVOICE, "id": "INV-1002", "gst": 48.0, "total": 528.0, "po_number": "PO-7790"}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chromium())
        page = browser.new_page()
        page.goto(url)
        page.fill("#token", token("alice"))
        page.click("#load")
        page.wait_for_selector("#agent option", state="attached")
        page.fill("#input", json.dumps({"invoice": invoice}))
        page.click("#start")
        page.wait_for_selector("#approval", state="visible", timeout=30_000)
        assert "post_payment" in page.inner_text("#approval-detail")
        shots = os.environ.get("AGENT_SCREENSHOTS")  # optional: where to save screenshots
        if shots:
            page.screenshot(path=f"{shots}/agent-api-approval.png", full_page=True)
        page.fill("#approver-token", token("jo"))
        page.click("#approve")
        expect(page.locator("#status")).to_have_text("succeeded", timeout=30_000)
        tools = page.inner_text("#tools")
        if shots:
            page.screenshot(path=f"{shots}/agent-api-done.png", full_page=True)
        browser.close()
    assert "erp__post_payment" in tools
    assert "INV-1002" in tools
    assert "INV-1002" in [p["invoice_id"] for p in json.loads(erp.read_text())["payments"]]
