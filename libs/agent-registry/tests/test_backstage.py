"""The Backstage catalogue as the agent registry: contract, entity shape, reload."""

from pathlib import Path

import pytest
import yaml
from agent_contracts.models import AgentRecord, AgentStatus
from agent_contracts.testing import AgentRegistryContract
from agent_registry.backstage import ANNOTATION, BackstageCatalogRegistry, location


class TestBackstageCatalogRegistry(AgentRegistryContract):
    @pytest.fixture(autouse=True)
    def _dir(self, tmp_path: Path) -> None:
        self.dir = tmp_path / "catalog"

    def make(self) -> BackstageCatalogRegistry:
        return BackstageCatalogRegistry(str(self.dir))


async def test_entities_show_lifecycle_and_reload(tmp_path: Path) -> None:
    reg = BackstageCatalogRegistry(str(tmp_path))
    await reg.register(
        AgentRecord(name="triage", version="1.0.0", owner="team-a", spec_hash="h", agents=("tech",))
    )
    await reg.set_status("triage", "1.0.0", AgentStatus.APPROVED, by="carol")
    doc = yaml.safe_load((tmp_path / "triage" / "1.0.0.yaml").read_text())
    assert doc["kind"] == "Component"
    assert doc["metadata"]["name"] == "triage-1.0.0"
    assert doc["spec"] == {
        "type": "ai-agent",
        "lifecycle": "production",
        "owner": "team-a",
        "system": "agent-platform",
        "dependsOn": ["component:tech"],
    }
    assert ANNOTATION in doc["metadata"]["annotations"]
    again = BackstageCatalogRegistry(str(tmp_path))
    assert (await again.get("triage")).status is AgentStatus.APPROVED
    assert len(await again.history("triage", "1.0.0")) == 2
    assert "./catalog/agents/*/*.yaml" in location()
