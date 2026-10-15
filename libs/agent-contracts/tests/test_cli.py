import pytest
from agent_contracts.registry import main


def test_cli_lists_all_slots(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "26 of 26 slots" in out
    assert "tool_executor" in out
