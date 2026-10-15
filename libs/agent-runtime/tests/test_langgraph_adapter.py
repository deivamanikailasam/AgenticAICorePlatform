from typing import Any

from agent_contracts.testing import WORKFLOW_FUNCTIONS, WorkflowRuntimeContract
from agent_runtime.adapters.langgraph import LangGraphAdapter


class TestLangGraphAdapter(WorkflowRuntimeContract):
    def make(self, functions: dict[str, Any] | None = None) -> LangGraphAdapter:
        return LangGraphAdapter(functions={**WORKFLOW_FUNCTIONS, **(functions or {})})
