"""Step 30: one multi-agent run through the run API is one trace: the supervisor's
``invoke_agent`` span, each worker's ``invoke_agent`` span under it, and every ``chat`` and
``execute_tool`` span under the agent that made the call."""

import importlib.util
from pathlib import Path

from agent_telemetry import semconv as S  # noqa: N812
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_spec = importlib.util.spec_from_file_location(
    "registry_gate", Path(__file__).parent / "test_registry_gate.py"
)
assert _spec
assert _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


async def test_a_team_run_is_one_trace_with_genai_spans(
    span_exporter: InMemorySpanExporter,
) -> None:
    manager, app, _, _ = await gate.world("dev", [])
    resp = await gate.start(app, "research-report")
    run = manager.runs[resp.json()["run_id"]]
    await manager.wait_idle(run)
    assert run.status == "succeeded", run.events[-1]

    spans = span_exporter.get_finished_spans()
    assert len({s.context.trace_id for s in spans}) == 1, "the whole team run is one trace"
    by_id = {s.context.span_id: s for s in spans}
    agents = [s for s in spans if s.name.startswith("invoke_agent ")]
    root = next(s for s in agents if s.name == "invoke_agent research-report")
    assert root.parent is None
    assert root.attributes[S.RUN_ID] == run.run_id
    assert root.attributes[S.RUN_STATUS] == "succeeded"
    workers = {s.attributes[S.AGENT_NAME] for s in agents} - {"research-report"}
    assert workers == {
        "research-report-researcher",
        "research-report-analyst",
        "research-report-writer",
    }

    def agent_of(span):  # the nearest invoke_agent ancestor
        while span.parent is not None:
            span = by_id[span.parent.span_id]
            if span.name.startswith("invoke_agent "):
                return span.attributes[S.AGENT_NAME]
        return None

    for worker in (s for s in agents if s is not root):
        assert agent_of(worker) == "research-report"
        assert worker.attributes[S.PARENT_RUN_ID] == run.run_id
    chats = [s for s in spans if s.attributes.get(S.OPERATION) == S.CHAT]
    tools = [s for s in spans if s.name.startswith("execute_tool ")]
    assert chats
    assert tools
    for span in chats + tools:
        # each call belongs to the agent that made it
        assert agent_of(span) == span.attributes[S.AGENT_NAME]
    assert {s.attributes[S.TOOL_NAME] for s in tools} >= {"search"}
    for span in spans:
        assert not set(span.attributes) & S.CONTENT_ATTRIBUTES  # no content without Langfuse


async def test_content_redactor_removes_secrets_and_withholds_without_pii_check() -> None:
    from agent_api.telemetry import content_redactor
    from agent_guardrails.pii import PresidioPiiGuardrail

    redact = content_redactor()
    text = await redact("key AKIAABCDEFGHIJKLMNOP leaked")  # secret-scan: allow
    assert text is not None
    assert "AKIAABCDEFGHIJKLMNOP" not in text  # secret-scan: allow

    down = PresidioPiiGuardrail(
        analyzer_url="http://127.0.0.1:1", anonymizer_url="http://127.0.0.1:1", timeout=0.5
    )
    assert await content_redactor(down)("call me on 0400 000 000") is None  # withheld
