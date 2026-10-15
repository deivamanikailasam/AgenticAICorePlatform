from pathlib import Path

import pytest
from agent_spec import SpecError
from agent_spec.platform import ROLES, load_platform

ROOT = Path(__file__).resolve().parents[3]


def test_repo_platform_yaml_loads_with_every_role() -> None:
    cfg = load_platform(ROOT / "platform.yaml", env={})
    assert set(ROLES) <= set(cfg.models.roles)
    assert cfg.gateway.url == "http://127.0.0.1:4000"
    assert cfg.alias_for("planner") == "local-qwen-quality"
    assert cfg.alias_for("planner", {"planner": "cloud-x"}) == "cloud-x"
    assert cfg.alias_for("some-alias") == "some-alias"
    assert cfg.capabilities("local-qwen-fast").tool_calling
    assert not cfg.capabilities("unknown").tool_calling
    assert cfg.currency.usd_to_aud > 1


def test_env_overrides_gateway_url() -> None:
    cfg = load_platform(ROOT / "platform.yaml", env={"P0_LITELLM_URL": "http://litellm:4000"})
    assert cfg.gateway.url == "http://litellm:4000"


def test_missing_role_is_reported(tmp_path: Path) -> None:
    text = (ROOT / "platform.yaml").read_text().replace("    judge: local-qwen-quality\n", "")
    f = tmp_path / "platform.yaml"
    f.write_text(text)
    with pytest.raises(SpecError, match="missing roles: judge"):
        load_platform(f, env={})
