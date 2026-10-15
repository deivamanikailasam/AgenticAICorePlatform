"""The OpenTelemetry GenAI semantic conventions this platform follows (Step 30, ADR 0010).

The conventions are in Development status and moved to their own repository
(``open-telemetry/semantic-conventions-genai``), which has no release yet. They are pinned to
the commit read on 2026-10-15; changing the pin is a reviewed change.
"""

from __future__ import annotations

SEMCONV_REPOSITORY = "https://github.com/open-telemetry/semantic-conventions-genai"
SEMCONV_COMMIT = "f3c6d0c9bb66ebd474668a3a0be25c61226f02c2"

# operations (``gen_ai.operation.name``)
INVOKE_AGENT = "invoke_agent"
CHAT = "chat"
EXECUTE_TOOL = "execute_tool"

OPERATION = "gen_ai.operation.name"
PROVIDER = "gen_ai.provider.name"
AGENT_NAME = "gen_ai.agent.name"
AGENT_ID = "gen_ai.agent.id"
AGENT_VERSION = "gen_ai.agent.version"
AGENT_DESCRIPTION = "gen_ai.agent.description"
CONVERSATION_ID = "gen_ai.conversation.id"
REQUEST_MODEL = "gen_ai.request.model"
REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
REQUEST_TEMPERATURE = "gen_ai.request.temperature"
RESPONSE_MODEL = "gen_ai.response.model"
FINISH_REASONS = "gen_ai.response.finish_reasons"
INPUT_TOKENS = "gen_ai.usage.input_tokens"
OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
TOOL_NAME = "gen_ai.tool.name"
TOOL_CALL_ID = "gen_ai.tool.call.id"
TOOL_TYPE = "gen_ai.tool.type"
TOOL_DESCRIPTION = "gen_ai.tool.description"
ERROR_TYPE = "error.type"

# Opt-in content attributes: the prompt, the response, instructions, tool arguments and results.
# They go to Langfuse only (redacted), never to Tempo or logs.
INPUT_MESSAGES = "gen_ai.input.messages"
OUTPUT_MESSAGES = "gen_ai.output.messages"
SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"
TOOL_DEFINITIONS = "gen_ai.tool.definitions"
TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"
TOOL_CALL_RESULT = "gen_ai.tool.call.result"
CONTENT_ATTRIBUTES = frozenset(
    {
        INPUT_MESSAGES,
        OUTPUT_MESSAGES,
        SYSTEM_INSTRUCTIONS,
        TOOL_DEFINITIONS,
        TOOL_CALL_ARGUMENTS,
        TOOL_CALL_RESULT,
    }
)

# The gateway in front of every model: a custom provider name (no well-known value fits a
# gateway that serves several providers).
PROVIDER_LITELLM = "litellm"

# platform attributes
RUN_ID = "agent_platform.run.id"
PARENT_RUN_ID = "agent_platform.run.parent_id"
TENANT_ID = "agent_platform.tenant.id"
RISK_TIER = "agent_platform.risk_tier"
RUN_STATUS = "agent_platform.run.status"
STOP_REASON = "agent_platform.run.stop_reason"
COST_AUD = "agent_platform.cost.aud"
TOOL_OK = "agent_platform.tool.ok"
TOOL_ERROR_CODE = "agent_platform.tool.error_code"
TOOL_REF = "agent_platform.tool.ref"
