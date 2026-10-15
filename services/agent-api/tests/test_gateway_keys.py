"""Step 29: the LiteLLM role keys come from OpenBao; in staging and prod never from env."""

from agent_tools.secrets import EnvSecretResolver, SecretResolver


async def test_gateway_keys_come_from_openbao_and_never_from_env_in_prod() -> None:
    from agent_api.assembly import load_gateway_keys
    from agent_spec.platform import load_platform

    class Keys(SecretResolver):
        async def resolve(self, reference: str) -> str:
            if "litellm/planner#" in reference:
                return "sk-planner"
            raise KeyError(reference)

    platform = load_platform()
    assert await load_gateway_keys(platform, Keys(), "prod") == {"planner": "sk-planner"}
    assert await load_gateway_keys(platform, EnvSecretResolver(), "dev") is None
    assert await load_gateway_keys(platform, EnvSecretResolver(), "prod") == {}
