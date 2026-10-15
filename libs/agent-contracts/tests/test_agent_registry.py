"""The SQLite agent registry keeps records and history across restarts (local ``just agents``)."""

from pathlib import Path

from agent_contracts.dummies.registry import SqliteAgentRegistry
from agent_contracts.models import AgentRecord, AgentStatus


async def test_records_and_history_survive_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "agents.db")
    reg = SqliteAgentRegistry(path)
    await reg.register(AgentRecord(name="triage", version="1.0.0", owner="team-a", spec_hash="h"))
    await reg.set_status("triage", "1.0.0", AgentStatus.APPROVED, by="carol", reason="ok")
    again = SqliteAgentRegistry(path)
    assert (await again.get("triage")).status is AgentStatus.APPROVED
    assert [h.to_status for h in await again.history("triage", "1.0.0")] == [
        AgentStatus.DRAFT,
        AgentStatus.APPROVED,
    ]
