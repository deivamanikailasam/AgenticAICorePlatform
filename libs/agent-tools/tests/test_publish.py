"""``local://platform/publish_report`` (Step 40): irreversible, external, and stored."""

import asyncio

from agent_contracts.models import RunContext, SideEffect
from agent_tools.artefacts import MemoryArtefactStore
from agent_tools.publish import PublishReportTool

CTX = RunContext(tenant_id="acme", user_id="alice", agent_id="research-report", run_id="r1")


def test_publishing_is_irreversible_external_and_stored() -> None:
    store = MemoryArtefactStore()
    tool = PublishReportTool(store)
    assert tool.spec.side_effect is SideEffect.IRREVERSIBLE  # policy and the spec need approval
    assert "external" in tool.spec.tags
    assert not tool.spec.idempotent  # never retried
    out = asyncio.run(tool.invoke({"title": "Solar", "report": "Rooftop solar grew 18%."}, CTX))
    assert out == {
        "published": "memory://reports/acme/r1.json",
        "report": "Rooftop solar grew 18%.",
    }
    assert b"Rooftop solar grew 18%." in store.objects["reports/acme/r1.json"]
