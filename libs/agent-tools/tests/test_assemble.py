from agent_contracts.models import ExecutablePlan, NodeKind, PlanNode
from agent_tools.assemble import grants_for, registry_name, tool_names


def test_registry_names() -> None:
    assert registry_name("mcp://erp/post_payment") == "erp__post_payment"
    assert registry_name("local://platform/calculator") == "calculator"
    assert registry_name("mcp://rag-core/search") == "search"
    assert registry_name("sandbox://python") == "python"


def test_names_and_grants_from_plan_metadata() -> None:
    plan = ExecutablePlan(
        name="p",
        nodes=(PlanNode(id="a", kind=NodeKind.JOIN),),
        entry="a",
        exits=("a",),
        metadata={
            "tools": [
                {
                    "name": "post_payment",
                    "ref": "mcp://erp/post_payment",
                    "approval": True,
                    "approvers": "group:finance-approvers",
                    "rate_limit_per_minute": 5,
                },
                {"name": "count", "ref": "local://platform/word_count", "approval": False},
            ]
        },
    )
    assert tool_names(plan) == {"post_payment": "erp__post_payment", "count": "word_count"}
    grants = grants_for(plan)
    assert set(grants) == {"erp__post_payment", "word_count"}
    assert grants["erp__post_payment"].rate_limit_per_minute == 5
    assert not grants["erp__post_payment"].approval_required, "the approval node enforces it"
