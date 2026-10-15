import pytest
from agent_contracts.registry import main


def test_cli_lists_all_slots(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "24 of 24 slots" in out
    assert "tool_executor" in out
